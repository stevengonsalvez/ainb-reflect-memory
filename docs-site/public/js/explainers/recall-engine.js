// Toy re-implementation of the reflect recall pipeline, for the walkthrough.
//
// STRUCTURE, ORDER and CONSTANTS are the real ones, read from
//   plugin/skills/recall/scripts/recall.py        (recall(), rrf_fuse, rerank_with_scores,
//                                                  mmr_select, filter_by_token_budget, render_markdown)
//   plugin/skills/recall/hooks/*.py               (per-hook limits and budgets)
//   plugin/reflect.toml                           ([recall.cross_encoder], [recall.boost])
// The RETRIEVERS are toys: a hashed bag-of-words/trigram "embedding", a small BM25, a
// graph walk over the sample KB's entity edges, and a term-overlap stand-in for the
// cross-encoder. Everything downstream of the arms (fusion, boosts, MMR, budget) is the
// real formula applied to those toy scores.

export const C = {
  RRF_K: 60, // recall.py RRF_K
  CE_CANDIDATES: 20, // recall.py CE_CANDIDATES
  CE_UNSCORED: 1e-6, // recall.py CE_UNSCORED
  MMR_LAMBDA: 0.7, // recall.py MMR_LAMBDA (RECALL_MMR_LAMBDA)
  MMR_CANDIDATES: 20, // recall.py MMR_CANDIDATES
  RECENCY_WINDOW_DAYS: 365.0,
  ALPHA: {
    confidence: 0.2, // CONFIDENCE_ALPHA
    recency: 0.2, // RECENCY_ALPHA
    tag: 0.2, // TAG_ALPHA
    proof: 0.1, // PROOF_COUNT_ALPHA
    project: 0.2, // PROJECT_AFFINITY_ALPHA (reflect.toml project_affinity_alpha)
    speculative: 0.2, // SPECULATIVE_ALPHA
  },
  // R12 calibrated floors (CALIBRATED_FLOORS); runtime default is 0 for every arm.
  CALIBRATED_FLOORS: { vector: 0.1, bm25: 0.15, graph: 0.0, temporal: 0.05 },
  DEFAULT_DISCOVERY_TOKENS: 1500,
  DISCOVERY_CATEGORY_AVERAGES: { 'bug-fix': 3000, 'anti-pattern': 2500, correction: 2000, pattern: 1500, decision: 1200 },
  USER_PROMPT_LIMIT: 3,
  USER_PROMPT_MAX_CHARS: 1500,
};

export const fetchedLimit = (limit) => Math.max(limit * 2, 10); // recall(): fetched_limit

export const PROFILES = {
  prompt: {
    label: 'UserPromptSubmit hook',
    limit: 9, // USER_PROMPT_LIMIT * 3 over-fetch
    minOverlap: 0,
    maxTokens: 0,
    maxChars: 3000, // USER_PROMPT_MAX_CHARS * 2
    hook: true,
    ref: 'plugin/skills/recall/hooks/user_prompt_submit_recall.py',
  },
  start: {
    label: 'SessionStart hook',
    limit: 3, // SESSION_START_LIMIT
    minOverlap: 0.2, // SESSION_START_MIN_OVERLAP
    maxTokens: 0, // SESSION_START_MAX_TOKENS
    maxChars: 1500, // SESSION_START_MAX_CHARS
    hook: false,
    ref: 'plugin/skills/recall/hooks/session_start_recall.py',
  },
  explicit: {
    label: '/reflect:recall (defaults)',
    limit: 10, // DEFAULT_LIMIT
    minOverlap: 0,
    maxTokens: 0,
    maxChars: 2000, // DEFAULT_MAX_CHARS
    hook: false,
    ref: 'plugin/skills/recall/scripts/recall.py (argparse defaults)',
  },
};

// ---------------------------------------------------------------- text utils

const STOP = new Set(
  ('a an the is are was were be been do does did to of in on at for with and or not no how what when where which who why our we i you it its ' +
    'this that these those there here from by as into over under again still now then than can could should would may might will shall am ' +
    'get got use used using my your').split(' '),
);

// recall.py _content_terms: [a-z0-9][a-z0-9_\-]{2,}, minus stopwords
export function contentTerms(text) {
  const out = new Set();
  for (const t of text.toLowerCase().match(/[a-z0-9][a-z0-9_-]{2,}/g) || []) if (!STOP.has(t)) out.add(t);
  return out;
}

// recall.py lexical_overlap (R7 / R12): share of query content terms present in the chunk.
export function lexicalOverlap(query, chunk) {
  const q = contentTerms(query);
  if (q.size === 0) return 1.0;
  const text = chunk.toLowerCase();
  let hits = 0;
  for (const term of q) {
    if (text.includes(term)) {
      hits += 1;
      continue;
    }
    const parts = term.split(/[-_]/).filter((p) => p.length >= 3);
    if (parts.length && parts.every((p) => text.includes(p))) hits += 1;
  }
  return hits / q.size;
}

export function stem(w) {
  let s = w;
  if (s.length > 4 && s.endsWith('ies')) s = s.slice(0, -3) + 'y';
  else if (s.length > 5 && s.endsWith('ing')) s = s.slice(0, -3);
  else if (s.length > 4 && s.endsWith('ed')) s = s.slice(0, -2);
  else if (s.length > 4 && s.endsWith('es')) s = s.slice(0, -2);
  else if (s.length > 3 && s.endsWith('s') && !s.endsWith('ss')) s = s.slice(0, -1);
  if (s.length > 3 && s.endsWith('e')) s = s.slice(0, -1);
  return s;
}

export function tokens(text) {
  const out = [];
  for (const t of text.toLowerCase().split(/[^a-z0-9]+/)) {
    if (t.length < 2 || STOP.has(t)) continue;
    out.push(stem(t));
  }
  return out;
}

function fnv(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

const DIM = 384;

function embed(toks, idf) {
  const v = new Float32Array(DIM);
  const tf = new Map();
  for (const t of toks) tf.set(t, (tf.get(t) || 0) + 1);
  for (const [t, n] of tf) {
    const w = (1 + Math.log(n)) * Math.sqrt(idf(t));
    add(v, 'w:' + t, w);
    const g = '#' + t + '#';
    for (let i = 0; i + 3 <= g.length; i += 1) add(v, 'g:' + g.slice(i, i + 3), 0.35 * w);
  }
  let norm = 0;
  for (let i = 0; i < DIM; i += 1) norm += v[i] * v[i];
  norm = Math.sqrt(norm) || 1;
  for (let i = 0; i < DIM; i += 1) v[i] /= norm;
  return v;
}
function add(v, feat, w) {
  const h = fnv(feat);
  v[h % DIM] += (h >>> 16) & 1 ? w : -w;
}
function cosine(a, b) {
  let s = 0;
  for (let i = 0; i < DIM; i += 1) s += a[i] * b[i];
  return s;
}

// ------------------------------------------------------------------ corpus

export function prepare(corpus) {
  const docs = corpus.docs.map((d, i) => {
    const titleToks = tokens(d.title);
    const bodyToks = tokens(d.text + ' ' + d.tags.join(' ') + ' ' + (d.entityLabels || []).join(' '));
    const toks = [...titleToks, ...titleToks, ...bodyToks]; // title counts double for BM25
    return { ...d, idx: i, titleToks, bodyToks, toks, ts: Date.parse(d.date + 'T00:00:00Z') };
  });
  const N = docs.length;
  const df = new Map();
  for (const d of docs) for (const t of new Set(d.toks)) df.set(t, (df.get(t) || 0) + 1);
  const idfBM = (t) => Math.log(1 + (N - (df.get(t) || 0) + 0.5) / ((df.get(t) || 0) + 0.5));
  const idfVec = (t) => 1 + Math.log((N + 1) / ((df.get(t) || 0) + 1));
  const avgLen = docs.reduce((s, d) => s + d.toks.length, 0) / N;
  for (const d of docs) d.vec = embed(d.toks, idfVec);
  // entity graph
  const adj = new Map();
  for (const [a, b, w] of corpus.relations) {
    if (!adj.has(a)) adj.set(a, []);
    if (!adj.has(b)) adj.set(b, []);
    adj.get(a).push([b, w]);
    adj.get(b).push([a, w]);
  }
  const byEntity = new Map();
  for (const d of docs) for (const e of d.entities) {
    if (!byEntity.has(e)) byEntity.set(e, []);
    byEntity.get(e).push(d.idx);
  }
  const byId = new Map(docs.map((d) => [d.id, d]));
  return { corpus, docs, byId, N, df, idfBM, idfVec, avgLen, adj, byEntity, now: Date.parse(corpus.now) };
}

// ---------------------------------------------------------------- temporal

const MONTHS = { january: 1, jan: 1, february: 2, feb: 2, march: 3, mar: 3, april: 4, apr: 4, may: 5, june: 6, jun: 6, july: 7, jul: 7, august: 8, aug: 8, september: 9, sep: 9, sept: 9, october: 10, oct: 10, november: 11, nov: 11, december: 12, dec: 12 };
const DAY = 86400000;
const utc = (y, m, d) => Date.UTC(y, m - 1, d);
const dayStart = (t) => Math.floor(t / DAY) * DAY;

// Subset of plugin/skills/recall/scripts/temporal_extraction.py (R6): yesterday/today,
// last/past N days|weeks|months, last/this week|month|year, ISO dates, "in <month>".
export function extractTemporal(query, now) {
  const q = query.toLowerCase();
  const ref = dayStart(now);
  let m;
  const range = (s, e, conf, text) => ({ start: dayStart(s), end: dayStart(e) + DAY - 1, confidence: conf, matched: text });
  if ((m = q.match(/\byesterday\b/))) return range(ref - DAY, ref - DAY, 0.9, m[0]);
  if ((m = q.match(/\btoday\b/))) return range(ref, ref, 0.9, m[0]);
  if ((m = q.match(/\b(?:last|past|previous)\s+(\d+|a|one|two|three|four|five|six)\s+(day|week|month)s?\b/))) {
    const words = { a: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6 };
    const n = words[m[1]] ?? parseInt(m[1], 10);
    const unit = { day: 1, week: 7, month: 30 }[m[2]];
    return range(ref - n * unit * DAY, ref, 0.8, m[0]);
  }
  if ((m = q.match(/\blast\s+week\b/))) {
    const wd = (new Date(ref).getUTCDay() + 6) % 7; // Monday = 0
    const start = ref - (wd + 7) * DAY;
    return range(start, start + 6 * DAY, 0.9, m[0]);
  }
  if ((m = q.match(/\bthis\s+week\b/))) {
    const wd = (new Date(ref).getUTCDay() + 6) % 7;
    return range(ref - wd * DAY, ref, 0.9, m[0]);
  }
  if ((m = q.match(/\blast\s+month\b/))) {
    const d = new Date(ref);
    const first = utc(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
    const end = first - DAY;
    const e = new Date(end);
    return range(utc(e.getUTCFullYear(), e.getUTCMonth() + 1, 1), end, 0.9, m[0]);
  }
  if ((m = q.match(/\bthis\s+month\b/))) {
    const d = new Date(ref);
    return range(utc(d.getUTCFullYear(), d.getUTCMonth() + 1, 1), ref, 0.9, m[0]);
  }
  if ((m = q.match(/\blast\s+year\b/))) {
    const y = new Date(ref).getUTCFullYear() - 1;
    return range(utc(y, 1, 1), utc(y, 12, 31), 0.9, m[0]);
  }
  if ((m = q.match(/\bthis\s+year\b/))) {
    return range(utc(new Date(ref).getUTCFullYear(), 1, 1), ref, 0.9, m[0]);
  }
  if ((m = q.match(/\b(20\d{2})[-/](\d{2})[-/](\d{2})\b/))) {
    const t = utc(+m[1], +m[2], +m[3]);
    if (!Number.isNaN(t)) return range(t, t, 1.0, m[0]);
  }
  if ((m = q.match(/\b(?:in|during|from|since|of)\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b/))) {
    const month = MONTHS[m[1]];
    const d = new Date(ref);
    const year = month <= d.getUTCMonth() + 1 ? d.getUTCFullYear() : d.getUTCFullYear() - 1;
    const last = new Date(utc(year, month + 1, 1) - DAY).getUTCDate();
    return range(utc(year, month, 1), utc(year, month, last), 0.6, m[1]);
  }
  return null;
}

// -------------------------------------------------------------------- arms

function rankList(scored, limit) {
  scored.sort((a, b) => b.score - a.score || a.idx - b.idx);
  return scored.slice(0, limit).map((x, i) => ({ ...x, rank: i + 1 }));
}

function vectorArm(P, query, limit) {
  const qv = embed(tokens(query), P.idfVec);
  const scored = P.docs.map((d) => ({ id: d.id, idx: d.idx, score: cosine(qv, d.vec) })).filter((x) => x.score > 0.02);
  return rankList(scored, limit);
}

function bm25Arm(P, query, limit) {
  const qt = [...new Set(tokens(query))];
  const k1 = 1.2;
  const b = 0.75;
  const scored = [];
  for (const d of P.docs) {
    const tf = new Map();
    for (const t of d.toks) tf.set(t, (tf.get(t) || 0) + 1);
    let s = 0;
    const hit = [];
    for (const t of qt) {
      const f = tf.get(t) || 0;
      if (!f) continue;
      s += P.idfBM(t) * ((f * (k1 + 1)) / (f + k1 * (1 - b + (b * d.toks.length) / P.avgLen)));
      hit.push(t);
    }
    if (s > 0) scored.push({ id: d.id, idx: d.idx, score: s, terms: hit });
  }
  return rankList(scored, limit);
}

const NEIGHBOUR_BUDGET = 6; // toy: neighbour entities followed per seed entity

export function seedEntities(P, query) {
  const qToks = new Set(tokens(query));
  const qRaw = query.toLowerCase();
  const seeds = [];
  for (const slug of P.byEntity.keys()) {
    const parts = tokens(slug);
    if (!parts.length) continue;
    const phrase = qRaw.includes(slug);
    const allIn = parts.every((p) => qToks.has(p) || [...qToks].some((q) => q.length >= 5 && p.startsWith(q)));
    if (phrase || allIn) seeds.push(slug);
  }
  return seeds;
}

function graphArm(P, query, limit) {
  const seeds = seedEntities(P, query);
  const entScore = new Map(); // slug -> { w, via }
  for (const s of seeds) entScore.set(s, { w: 1.0, hop: 0, from: null });
  for (const s of seeds) {
    const nb = [...(P.adj.get(s) || [])].sort((a, b) => b[1] - a[1]).slice(0, NEIGHBOUR_BUDGET);
    for (const [n, w] of nb) {
      if (entScore.has(n)) continue;
      entScore.set(n, { w: 0.5 * (w / 10), hop: 1, from: s });
    }
  }
  const docScore = new Map();
  for (const [slug, info] of entScore) {
    for (const di of P.byEntity.get(slug) || []) {
      const cur = docScore.get(di) || { score: 0, via: [] };
      cur.score += info.w;
      cur.via.push({ entity: slug, hop: info.hop, from: info.from });
      docScore.set(di, cur);
    }
  }
  const scored = [...docScore].map(([di, v]) => ({ id: P.docs[di].id, idx: di, score: v.score, via: v.via }));
  return { seeds, list: rankList(scored, limit) };
}

function temporalArm(P, query, temporal, limit) {
  if (!temporal) return [];
  let topical = query.toLowerCase();
  if (temporal.matched) topical = topical.replace(temporal.matched, ' ');
  const mid = temporal.start + (temporal.end - temporal.start) / 2;
  const span = temporal.end - temporal.start;
  const scored = [];
  for (const d of P.docs) {
    if (!(d.ts >= temporal.start && d.ts <= temporal.end)) continue;
    const prox = span > 0 ? 1 - Math.min(Math.abs(d.ts - mid) / (span / 2), 1) : 1;
    const ov = topical.trim() ? lexicalOverlap(topical, d.text + ' ' + d.title) : 0;
    scored.push({ id: d.id, idx: d.idx, score: ov + prox * 1e-3, overlap: ov, prox });
  }
  return rankList(scored, limit);
}

// R12: per-arm floor on query-term coverage, applied BEFORE fusion.
function applyFloor(P, list, query, floor) {
  if (floor <= 0 || !list.length) return { kept: list, dropped: [] };
  const kept = [];
  const dropped = [];
  for (const it of list) {
    const d = P.byId.get(it.id);
    const ov = lexicalOverlap(query, d.text + ' ' + d.title);
    (ov >= floor ? kept : dropped).push({ ...it, overlap: ov });
  }
  return { kept: kept.map((x, i) => ({ ...x, rank: i + 1 })), dropped };
}

// ------------------------------------------------------------------- fusion

// recall.py rrf_fuse: score(doc) = sum 1/(k + rank) over arms; insertion order breaks ties.
export function rrfFuse(lists, k) {
  const scores = new Map();
  const contrib = new Map();
  const order = [];
  for (const [arm, list] of lists) {
    for (const it of list) {
      if (!scores.has(it.id)) {
        scores.set(it.id, 0);
        contrib.set(it.id, []);
        order.push(it.id);
      }
      const part = 1 / (k + it.rank);
      scores.set(it.id, scores.get(it.id) + part);
      contrib.get(it.id).push({ arm, rank: it.rank, part });
    }
  }
  const sorted = order.map((id, i) => ({ id, i })).sort((a, b) => scores.get(b.id) - scores.get(a.id) || a.i - b.i);
  return sorted.map((x, i) => ({ id: x.id, rank: i + 1, score: scores.get(x.id), contrib: contrib.get(x.id) }));
}

// ------------------------------------------------------------------- rerank

const sigmoid = (x) => 1 / (1 + Math.exp(-x));
export const boundedBoost = (norm, alpha) => 1 + alpha * (Math.min(1, Math.max(0, norm)) - 0.5);
const confNumNorm = (n) => Math.min(1, Math.max(0, (n - 0.3) / 0.6));
function recencyNorm(ts, now) {
  if (!Number.isFinite(ts)) return 0.5;
  const days = (now - ts) / DAY;
  return Math.max(0.1, Math.min(1.0, 1.0 - days / C.RECENCY_WINDOW_DAYS));
}
function proofBoost(n) {
  const norm = n !== null && n >= 1 ? 0.5 + Math.log(n) / 10 : 0.5;
  return boundedBoost(norm, C.ALPHA.proof);
}

// Stand-in for cross-encoder/ms-marco-MiniLM-L-6-v2: joint query/document term coverage
// (IDF-weighted, title weighted highest) plus a phrase bonus, mapped to a logit.
function toyCE(P, query, d) {
  const q = [...new Set(tokens(query))];
  if (!q.length) return { logit: -4, s: 0 };
  const title = new Set(d.titleToks);
  const insight = new Set(tokens(d.insight));
  const body = new Set(d.bodyToks);
  const wsum = q.reduce((a, t) => a + P.idfBM(t), 0) || 1;
  const cov = (set) => q.reduce((a, t) => a + (set.has(t) ? P.idfBM(t) : 0), 0) / wsum;
  const seq = tokens(d.title + ' ' + d.insight + ' ' + d.text);
  const qseq = tokens(query);
  let bi = 0;
  for (let i = 0; i + 1 < qseq.length; i += 1) {
    for (let j = 0; j + 1 < seq.length; j += 1) {
      if (seq[j] === qseq[i] && seq[j + 1] === qseq[i + 1]) {
        bi += 1;
        break;
      }
    }
  }
  const s = 0.3 * cov(title) + 0.2 * cov(insight) + 0.4 * cov(body) + 0.1 * (bi / Math.max(1, qseq.length - 1));
  return { logit: 12 * s - 4, s };
}

function rerank(P, query, fused, queryTags, projectHint) {
  const window = new Set(fused.slice(0, C.CE_CANDIDATES).map((x) => x.id));
  const qt = new Set(queryTags.map((t) => t.toLowerCase()));
  const rows = fused.map((f, i) => {
    const d = P.byId.get(f.id);
    const scored = window.has(f.id);
    const ce = scored ? toyCE(P, query, d) : null;
    const ceSig = scored ? sigmoid(ce.logit) : C.CE_UNSCORED;
    const tagNorm = qt.size ? d.tags.filter((t) => qt.has(t.toLowerCase())).length / qt.size : 0.5;
    const boosts = {
      confidence: boundedBoost(confNumNorm(d.confNum), C.ALPHA.confidence),
      recency: boundedBoost(recencyNorm(d.ts, P.now), C.ALPHA.recency),
      tag: boundedBoost(tagNorm, C.ALPHA.tag),
      proof: proofBoost(d.proof),
      project: boundedBoost(projectHint && d.scope === projectHint ? 1.0 : 0.5, C.ALPHA.project),
      speculative: boundedBoost(d.tags.some((t) => t.toLowerCase() === 'speculative') ? 0 : 0.5, C.ALPHA.speculative),
    };
    const product = Object.values(boosts).reduce((a, b) => a * b, 1);
    return { id: f.id, fusedRank: f.rank, scored, ceLogit: ce ? ce.logit : null, ce: ceSig, boosts, product, score: ceSig * product, i };
  });
  rows.sort((a, b) => b.score - a.score || a.i - b.i);
  return rows.map((r, i) => ({ ...r, rank: i + 1 }));
}

// --------------------------------------------------------------------- MMR

// recall.py mmr_select, line for line: keep the top hit, then argmax(lam*rel - (1-lam)*maxsim).
function mmr(P, ranked, embeddedIds, k, lam) {
  const scoreOf = new Map(ranked.map((r) => [r.id, r.score]));
  const head = ranked.filter((r) => embeddedIds.has(r.id));
  const tail = ranked.filter((r) => !embeddedIds.has(r.id));
  if (k <= 0) return { picks: [], dropped: [], final: [] };
  if (head.length <= 1 || !embeddedIds.has(ranked[0].id)) {
    const final = ranked.slice(0, k).map((r) => r.id);
    return { picks: final.map((id, i) => ({ id, step: i + 1, rel: null, maxSim: null, value: null, simTo: null })), dropped: [], final, passthrough: true };
  }
  const maxScore = Math.max(...head.map((r) => scoreOf.get(r.id)), 0);
  const rel = new Map(head.map((r) => [r.id, maxScore > 0 ? scoreOf.get(r.id) / maxScore : 0]));
  const vec = (id) => P.byId.get(id).vec;
  const selected = [head[0].id];
  const picks = [{ id: head[0].id, step: 1, rel: rel.get(head[0].id), maxSim: 0, value: null, simTo: null, first: true }];
  let remaining = head.slice(1).map((r) => r.id);
  const maxSim = new Map();
  const simTo = new Map();
  for (const id of remaining) {
    maxSim.set(id, cosine(vec(id), vec(head[0].id)));
    simTo.set(id, head[0].id);
  }
  while (remaining.length && selected.length < k) {
    let bestIdx = 0;
    let bestVal = -Infinity;
    const table = [];
    remaining.forEach((id, idx) => {
      const val = lam * rel.get(id) - (1 - lam) * maxSim.get(id);
      table.push({ id, rel: rel.get(id), maxSim: maxSim.get(id), simTo: simTo.get(id), value: val });
      if (val > bestVal) {
        bestIdx = idx;
        bestVal = val;
      }
    });
    const pickedId = remaining[bestIdx];
    remaining = remaining.filter((_, i) => i !== bestIdx);
    selected.push(pickedId);
    picks.push({ id: pickedId, step: selected.length, rel: rel.get(pickedId), maxSim: maxSim.get(pickedId), simTo: simTo.get(pickedId), value: bestVal, considered: table });
    for (const id of remaining) {
      const s = cosine(vec(id), vec(pickedId));
      if (s > maxSim.get(id)) {
        maxSim.set(id, s);
        simTo.set(id, pickedId);
      }
    }
  }
  const final = [...selected];
  for (const r of tail) if (final.length < k) final.push(r.id);
  const dropped = remaining.map((id) => ({ id, rel: rel.get(id), maxSim: maxSim.get(id), simTo: simTo.get(id), value: lam * rel.get(id) - (1 - lam) * maxSim.get(id) }));
  return { picks, dropped, final };
}

// ------------------------------------------------------------------ budget

const estTokens = (chars) => Math.max(1, Math.floor(chars / 4)); // recall.py _est_tokens

function discoveryOf(d) {
  if (d.discovery !== null && d.discovery !== undefined) return d.discovery;
  return C.DISCOVERY_CATEGORY_AVERAGES[d.type] ?? C.DEFAULT_DISCOVERY_TOKENS;
}

function budget(P, ids, query, prof, maxTokensOverride) {
  const maxTokens = maxTokensOverride ?? prof.maxTokens;
  // R4: filter_by_token_budget (>= 1 always kept)
  const afterTok = [];
  let spent = 0;
  const tokRows = [];
  for (const id of ids) {
    const d = P.byId.get(id);
    const cost = estTokens(d.chars);
    if (maxTokens > 0 && afterTok.length && spent + cost > maxTokens) {
      tokRows.push({ id, cost, kept: false, reason: 'token budget ' + maxTokens + ' spent (' + spent + ')' });
      continue;
    }
    afterTok.push(id);
    spent += cost;
    tokRows.push({ id, cost, kept: true });
  }
  // render_markdown: header + one entry per learning, cut at max_chars
  const reads = afterTok.map((id) => estTokens(P.byId.get(id).chars));
  const discs = afterTok.map((id) => discoveryOf(P.byId.get(id)));
  const readSum = reads.reduce((a, b) => a + b, 0);
  const discSum = discs.reduce((a, b) => a + b, 0);
  const header = '## Prior learnings relevant to `' + query.slice(0, 80) + '` - ' + afterTok.length + ' learnings, ~' + readSum + ' tok injected, est ~' + (discSum - readSum) + ' tok saved';
  const first = header + '\n';
  let used = first.length;
  const entries = [];
  let truncatedFrom = -1;
  afterTok.forEach((id, i) => {
    const d = P.byId.get(id);
    const pct = discs[i] > 0 ? Math.round(((discs[i] - reads[i]) / discs[i]) * 100) : 0;
    const row = '· D:' + discs[i] + ' → R:' + reads[i] + ' (' + (pct >= 0 ? '-' : '+') + Math.abs(pct) + '%)';
    const entry = '- **[' + id + ']** ' + (d.insight || d.title) + ' - ' + row + '\n';
    if (truncatedFrom >= 0) {
      entries.push({ id, entry, chars: entry.length, cum: used, kept: false, reason: 'after truncation', read: reads[i], disc: discs[i] });
      return;
    }
    if (used + entry.length > prof.maxChars) {
      truncatedFrom = i;
      entries.push({ id, entry, chars: entry.length, cum: used + entry.length, kept: false, reason: 'cumulative ' + (used + entry.length) + ' chars > max-chars ' + prof.maxChars, read: reads[i], disc: discs[i] });
      return;
    }
    used += entry.length;
    entries.push({ id, entry, chars: entry.length, cum: used, kept: true, read: reads[i], disc: discs[i] });
  });
  let block = first + entries.filter((e) => e.kept).map((e) => e.entry).join('');
  if (truncatedFrom >= 0) block += '- _(…' + (afterTok.length - truncatedFrom) + ' more truncated)_\n';
  block = block.replace(/\s+$/, '') + '\n';
  const out = { tokRows, entries, header, block, markdownChars: block.length, droppedByTokens: tokRows.filter((r) => !r.kept).length, maxChars: prof.maxChars, maxTokens };
  // UserPromptSubmit hook: filter_to_new keeps the first USER_PROMPT_LIMIT *blocks*
  // (the header counts as block 1), then hard-cuts at USER_PROMPT_MAX_CHARS.
  if (prof.hook) {
    const keptEntries = entries.filter((e) => e.kept);
    const blocks = [header, ...keptEntries.map((e) => e.entry.replace(/\n$/, ''))];
    const kept = blocks.slice(0, C.USER_PROMPT_LIMIT);
    let text = kept.join('\n');
    const hookDropped = blocks.slice(C.USER_PROMPT_LIMIT).length;
    let cut = false;
    if (text.length > C.USER_PROMPT_MAX_CHARS) {
      text = text.slice(0, C.USER_PROMPT_MAX_CHARS).replace(/\s+$/, '') + ' …';
      cut = true;
    }
    out.hook = { blocks: kept.length, injectedIds: keptEntries.slice(0, C.USER_PROMPT_LIMIT - 1).map((e) => e.id), droppedBlocks: hookDropped, text, cut };
  }
  return out;
}

// --------------------------------------------------------------------- run

export function run(P, query, opts = {}) {
  const prof = { ...PROFILES[opts.profile || 'prompt'] };
  if (opts.limit !== undefined) prof.limit = opts.limit;
  if (opts.minOverlap !== undefined) prof.minOverlap = opts.minOverlap;
  if (opts.maxChars !== undefined) prof.maxChars = opts.maxChars;
  const k = opts.rrfK ?? C.RRF_K;
  const lam = opts.lambda ?? C.MMR_LAMBDA;
  const queryTags = (opts.tags || []).filter(Boolean);
  const fl = fetchedLimit(prof.limit);
  const trace = { query, profile: prof, fetched: fl, k, lambda: lam };

  trace.temporal = extractTemporal(query, P.now);
  const vec = vectorArm(P, query, fl);
  const bm = bm25Arm(P, query, fl);
  const g = graphArm(P, query, fl);
  const tem = temporalArm(P, query, trace.temporal, fl);
  trace.arms = { vector: vec, bm25: bm, graph: g.list, temporal: tem };
  trace.seeds = g.seeds;

  const floors = opts.floors ? C.CALIBRATED_FLOORS : { vector: 0, bm25: 0, graph: 0, temporal: 0 };
  trace.floors = { on: !!opts.floors, values: floors, dropped: {} };
  const arms = {};
  for (const name of ['vector', 'bm25', 'graph', 'temporal']) {
    const r = applyFloor(P, trace.arms[name], query, floors[name]);
    arms[name] = r.kept;
    trace.floors.dropped[name] = r.dropped;
  }
  trace.afterFloors = arms;

  trace.rrf = rrfFuse([['vector', arms.vector], ['bm25', arms.bm25], ['graph', arms.graph], ['temporal', arms.temporal]], k);
  trace.rerank = rerank(P, query, trace.rrf, queryTags, opts.project || '');

  // filters: quarantine (none in the sample KB), --confidence ANY, then the R7 OOD gate
  let list = trace.rerank;
  const top5 = list.slice(0, 5).map((r) => lexicalOverlap(query, P.byId.get(r.id).text + ' ' + P.byId.get(r.id).title));
  const best = top5.length ? Math.max(...top5) : 0;
  trace.ood = { threshold: prof.minOverlap, best, gated: prof.minOverlap > 0 && list.length > 0 && best < prof.minOverlap };
  if (trace.ood.gated) list = [];
  trace.afterFilter = list;

  // MMR window = the first MMR_CANDIDATES of the RRF list (their embeddings are fetched)
  const embeddedIds = new Set(trace.rrf.slice(0, C.MMR_CANDIDATES).map((x) => x.id));
  if (opts.mmr === false) {
    const final = list.slice(0, prof.limit).map((r) => r.id);
    trace.mmr = { picks: final.map((id, i) => ({ id, step: i + 1 })), dropped: [], final, off: true };
  } else {
    trace.mmr = mmr(P, list, embeddedIds, prof.limit, lam);
  }
  trace.budget = budget(P, trace.mmr.final, query, prof, opts.maxTokens);
  return trace;
}
