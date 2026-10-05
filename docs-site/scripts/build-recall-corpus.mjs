#!/usr/bin/env node
// Derive the compact corpus the recall-walkthrough explainer runs on.
//
//   input   src/data/api-snapshot.json            (real `reflect serve` payloads over the invented sample KB)
//           data/sample-kb/documents/<id>.md      (optional enrichment: confidence_num, discovery_tokens,
//                                                  problem/root_cause/fix/rule, which serve does not return)
//   output  src/data/recall-corpus.json
//
// Usage: node scripts/build-recall-corpus.mjs [--check]
//   --check  exit 1 if the committed output differs from what would be written.
//
// Nothing here is real user data: the snapshot is built from invented learnings.

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SNAPSHOT = join(ROOT, 'src/data/api-snapshot.json');
const DOCS_DIR = join(ROOT, 'data/sample-kb/documents');
const OUT = join(ROOT, 'src/data/recall-corpus.json');
const CHECK = process.argv.includes('--check');

if (!existsSync(SNAPSHOT)) {
  console.error('missing ' + SNAPSHOT + ' (written by the demo snapshot step)');
  process.exit(2);
}
const snap = JSON.parse(readFileSync(SNAPSHOT, 'utf8'));
const memory = snap.endpoints?.memory ?? {};
const list = snap.endpoints?.memories ?? [];
const graph = snap.endpoints?.graph ?? { nodes: [], edges: [] };

// Same tier -> number table as recall.py CONFIDENCE_TIER_NUMS.
const TIER_NUM = { high: 0.9, medium: 0.6, low: 0.3 };

function parseFrontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---\n?/);
  if (!m) return {};
  const out = {};
  let listKey = null;
  for (const line of m[1].split('\n')) {
    const item = line.match(/^\s*-\s+(.*)$/);
    if (item && listKey) {
      (out[listKey] ||= []).push(unquote(item[1]));
      continue;
    }
    const kv = line.match(/^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$/);
    if (!kv) continue;
    if (kv[2] === '') {
      listKey = kv[1];
      continue;
    }
    listKey = null;
    out[kv[1]] = unquote(kv[2]);
  }
  return out;
}
function unquote(s) {
  const t = s.trim();
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) return t.slice(1, -1);
  return t;
}

const docs = [];
for (const row of list) {
  const full = memory[row.id] ?? row;
  const mdPath = join(DOCS_DIR, full.file ?? row.file ?? row.id + '.md');
  let fm = {};
  let mdChars = 0;
  if (existsSync(mdPath)) {
    const raw = readFileSync(mdPath, 'utf8');
    fm = parseFrontmatter(raw);
    mdChars = raw.length;
  }
  const insight = full.key_insight ?? fm.key_insight ?? '';
  const parts = [full.title, insight];
  for (const k of ['problem', 'root_cause', 'fix', 'rule']) if (fm[k]) parts.push(fm[k]);
  if (Array.isArray(fm.symptoms)) parts.push(...fm.symptoms);
  if (full.body) parts.push(String(full.body));
  const text = parts.filter(Boolean).join('\n');
  const conf = String(full.confidence ?? 'medium').toLowerCase();
  const confNum = fm.confidence_num !== undefined && !Number.isNaN(parseFloat(fm.confidence_num)) ? parseFloat(fm.confidence_num) : (TIER_NUM[conf] ?? 0.6);
  const ents = (full.entities ?? []).map((e) => e.name);
  docs.push({
    id: full.id,
    title: full.title,
    insight,
    text,
    // size of the stored note, what recall.py measures as read_tokens (chars // 4)
    chars: mdChars || text.length,
    tags: full.tags ?? [],
    type: full.type ?? '',
    conf,
    confNum,
    date: full.date ?? '',
    proof: full.provenance?.proof_count ?? null,
    scope: full.scope ?? '',
    superseded: full.superseded_by ?? null,
    discovery: fm.discovery_tokens !== undefined && !Number.isNaN(parseInt(fm.discovery_tokens, 10)) ? parseInt(fm.discovery_tokens, 10) : null,
    entities: (full.entity_names ?? []).map((s) => String(s)),
    entityLabels: ents,
  });
}

// Entity graph: slug -> display label, doc -> entity slugs (from mention edges), entity-entity weights.
const labels = {};
for (const n of graph.nodes ?? []) if (n.kind === 'entity') labels[n.id.replace(/^e:/, '')] = n.label;
const mentions = {};
for (const e of graph.edges ?? []) {
  if (e.kind !== 'mention') continue;
  const doc = e.s.replace(/^m:/, '');
  (mentions[doc] ||= []).push(e.t.replace(/^e:/, ''));
}
for (const d of docs) d.entities = mentions[d.id] ?? d.entities;
const relations = (snap.relations ?? []).map((r) => [r.s.replace(/^e:/, ''), r.t.replace(/^e:/, ''), r.w]);

const corpus = {
  _about: 'Derived from src/data/api-snapshot.json by scripts/build-recall-corpus.mjs. Invented sample data.',
  now: snap.meta?.now ?? '2026-10-14T00:00:00+00:00',
  docs,
  entityLabels: labels,
  relations,
};
const json = JSON.stringify(corpus) + '\n';

if (CHECK) {
  const cur = existsSync(OUT) ? readFileSync(OUT, 'utf8') : '';
  if (cur !== json) {
    console.error('recall-corpus.json is stale; run node scripts/build-recall-corpus.mjs');
    process.exit(1);
  }
  console.log('recall-corpus.json up to date');
} else {
  writeFileSync(OUT, json);
  console.log('wrote ' + OUT + ': ' + docs.length + ' docs, ' + Object.keys(labels).length + ' entities, ' + relations.length + ' relations, ' + (json.length / 1024).toFixed(1) + ' KB');
}
