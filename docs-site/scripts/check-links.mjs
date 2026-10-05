#!/usr/bin/env node
// Post-build link checker for docs-site/dist.
//
//   internal links, assets, demo iframe  -> verified on disk, FATAL (exit 1)
//   #hash targets on built pages         -> verified against element ids, FATAL
//   external links                       -> HEAD-checked, WARNINGS only
//   github.com/<this repo>/blob|tree/... -> verified via HEAD on raw paths
//
// Usage: node scripts/check-links.mjs [--dist <dir>] [--no-external]
// Env:   CHECK_EXTERNAL=0 also disables external checks.
// External checks are skipped automatically when offline.

import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs';
import { join, resolve, dirname, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const argVal = (name) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
};
const DIST = resolve(argVal('--dist') ?? join(ROOT, 'dist'));
const CHECK_EXTERNAL = !args.includes('--no-external') && process.env.CHECK_EXTERNAL !== '0';

const SITE = 'https://stevengonsalvez.github.io';
const BASE = '/ainb-reflect-memory/';
const REPO = 'stevengonsalvez/ainb-reflect-memory';
const RAW = `https://raw.githubusercontent.com/${REPO}`;

// Politeness limits for external checks.
const EXTERNAL_MAX = 150;
const EXTERNAL_CONCURRENCY = 4;
const PER_HOST_DELAY_MS = 250;
const TIMEOUT_MS = 8000;

if (!existsSync(DIST)) {
  console.error(`check-links: dist not found at ${DIST}. Run "npm run build" first.`);
  process.exit(2);
}

// ---- collect html files ----------------------------------------------------
function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (name.endsWith('.html')) out.push(p);
  }
  return out;
}
const pages = walk(DIST);

// ---- url helpers -------------------------------------------------------------
const toPosix = (p) => p.split(sep).join('/');

/** Public URL path of an html file in dist, e.g. /ainb-reflect-memory/start/overview/ */
function pageUrlPath(file) {
  const rel = toPosix(relative(DIST, file));
  const noIndex = rel.endsWith('index.html') ? rel.slice(0, -'index.html'.length) : rel;
  return BASE + noIndex;
}

/** Map a base-prefixed URL path to a file in dist, or null. */
function resolveToFile(urlPath) {
  if (!urlPath.startsWith(BASE)) return null;
  let rel;
  try {
    rel = decodeURIComponent(urlPath.slice(BASE.length));
  } catch {
    return null;
  }
  const abs = resolve(DIST, rel);
  if (abs !== DIST && !abs.startsWith(DIST + sep)) return null; // traversal guard
  const candidates = rel === '' || rel.endsWith('/')
    ? [join(abs, 'index.html')]
    : [abs, abs + '.html', join(abs, 'index.html')];
  for (const c of candidates) {
    if (existsSync(c) && statSync(c).isFile()) return c;
  }
  return null;
}

const idCache = new Map();
function idsOf(file) {
  if (!idCache.has(file)) {
    const html = readFileSync(file, 'utf8');
    const ids = new Set();
    for (const m of html.matchAll(/\s(?:id|name)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) ids.add(m[1] ?? m[2]);
    idCache.set(file, ids);
  }
  return idCache.get(file);
}

/** Extract every url-bearing attribute value from an html string. */
function extractRefs(html) {
  const refs = [];
  const attr = /\s(href|src|poster|data-src|srcset)\s*=\s*(?:"([^"]*)"|'([^']*)')/gi;
  for (const m of html.matchAll(attr)) {
    const name = m[1].toLowerCase();
    const val = (m[2] ?? m[3] ?? '').trim();
    if (!val) continue;
    if (name === 'srcset') {
      for (const part of val.split(',')) {
        const u = part.trim().split(/\s+/)[0];
        if (u) refs.push({ attr: name, url: u });
      }
    } else {
      refs.push({ attr: name, url: val });
    }
  }
  return refs;
}

const decodeEntities = (s) =>
  s.replace(/&amp;/g, '&').replace(/&#38;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'");

// ---- run ---------------------------------------------------------------------
const errors = [];
const warnings = [];
const external = new Map(); // url -> first referring page
let internalCount = 0;

for (const file of pages) {
  const pageUrl = pageUrlPath(file);
  const pageRel = toPosix(relative(DIST, file));
  const html = readFileSync(file, 'utf8');
  const isDemo = pageRel.startsWith('demo/');

  for (const { attr, url: rawUrl } of extractRefs(html)) {
    const raw = decodeEntities(rawUrl);
    if (/^(mailto:|tel:|data:|javascript:|blob:|about:)/i.test(raw)) continue;

    let u;
    try {
      u = new URL(raw, SITE + pageUrl);
    } catch {
      errors.push(`${pageRel}: unparseable ${attr}="${raw}"`);
      continue;
    }

    // Absolute links to the site itself count as internal.
    const isExternal = u.origin !== SITE;
    if (isExternal) {
      if (/^https?:$/.test(u.protocol)) {
        u.hash = '';
        if (!external.has(u.href)) external.set(u.href, pageRel);
      }
      continue;
    }

    // Starlight's 404.html canonical points at /404/, which Astro emits as 404.html.
    if (pageRel === '404.html' && u.pathname === BASE + '404/') continue;

    internalCount++;
    // Root-relative links that skip the base path are always a bug on a project site.
    if (raw.startsWith('/') && !raw.startsWith('//') && !u.pathname.startsWith(BASE)) {
      errors.push(`${pageRel}: ${attr}="${raw}" is missing the base path ${BASE}`);
      continue;
    }
    const target = resolveToFile(u.pathname);
    if (!target) {
      errors.push(`${pageRel}: broken ${attr}="${raw}" (no such file under dist)`);
      continue;
    }
    if (u.hash.length > 1 && target.endsWith('.html')) {
      let id;
      try {
        id = decodeURIComponent(u.hash.slice(1));
      } catch {
        id = u.hash.slice(1);
      }
      if (id !== 'top' && !idsOf(target).has(id)) {
        const msg = `${pageRel}: ${attr}="${raw}" anchor #${id} not found in ${toPosix(relative(DIST, target))}`;
        // Demo bundles may build anchors client-side; do not fail the build on them.
        (isDemo || toPosix(relative(DIST, target)).startsWith('demo/') ? warnings : errors).push(msg);
      }
    }
  }
}

// ---- external checks (warnings only) -----------------------------------------
async function online() {
  try {
    const ctl = AbortSignal.timeout(4000);
    await fetch('https://github.com', { method: 'HEAD', signal: ctl });
    return true;
  } catch {
    return false;
  }
}

/** Map this repo's github blob/tree urls to a raw URL we can HEAD cheaply. */
function toProbeUrl(href) {
  const m = href.match(new RegExp(`^https://github\\.com/${REPO}/(?:blob|tree)/([^/]+)/(.+)$`));
  if (m && !href.includes('/tree/')) return `${RAW}/${m[1]}/${m[2]}`;
  return href;
}

const lastHit = new Map();
async function politeWait(host) {
  const wait = (lastHit.get(host) ?? 0) + PER_HOST_DELAY_MS - Date.now();
  lastHit.set(host, Math.max(Date.now(), lastHit.get(host) ?? 0) + PER_HOST_DELAY_MS);
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
}

async function probe(href) {
  const url = toProbeUrl(href);
  await politeWait(new URL(url).host);
  const opts = { redirect: 'follow', signal: AbortSignal.timeout(TIMEOUT_MS), headers: { 'user-agent': 'reflect-docs-link-check' } };
  try {
    let res = await fetch(url, { ...opts, method: 'HEAD' });
    if (res.status === 405 || res.status === 403 || res.status === 501) {
      res = await fetch(url, { ...opts, method: 'GET' });
    }
    return res.ok ? null : `HTTP ${res.status}`;
  } catch (e) {
    return `request failed (${e.name === 'TimeoutError' ? 'timeout' : e.message})`;
  }
}

let externalChecked = 0;
let externalNote = '';
if (!CHECK_EXTERNAL) {
  externalNote = 'external checks disabled';
} else if (external.size === 0) {
  externalNote = 'no external links';
} else if (!(await online())) {
  externalNote = 'offline, external checks skipped';
} else {
  // Own-repo links first, then the rest, capped.
  const all = [...external.entries()].sort(([a], [b]) => Number(b.includes(REPO)) - Number(a.includes(REPO)));
  const queue = all.slice(0, EXTERNAL_MAX);
  if (all.length > EXTERNAL_MAX) externalNote = `checked first ${EXTERNAL_MAX} of ${all.length} external links`;
  let i = 0;
  await Promise.all(
    Array.from({ length: EXTERNAL_CONCURRENCY }, async () => {
      while (i < queue.length) {
        const [href, from] = queue[i++];
        const problem = await probe(href);
        externalChecked++;
        if (problem) warnings.push(`${from}: external ${href} -> ${problem}`);
      }
    }),
  );
}

// ---- report ------------------------------------------------------------------
console.log(
  `check-links: ${pages.length} pages, ${internalCount} internal refs, ${externalChecked}/${external.size} external checked` +
    (externalNote ? ` (${externalNote})` : ''),
);
for (const w of warnings) console.warn(`WARN  ${w}`);
for (const e of errors) console.error(`ERROR ${e}`);

if (errors.length) {
  console.error(`check-links: ${errors.length} internal problem(s), ${warnings.length} warning(s)`);
  process.exit(1);
}
console.log(`check-links: OK (${warnings.length} warning(s))`);
