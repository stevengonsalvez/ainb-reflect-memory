#!/usr/bin/env node
/*
 * Build the in-browser memory-browser demo into docs-site/public/demo/ (gitignored).
 *
 *   node docs-site/scripts/build-demo.mjs        (also run by `npm run build` / `npm run dev`)
 *
 * Takes the REAL `reflect serve` frontend (src/reflect_kb/cli/serve_static/index.html)
 * at build time, so the demo always tracks the shipped UI, and:
 *   - injects demo/mock-api.js (fetch interceptor) BEFORE the app script,
 *   - injects a slim "demo mode" banner and an iframe theme-sync snippet,
 *   - copies the mock and src/data/api-snapshot.json next to index.html
 *     (fetched relatively, so the demo works under any base path).
 *
 * Plain node only (>= 18); no python. The snapshot is regenerated separately with
 * `uv run docs-site/scripts/snapshot-api.py`. Idempotent: files are rewritten
 * only when their content changes.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const site = path.resolve(here, '..');
const repo = path.resolve(site, '..');

const SPA = path.join(repo, 'src/reflect_kb/cli/serve_static/index.html');
const MOCK = path.join(site, 'demo/mock-api.js');
const SNAPSHOT = path.join(site, 'src/data/api-snapshot.json');
const OUT = path.join(site, 'public/demo');

function fail(msg) {
  console.error(`build-demo: ${msg}`);
  process.exit(1);
}
for (const [label, p] of [['frontend', SPA], ['mock', MOCK], ['snapshot', SNAPSHOT]]) {
  if (!fs.existsSync(p)) {
    fail(`${label} missing: ${path.relative(repo, p)}` +
      (label === 'snapshot' ? ' (run: uv run docs-site/scripts/snapshot-api.py)' : ''));
  }
}

// Theme + banner. Uses the SPA's own CSS variables so it follows light/dark.
const INJECT = `<!-- reflect docs demo: injected by docs-site/scripts/build-demo.mjs -->
<style>
  #demo-banner { display:flex; align-items:center; justify-content:center; gap:10px;
    padding:3px 12px; font:12px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
    background:var(--accent-soft); color:var(--accent-ink);
    border-bottom:1px solid var(--border); flex:none; }
  #demo-banner button { border:0; background:transparent; color:inherit; cursor:pointer;
    font-size:14px; line-height:1; padding:0 4px; opacity:.7; }
  #demo-banner button:hover { opacity:1; }
</style>
<script src="mock-api.js"></script>
<script>
(function () {
  // Follow the docs theme when embedded in the docs page (same origin).
  try {
    if (window.parent === window) return;
    var root = window.parent.document.documentElement;
    var pick = function () {
      var t = root.getAttribute("data-theme");
      return t === "dark" || t === "light" ? t : null;
    };
    var first = pick();
    if (first) localStorage.setItem("reflect-theme", first);
    new MutationObserver(function () {
      var t = pick();
      if (!t) return;
      document.documentElement.dataset.theme = t;
      try { localStorage.setItem("reflect-theme", t); } catch (e) {}
      var box = document.getElementById("showents");
      if (box) box.dispatchEvent(new Event("change")); // redraw the graph canvas
    }).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  } catch (e) {}
})();
</script>
`;

const BANNER = `<div id="demo-banner" data-testid="demo-banner" role="note">
  <span>Demo mode: sample data, changes are in-memory only (reload to reset)</span>
  <button type="button" aria-label="Dismiss" onclick="this.parentNode.remove()">&times;</button>
</div>
`;

let html = fs.readFileSync(SPA, 'utf8');

const scriptAt = html.search(/<script\b/);
if (scriptAt === -1) fail('no <script> found in the frontend; update build-demo.mjs');
html = html.slice(0, scriptAt) + INJECT + html.slice(scriptAt);

const bodyAt = html.search(/<body[^>]*>/);
if (bodyAt === -1) fail('no <body> found in the frontend; update build-demo.mjs');
const bodyEnd = bodyAt + html.slice(bodyAt).match(/<body[^>]*>/)[0].length;
html = html.slice(0, bodyEnd) + '\n' + BANNER + html.slice(bodyEnd);

html = html.replace(/<title>([^<]*)<\/title>/, (_, t) => `<title>${t} (demo)</title>`);

function writeIfChanged(file, content) {
  const buf = Buffer.isBuffer(content) ? content : Buffer.from(content);
  if (fs.existsSync(file) && fs.readFileSync(file).equals(buf)) return false;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, buf);
  return true;
}

const changed = [
  writeIfChanged(path.join(OUT, 'index.html'), html),
  writeIfChanged(path.join(OUT, 'mock-api.js'), fs.readFileSync(MOCK)),
  writeIfChanged(path.join(OUT, 'api-snapshot.json'), fs.readFileSync(SNAPSHOT)),
].filter(Boolean).length;

console.log(`build-demo: ${path.relative(repo, OUT)}/ (${changed ? changed + ' file(s) updated' : 'up to date'})`);
