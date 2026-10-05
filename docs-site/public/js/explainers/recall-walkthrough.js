import { h, clear } from './dom.js';
import { prepare, run, C, PROFILES, fetchedLimit } from './recall-engine.js';


const BLOB = 'https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/';

const EXAMPLES = [
  ['JWT rejected after login', 'jwt token rejected right after login'],
  ['Slow docker builds', 'speed up docker image builds in ci'],
  ['Pod not ready', 'kubernetes pod not ready after deploy'],
  ['Postgres connections', 'postgres connection limit'],
  ['Date phrase', 'redis cache stampede last month'],
  ['Nothing relevant', 'ukulele banana tuning'],
];

const ARMS = [
  { id: 'vector', label: 'Vector', sub: 'toy embedding', real: 'reflect search --mode naive (all-mpnet-base-v2 over nano-graphrag chunks)', cls: 'arm-vector' },
  { id: 'bm25', label: 'BM25', sub: 'toy lexical', real: 'QMD lexical search (fetch_qmd)', cls: 'arm-bm25' },
  { id: 'graph', label: 'Graph', sub: 'entity edges', real: 'reflect search --mode local (entity neighbourhood)', cls: 'arm-graph' },
  { id: 'temporal', label: 'Temporal', sub: 'only with a date phrase', real: 'fetch_temporal: date-window scan of the corpus', cls: 'arm-temporal' },
];

const STAGES = [
  { id: 'query', label: 'Query', code: 'recall(): extract_temporal_constraint, cache lookup' },
  { id: 'arms', label: 'Arms', code: 'recall(): ThreadPoolExecutor(max_workers=4), fetched_limit = max(limit*2, 10)' },
  { id: 'floors', label: 'Arm floors', code: 'apply_arm_floor (R12), CALIBRATED_FLOORS' },
  { id: 'rrf', label: 'RRF fusion', code: 'rrf_fuse(), RRF_K = 60' },
  { id: 'rerank', label: 'Rerank', code: 'fetch_ce_scores + rerank_with_scores (R2, R8), CE_CANDIDATES = 20' },
  { id: 'filter', label: 'Filters', code: 'filter_by_quarantine, filter_by_confidence, apply_ood_gate (R7)' },
  { id: 'mmr', label: 'MMR', code: 'mmr_select (R3), MMR_LAMBDA = 0.7, MMR_CANDIDATES = 20' },
  { id: 'budget', label: 'Budget cut', code: 'filter_by_token_budget (R4), render_markdown(max_chars), hook cut' },
];

function init(root, corpus) {
  const P = prepare(corpus);
  const byId = P.byId;
  const short = (id) => byId.get(id).title;
  const ell = (str, n) => (str.length > n ? str.slice(0, n - 1) + '\u2026' : str);
  const put = (...nodes) => panel.append(...nodes.flat().filter((n) => n !== null && n !== undefined && n !== false));

  const q0 = new URLSearchParams(location.hash.replace(/^#/, ''));
  const st = {
    query: q0.get('q') || EXAMPLES[0][1],
    profile: PROFILES[q0.get('p')] ? q0.get('p') : 'prompt',
    stage: clampInt(q0.get('s'), 0, STAGES.length - 1, 0),
    rrfK: numOr(q0.get('k'), C.RRF_K),
    lambda: numOr(q0.get('l'), C.MMR_LAMBDA),
    mmr: q0.get('mmr') !== '0',
    floors: q0.get('f') === '1',
    limit: q0.get('n') ? clampInt(q0.get('n'), 1, 20, null) : null,
    maxChars: q0.get('c') ? clampInt(q0.get('c'), 200, 6000, null) : null,
    maxTokens: clampInt(q0.get('t'), 0, 5000, 0),
    minOverlap: q0.get('o') !== null && q0.get('o') !== '' ? numOr(q0.get('o'), null) : null,
    tags: q0.get('tags') || '',
    project: q0.get('proj') || '',
    hl: null,
    trace: null,
  };

  function clampInt(v, lo, hi, d) {
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : d;
  }
  function numOr(v, d) {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : d;
  }

  // ---------------------------------------------------------------- shell
  const input = h('input', { type: 'search', id: 'xp-q', class: 'xp-q', value: st.query, autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Query', placeholder: 'Ask the sample knowledge base...' });
  const runBtn = h('button', { type: 'submit', class: 'xp-btn xp-btn-primary', text: 'Run' });
  const form = h('form', { class: 'xp-qform', role: 'search' }, h('label', { for: 'xp-q', class: 'xp-eyebrow', text: 'Query' }), h('div', { class: 'xp-qrow' }, input, runBtn));
  const chips = h('div', { class: 'xp-chips', role: 'group', 'aria-label': 'Example queries' }, EXAMPLES.map(([l, q]) => h('button', { type: 'button', class: 'xp-chipbtn', 'data-q': q, text: l })));

  // settings
  const settings = h('details', { class: 'xp-card xp-settings' });
  const setBody = h('div', { class: 'xp-set-body' });

  const stageSeg = h('div', { class: 'xp-stagebar', role: 'tablist', 'aria-label': 'Pipeline stages' }, STAGES.map((s, i) => h('button', { type: 'button', role: 'tab', class: 'xp-stagetab', id: 'xp-tab-' + s.id, 'aria-controls': 'xp-panel', 'data-i': String(i) }, h('span', { class: 'xp-step-n', text: String(i + 1) }), h('span', { text: s.label }))));
  const scrub = h('input', { type: 'range', class: 'xp-range', min: '0', max: String(STAGES.length - 1), step: '1', value: String(st.stage), 'aria-label': 'Pipeline stage' });
  const btnPrev = h('button', { type: 'button', class: 'xp-btn', text: 'Prev stage' });
  const btnNext = h('button', { type: 'button', class: 'xp-btn xp-btn-primary', text: 'Next stage' });
  const panel = h('div', { class: 'xp-card xp-panel', id: 'xp-panel', role: 'tabpanel', tabindex: '-1', 'aria-live': 'polite' });
  const matrixCard = h('div', { class: 'xp-card' });

  root.append(
    form,
    chips,
    settings,
    h('div', { class: 'xp-stagectl' }, stageSeg, h('div', { class: 'xp-scrubrow' }, scrub, h('div', { class: 'xp-controls' }, btnPrev, btnNext))),
    panel,
    matrixCard,
  );

  // ------------------------------------------------------------- settings
  function buildSettings() {
    const prof = PROFILES[st.profile];
    const wasOpen = settings.open;
    clear(settings);
    settings.open = wasOpen;
    settings.append(h('summary', { text: 'Tune the constants (real defaults shown)' }), setBody);
    clear(setBody);
    const sel = h('select', { id: 'xp-profile' }, Object.entries(PROFILES).map(([k, v]) => h('option', { value: k, text: v.label + ': limit ' + v.limit + ', max-chars ' + v.maxChars + (v.minOverlap ? ', min-overlap ' + v.minOverlap : '') })));
    sel.value = st.profile;
    sel.addEventListener('change', () => {
      st.profile = sel.value;
      st.limit = null;
      st.maxChars = null;
      st.minOverlap = null;
      buildSettings();
      recompute();
    });
    const row = (label, ctrl, hint) => h('div', { class: 'xp-field' }, h('label', { class: 'xp-label', for: ctrl.id, text: label }), ctrl, hint ? h('p', { class: 'xp-note', text: hint }) : null);
    const slider = (id, key, min, max, step, fmt) => {
      const out = h('output', { class: 'xp-out', for: id });
      const r = h('input', { type: 'range', class: 'xp-range', id, min, max, step, value: String(st[key]) });
      const upd = () => {
        out.textContent = fmt(st[key]);
      };
      r.addEventListener('input', () => {
        st[key] = parseFloat(r.value);
        upd();
        recompute();
      });
      upd();
      return h('div', { class: 'xp-pair2' }, r, out);
    };
    const numInput = (id, key, def, min, max) => {
      const n = h('input', { type: 'number', id, min, max, step: '1', value: st[key] === null ? '' : String(st[key]), placeholder: String(def) });
      n.addEventListener('input', () => {
        const v = parseInt(n.value, 10);
        st[key] = Number.isFinite(v) ? Math.min(max, Math.max(min, v)) : null;
        recompute();
      });
      return n;
    };
    const overlapInput = (prof) => {
      const ov = h('input', { type: 'number', id: 'xp-overlap-x', min: '0', max: '100', step: '5', placeholder: String(Math.round(prof.minOverlap * 100)), value: st.minOverlap === null ? '' : String(Math.round(st.minOverlap * 100)) });
      ov.addEventListener('input', () => {
        const v = parseInt(ov.value, 10);
        st.minOverlap = Number.isFinite(v) ? Math.min(100, Math.max(0, v)) / 100 : null;
        recompute();
      });
      return ov;
    };
    const chk = (id, key, label) => {
      const c = h('input', { type: 'checkbox', id });
      c.checked = !!st[key];
      c.addEventListener('change', () => {
        st[key] = c.checked;
        recompute();
      });
      return h('div', { class: 'xp-field xp-field-check' }, c, h('label', { class: 'xp-label', for: id, text: label }));
    };
    const rrfSlider = slider('xp-rrfk', 'rrfK', 1, 200, 1, (v) => 'k = ' + v + (v === C.RRF_K ? ' (real default)' : ''));
    const lamSlider = slider('xp-lam', 'lambda', 0, 1, 0.05, (v) => 'lambda = ' + v.toFixed(2) + (v === C.MMR_LAMBDA ? ' (real default)' : ''));
    const tags = h('input', { type: 'text', id: 'xp-tags', value: st.tags, placeholder: 'e.g. auth, jwt', autocomplete: 'off' });
    tags.addEventListener('input', () => {
      st.tags = tags.value;
      recompute();
    });
    const proj = h('select', { id: 'xp-proj' }, [['', 'none (per-project shard, the default)'], ...[...new Set(corpus.docs.map((d) => d.scope))].sort().map((s) => [s, s])].map(([v, t]) => h('option', { value: v, text: t })));
    proj.value = st.project;
    proj.addEventListener('change', () => {
      st.project = proj.value;
      recompute();
    });
    setBody.append(
      row('Caller', sel, 'Which code path is calling recall.py. Source: ' + prof.ref),
      row('RRF constant k', rrfSlider, 'RRF_K = 60 in recall.py. Larger k flattens the difference between ranks.'),
      row('MMR lambda', lamSlider, 'MMR_LAMBDA = 0.7 (RECALL_MMR_LAMBDA). 1.0 is pure relevance, 0.0 is pure diversity.'),
      chk('xp-mmr', 'mmr', 'MMR diversity on (RECALL_MMR, --no-mmr turns it off)'),
      chk('xp-floors', 'floors', 'Apply the calibrated per-arm floors (R12, off by default in recall.py)'),
      row('Final limit (top-k)', numInput('xp-limit', 'limit', prof.limit, 1, 20), 'Default for this caller: ' + prof.limit),
      row('--max-chars', numInput('xp-chars', 'maxChars', prof.maxChars, 200, 6000), 'Default for this caller: ' + prof.maxChars),
      row('--max-tokens (R4, 0 = off)', numInput('xp-tokens', 'maxTokens', 0, 0, 5000), 'The hooks leave this at 0; REFLECT_RECALL_MAX_TOKENS sets it for SessionStart.'),
      row('--min-overlap (R7 OOD gate, percent)', overlapInput(prof), 'Percent of query terms the best hit must contain. Default for this caller: ' + Math.round(prof.minOverlap * 100) + '%.'),
      row('Query tags (--tags)', tags, 'Only the SessionStart hook passes tags (from recent commits). Empty keeps the tag boost neutral.'),
      row('Current project', proj, 'Project affinity only applies when searching the pooled global KB; in a per-project shard it is neutral.'),
      h('button', { type: 'button', class: 'xp-btn', id: 'xp-resetset', text: 'Reset constants' }),
    );
    setBody.querySelector('#xp-resetset').addEventListener('click', () => {
      Object.assign(st, { rrfK: C.RRF_K, lambda: C.MMR_LAMBDA, mmr: true, floors: false, limit: null, maxChars: null, maxTokens: 0, minOverlap: null, tags: '', project: '' });
      buildSettings();
      recompute();
    });
  }

  // -------------------------------------------------------------- compute
  function recompute() {
    const opts = {
      profile: st.profile,
      rrfK: st.rrfK,
      lambda: st.lambda,
      mmr: st.mmr,
      floors: st.floors,
      tags: st.tags.split(',').map((x) => x.trim()).filter(Boolean),
      project: st.project,
    };
    if (st.limit !== null) opts.limit = st.limit;
    if (st.maxChars !== null) opts.maxChars = st.maxChars;
    if (st.maxTokens > 0) opts.maxTokens = st.maxTokens;
    if (st.minOverlap !== null) opts.minOverlap = st.minOverlap;
    st.trace = run(P, st.query, opts);
    render();
  }

  // --------------------------------------------------------------- helpers
  const f2 = (v) => v.toFixed(2);
  const f4 = (v) => v.toFixed(4);
  const f3 = (v) => v.toFixed(3);

  function finalSet() {
    const t = st.trace;
    const ids = t.budget.entries.filter((e) => e.kept).map((e) => e.id);
    if (t.budget.hook) return new Set(t.budget.hook.injectedIds);
    return new Set(ids);
  }

  function deltaChip(prev, now) {
    if (prev === null || prev === undefined) return h('span', { class: 'xp-delta is-new', title: 'not in the previous list', text: 'new' });
    const d = prev - now;
    if (d === 0) return h('span', { class: 'xp-delta', title: 'same rank as the previous stage', text: '=' });
    return h('span', { class: 'xp-delta ' + (d > 0 ? 'is-up' : 'is-down'), title: (d > 0 ? 'up ' : 'down ') + Math.abs(d) + ' from the previous stage', text: (d > 0 ? '▲' : '▼') + Math.abs(d) });
  }

  function item({ id, rank, score, scoreText, prev, why, extra, dim }) {
    const d = byId.get(id);
    const inFinal = finalSet().has(id);
    const li = h('li', { class: 'xp-item' + (inFinal ? ' is-final' : '') + (dim ? ' is-dim' : ''), 'data-id': id, tabindex: '0' },
      h('div', { class: 'xp-item-top' },
        h('span', { class: 'xp-rank', text: '#' + rank }),
        h('span', { class: 'xp-item-title', text: d.title }),
        prev !== undefined ? deltaChip(prev, rank) : null,
      ),
      h('div', { class: 'xp-item-meta' },
        h('code', { text: id.replace(/^lrn-/, '').replace(/-[0-9a-f]{6}$/, '') }),
        scoreText !== undefined ? h('span', { class: 'xp-score', text: scoreText }) : null,
        inFinal ? h('span', { class: 'xp-chip lane-queue', text: 'injected' }) : null,
      ),
      extra || null,
      why ? h('div', { class: 'xp-why-line', text: why }) : null,
    );
    return li;
  }

  function wireHighlight(scope) {
    const set = (id) => {
      st.hl = id;
      root.querySelectorAll('[data-id]').forEach((el) => el.classList.toggle('is-hl', !!id && el.dataset.id === id));
    };
    scope.querySelectorAll('[data-id]').forEach((el) => {
      el.addEventListener('mouseenter', () => set(el.dataset.id));
      el.addEventListener('mouseleave', () => set(null));
      el.addEventListener('focus', () => set(el.dataset.id));
      el.addEventListener('blur', () => set(null));
    });
  }

  // ---------------------------------------------------------------- stages
  function stageHeader(i, blurb, constants) {
    const s = STAGES[i];
    return [
      h('div', { class: 'xp-eyebrow', text: 'Stage ' + (i + 1) + ' of ' + STAGES.length }),
      h('h4', { class: 'xp-h', text: s.label }),
      h('p', { text: blurb }),
      constants && constants.length ? h('div', { class: 'xp-consts' }, constants.map(([k, v]) => h('span', { class: 'xp-const' }, h('code', { text: k }), ' ', h('strong', { text: v })))) : null,
    ];
  }
  function codeLine(i) {
    return h('p', { class: 'xp-codeline' }, 'In code: ', h('code', { text: STAGES[i].code }), ' in ', h('a', { href: BLOB + 'plugin/skills/recall/scripts/recall.py', rel: 'noopener' }, h('code', { text: 'recall.py' })));
  }

  function renderStage() {
    const t = st.trace;
    clear(panel);
    const i = st.stage;
    const id = STAGES[i].id;
    if (id === 'query') {
      const tokens = [...new Set(t.query.toLowerCase().match(/[a-z0-9][a-z0-9_-]{2,}/g) || [])];
      put(
        ...stageHeader(i, 'recall.py first checks the cache (exact hash, then a fuzzy Jaccard match; this toy always runs fresh), then pulls any date phrase out of the query.', [
          ['caller', PROFILES[st.profile].label],
          ['fetched_limit', String(t.fetched)],
          ['final limit', String(t.profile.limit)],
        ]),
        h('div', { class: 'xp-kv-block' },
          h('div', null, h('span', { class: 'xp-eyebrow', text: 'Query' }), h('p', null, h('code', { text: t.query }))),
          h('div', null, h('span', { class: 'xp-eyebrow', text: 'Terms (content words, stopwords dropped)' }), h('p', null, tokens.length ? tokens.map((x) => h('code', { class: 'xp-term', text: x })) : h('em', { text: 'none' }))),
          h('div', null, h('span', { class: 'xp-eyebrow', text: 'Date phrase (R6)' }), h('p', null, t.temporal ? [h('code', { text: t.temporal.matched }), ' resolves to ' + new Date(t.temporal.start).toISOString().slice(0, 10) + ' .. ' + new Date(t.temporal.end).toISOString().slice(0, 10) + ', so the temporal arm will run.'] : 'none found, so the temporal arm returns nothing (no false boost).')),
          h('div', null, h('span', { class: 'xp-eyebrow', text: 'Entities the graph arm will seed from' }), h('p', null, t.seeds.length ? t.seeds.map((x) => h('code', { class: 'xp-term', text: x })) : h('em', { text: 'none matched, graph arm returns nothing' }))),
        ),
        h('p', { class: 'xp-note', text: 'The sample knowledge base has ' + P.N + ' invented learnings. Reference date for relative phrases is ' + corpus.now.slice(0, 10) + '.' }),
        codeLine(i),
      );
    } else if (id === 'arms') {
      const cols = ARMS.map((a) => {
        const list = t.arms[a.id];
        const fired = a.id !== 'temporal' || t.temporal;
        const ol = h('ol', { class: 'xp-list' });
        list.slice(0, 8).forEach((it) => {
          let extra = null;
          let why;
          if (a.id === 'graph') {
            const seedHit = it.via.filter((v) => v.hop === 0).map((v) => v.entity);
            const nb = it.via.filter((v) => v.hop === 1);
            why = (seedHit.length ? 'mentions ' + seedHit.join(', ') : '') + (nb.length ? (seedHit.length ? '; ' : '') + 'via neighbour ' + nb.slice(0, 2).map((v) => v.entity + ' (from ' + v.from + ')').join(', ') : '');
          } else if (a.id === 'bm25') why = 'matched: ' + it.terms.join(', ');
          else if (a.id === 'temporal') why = 'in window, topical overlap ' + f2(it.overlap);
          ol.append(item({ id: it.id, rank: it.rank, scoreText: a.id === 'bm25' ? f2(it.score) : a.id === 'graph' ? f2(it.score) : a.id === 'temporal' ? f2(it.overlap) : f3(it.score), why, extra }));
        });
        if (!list.length) ol.append(h('li', { class: 'xp-empty', text: fired ? 'No candidates.' : 'Skipped: no date phrase in the query.' }));
        return h('section', { class: 'xp-col ' + a.cls }, h('h5', null, a.label, ' ', h('span', { class: 'xp-muted', text: a.sub })), h('p', { class: 'xp-note', text: 'Real: ' + a.real }), ol);
      });
      put(
        ...stageHeader(i, 'Up to four retrieval arms run in parallel and each returns its own ranked list. The scores are on different scales, which is exactly why fusion uses ranks.', [
          ['arms', t.temporal ? '4' : '3 (+ temporal when dated)'],
          ['fetched_limit', String(t.fetched)],
        ]),
        h('div', { class: 'xp-cols' }, cols),
        h('p', { class: 'xp-note', text: 'Toy embedding: signed hashing of word and character-trigram features, 384 dimensions, IDF-weighted cosine. It is not a neural model. Lists show the top 8 of ' + t.fetched + '.' }),
        codeLine(i),
      );
    } else if (id === 'floors') {
      const rows = ARMS.map((a) => {
        const fl = t.floors.values[a.id];
        const dropped = t.floors.dropped[a.id];
        return h('tr', null,
          h('th', { scope: 'row', text: a.label }),
          h('td', { text: f2(fl) }),
          h('td', { text: String(t.arms[a.id].length) }),
          h('td', { text: String(t.afterFloors[a.id].length) }),
          h('td', null, dropped.length ? dropped.map((d) => h('div', null, h('code', { text: ell(short(d.id), 50) }), ' coverage ' + f2(d.overlap))) : h('span', { class: 'xp-muted', text: 'none' })),
        );
      });
      put(
        ...stageHeader(i, 'Each arm can drop its own weak candidates before fusion, using query-term coverage. recall.py ships every floor at 0 (off) because a non-zero floor changes the OOD gate contract; reflect calibrate-thresholds suggests the values below.', [
          ['state', t.floors.on ? 'calibrated floors ON' : 'all floors 0 (default)'],
        ]),
        h('table', { class: 'xp-table' },
          h('thead', null, h('tr', null, ['Arm', 'Floor', 'In', 'Out', 'Dropped'].map((x) => h('th', { text: x })))),
          h('tbody', null, rows),
        ),
        h('p', { class: 'xp-note', text: 'Turn on "calibrated per-arm floors" under the constants to see candidates removed here. Calibrated values: vector 0.1, bm25 0.15, graph 0, temporal 0.05 (CALIBRATED_FLOORS).' }),
        codeLine(i),
      );
    } else if (id === 'rrf') {
      const ol = h('ol', { class: 'xp-list xp-list-wide' });
      const best = (id2) => {
        const ranks = ARMS.map((a) => {
          const x = t.afterFloors[a.id].find((y) => y.id === id2);
          return x ? x.rank : Infinity;
        });
        return Math.min(...ranks);
      };
      t.rrf.slice(0, 12).forEach((it) => {
        const parts = it.contrib.map((c) => h('span', { class: 'xp-part ' + ARMS.find((a) => a.id === c.arm).cls }, c.arm + ' #' + c.rank + ' = 1/(' + t.k + '+' + c.rank + ') = ' + f4(c.part)));
        const bestArm = best(it.id);
        const why = it.contrib.length > 1 ? it.contrib.length + ' arms agree, so the reciprocal ranks add.' : 'Only ' + it.contrib[0].arm + ' found it: one reciprocal term.';
        ol.append(item({ id: it.id, rank: it.rank, scoreText: f4(it.score), prev: bestArm === Infinity ? undefined : bestArm, why, extra: h('div', { class: 'xp-parts' }, parts) }));
      });
      put(
        ...stageHeader(i, 'Reciprocal Rank Fusion: score(doc) = sum over arms of 1 / (k + rank). It needs no score normalisation. The delta chip compares to the best rank any single arm gave.', [
          ['k', String(t.k) + (t.k === C.RRF_K ? ' (real)' : ' (you changed it)')],
          ['arms fused', String(['vector', 'bm25', 'graph', 'temporal'].filter((a) => t.afterFloors[a].length).length)],
        ]),
        ol,
        h('p', { class: 'xp-note', text: 'Ties are broken by first-seen order (vector, BM25, graph, temporal), as in the Python.' }),
        codeLine(i),
      );
    } else if (id === 'rerank') {
      const ol = h('ol', { class: 'xp-list xp-list-wide' });
      t.rerank.slice(0, 12).forEach((r) => {
        const chips = Object.entries(r.boosts).filter(([, v]) => Math.abs(v - 1) > 0.0005).map(([k, v]) => h('span', { class: 'xp-boost ' + (v > 1 ? 'is-up' : 'is-down'), title: k + ' boost, bounded to +/- alpha/2', text: k + ' x' + v.toFixed(3) }));
        const delta = r.fusedRank - r.rank;
        const why = !r.scored ? 'Outside the top ' + C.CE_CANDIDATES + ' of RRF, so the cross-encoder never scored it (CE = ' + C.CE_UNSCORED + ').' : delta >= 2 ? 'The joint query/document score (toy CE) is higher than its fused rank implied.' : delta <= -2 ? 'The toy CE found a weaker joint match than the arms did.' : 'The cross-encoder agrees with fusion.';
        ol.append(item({
          id: r.id, rank: r.rank, scoreText: f3(r.score), prev: r.fusedRank, why,
          extra: h('div', { class: 'xp-parts' },
            h('span', { class: 'xp-part', text: r.scored ? 'CE logit ' + f2(r.ceLogit) + ' → sigmoid ' + f3(r.ce) : 'CE unscored ' + r.ce }),
            h('span', { class: 'xp-part', text: 'boosts x' + r.product.toFixed(3) }),
            chips,
          ),
        }));
      });
      put(
        ...stageHeader(i, 'The fused top ' + C.CE_CANDIDATES + ' are scored jointly with the query by a cross-encoder; its sigmoid is the primary key. Bounded boosts (1 + alpha x (norm - 0.5), so at most +/- alpha/2) then nudge it. Delta chips compare to the RRF rank.', [
          ['CE_CANDIDATES', String(C.CE_CANDIDATES)],
          ['model', 'ms-marco-MiniLM-L-6-v2 (toy here)'],
          ['alpha', 'confidence 0.2, recency 0.2, tag 0.2, proof 0.1'],
        ]),
        ol,
        h('p', { class: 'xp-note', text: 'score = sigmoid(ce_logit) x confidence x recency x tags x proof x project x speculative. Recency is linear over 365 days from the reference date (floor 0.1). Boosts equal to 1.000 are hidden. The real cross-encoder is a neural model; this page substitutes IDF-weighted term coverage.' }),
        codeLine(i),
      );
    } else if (id === 'filter') {
      const o = t.ood;
      put(
        ...stageHeader(i, 'After scoring: drop quarantined notes, apply the --confidence filter, then the out-of-domain gate. The gate looks at the best of the top 5 hits; if even that barely mentions the query terms, the whole result set is suppressed.', [
          ['quarantine', 'none in this KB'],
          ['--confidence', 'ANY (both hooks)'],
          ['--min-overlap', o.threshold ? String(o.threshold) : '0 (gate off)'],
        ]),
        h('div', { class: 'xp-gate ' + (o.gated ? 'is-gated' : 'is-pass') },
          h('strong', { text: o.threshold <= 0 ? 'Gate is off for this caller' : o.gated ? 'Gated: result set suppressed' : 'Gate passed' }),
          h('p', { text: 'Best query-term coverage in the top 5: ' + Math.round(o.best * 100) + '% against a threshold of ' + Math.round(o.threshold * 100) + '%.' + (o.gated ? ' Nothing is injected, and the empty recall is logged as a knowledge gap.' : '') }),
        ),
        h('p', { class: 'xp-note', text: 'Try "ukulele banana tuning" with the SessionStart caller to see the gate fire. The UserPromptSubmit hook does not pass --min-overlap, so it would inject nearest-neighbour junk for the same query.' }),
        codeLine(i),
      );
    } else if (id === 'mmr') {
      const m = t.mmr;
      const tab = h('table', { class: 'xp-table xp-mmr' },
        h('thead', null, h('tr', null, ['Pick', 'Learning', 'rel', 'max sim', 'MMR', 'Why'].map((x) => h('th', { text: x })))));
      const tb = h('tbody');
      m.picks.forEach((p) => {
        const d = byId.get(p.id);
        let why;
        if (m.off) why = 'MMR off: plain top-k of the rerank order.';
        else if (p.first) why = 'The top reranked hit is always kept.';
        else if (p.maxSim !== null && p.maxSim !== undefined) why = 'Best trade-off; closest already-picked item is ' + (p.simTo ? ell(short(p.simTo), 40) : '?') + ' (cos ' + f2(p.maxSim) + ').';
        else why = 'Kept in rerank order.';
        tb.append(h('tr', { 'data-id': p.id, tabindex: '0', class: 'xp-trow' },
          h('td', { text: '#' + p.step }),
          h('td', { text: d.title }),
          h('td', { text: p.rel !== null && p.rel !== undefined ? f2(p.rel) : '-' }),
          h('td', { text: p.maxSim !== null && p.maxSim !== undefined ? f2(p.maxSim) : '-' }),
          h('td', { text: p.value !== null && p.value !== undefined ? f2(p.value) : '-' }),
          h('td', { text: why }),
        ));
      });
      tab.append(tb);
      const dropped = m.dropped.slice(0, 4).map((d) => h('li', { 'data-id': d.id, tabindex: '0' }, h('strong', { text: short(d.id) }), ' skipped: its closest picked neighbour is ' + (d.simTo ? ell(short(d.simTo), 44) : '?') + ' (cos ' + f2(d.maxSim) + '), MMR ' + f2(d.value) + '.'));
      put(
        ...stageHeader(i, 'MMR picks the final top-k one at a time: argmax( lambda x rel - (1 - lambda) x max similarity to what is already picked ). rel is the rerank score divided by the window maximum; similarity is cosine between embeddings (here the toy vectors).', [
          ['lambda', st.lambda.toFixed(2) + (st.lambda === C.MMR_LAMBDA ? ' (real)' : ' (changed)')],
          ['MMR_CANDIDATES', String(C.MMR_CANDIDATES)],
          ['top-k', String(t.profile.limit)],
        ]),
        m.final.length ? tab : h('p', { class: 'xp-warn', text: 'Nothing to select: the OOD gate emptied the set.' }),
        dropped.length && !m.off ? h('div', null, h('p', { class: 'xp-eyebrow', text: 'Near-misses left out' }), h('ul', { class: 'xp-bullets' }, dropped)) : null,
        h('p', { class: 'xp-note', text: 'Drag lambda toward 0 under "Tune the constants" and watch near-duplicates get pushed out. At 1.0 the order equals the rerank order.' }),
        codeLine(i),
      );
    } else if (id === 'budget') {
      const b = t.budget;
      const rows = b.entries.map((e) => h('tr', { 'data-id': e.id, tabindex: '0', class: 'xp-trow' + (e.kept ? '' : ' is-dim') },
        h('td', { text: e.kept ? 'kept' : 'cut' }),
        h('td', { text: short(e.id) }),
        h('td', { text: 'R:' + e.read }),
        h('td', { text: 'D:' + e.disc }),
        h('td', { text: String(e.cum) + ' / ' + b.maxChars }),
        h('td', { text: e.kept ? '' : e.reason }),
      ));
      const tokCut = b.tokRows.filter((r) => !r.kept);
      const hook = b.hook;
      put(
        ...stageHeader(i, 'Last two cuts. R4 trims by estimated tokens (chars / 4) when --max-tokens is set. Then render_markdown writes one line per learning with its token economics and stops at --max-chars.' + (hook ? ' The UserPromptSubmit hook then keeps the header plus the first ' + C.USER_PROMPT_LIMIT + ' learnings (the header does not count toward the cap).' : ''), [
          ['--max-tokens', b.maxTokens ? String(b.maxTokens) : '0 (off)'],
          ['--max-chars', String(b.maxChars)],
          ['hook cut', hook ? C.USER_PROMPT_LIMIT + ' learnings, ' + C.USER_PROMPT_MAX_CHARS + ' chars' : 'n/a for this caller'],
        ]),
        b.entries.length ? h('table', { class: 'xp-table' }, h('thead', null, h('tr', null, ['', 'Learning', 'Read', 'Discovery', 'Chars so far', 'Cut because'].map((x) => h('th', { text: x })))), h('tbody', null, rows)) : h('p', { class: 'xp-warn', text: 'Nothing survived to render.' }),
        tokCut.length ? h('p', { class: 'xp-note', text: tokCut.length + ' dropped by the token budget before rendering.' }) : null,
        h('div', { class: 'xp-eyebrow', text: hook ? 'What the model receives (after the hook cut)' : 'What the model receives' }),
        h('pre', { class: 'xp-summary xp-inject', tabindex: '0', text: hook ? hook.text || '(nothing)' : (b.entries.some((e) => e.kept) ? b.block : '(nothing injected)') }),
        hook ? h('p', { class: 'xp-note', text: 'Render produced ' + b.entries.filter((e) => e.kept).length + ' entries; the hook kept the header plus ' + hook.injectedIds.length + ' of them' + (hook.cut ? ' and then hard-cut at ' + C.USER_PROMPT_MAX_CHARS + ' characters' : '') + '. This is verified against filter_to_new in user_prompt_submit_recall.py.' }) : null,
        h('p', { class: 'xp-note', text: 'D is the estimated tokens it would take to rediscover the fact, R is the stored note size. The sample notes carry invented discovery_tokens in their frontmatter.' }),
        codeLine(i),
      );
    }
    wireHighlight(panel);
  }

  // ---------------------------------------------------------- rank matrix
  function renderMatrix() {
    const t = st.trace;
    const cols = [
      { id: 'vector', label: 'Vector', stage: 1, get: (id) => rankIn(t.arms.vector, id) },
      { id: 'bm25', label: 'BM25', stage: 1, get: (id) => rankIn(t.arms.bm25, id) },
      { id: 'graph', label: 'Graph', stage: 1, get: (id) => rankIn(t.arms.graph, id) },
    ];
    if (t.temporal) cols.push({ id: 'temporal', label: 'Time', stage: 1, get: (id) => rankIn(t.arms.temporal, id) });
    cols.push(
      { id: 'rrf', label: 'RRF', stage: 3, get: (id) => rankIn(t.rrf, id) },
      { id: 'rerank', label: 'Rerank', stage: 4, get: (id) => rankIn(t.afterFilter, id) },
      { id: 'mmr', label: 'MMR', stage: 6, get: (id) => { const i = t.mmr.final.indexOf(id); return i < 0 ? null : i + 1; } },
      { id: 'final', label: 'Injected', stage: 7, get: (id) => { const ids = [...(t.budget.hook ? t.budget.hook.injectedIds : t.budget.entries.filter((e) => e.kept).map((e) => e.id))]; const i = ids.indexOf(id); return i < 0 ? null : i + 1; } },
    );
    function rankIn(list, id) {
      const x = list.find((y) => y.id === id);
      return x ? x.rank : null;
    }
    const ids = new Set();
    [t.arms.vector, t.arms.bm25, t.arms.graph, t.arms.temporal, t.rrf, t.afterFilter].forEach((l) => l.slice(0, 6).forEach((x) => ids.add(x.id)));
    t.mmr.final.forEach((id) => ids.add(id));
    const rows = [...ids].sort((a, b) => {
      const ra = cols[cols.length - 1].get(a) ?? 99;
      const rb = cols[cols.length - 1].get(b) ?? 99;
      if (ra !== rb) return ra - rb;
      const ma = t.mmr.final.indexOf(a) < 0 ? 99 : t.mmr.final.indexOf(a);
      const mb = t.mmr.final.indexOf(b) < 0 ? 99 : t.mmr.final.indexOf(b);
      if (ma !== mb) return ma - mb;
      return (rankIn(t.rrf, a) ?? 99) - (rankIn(t.rrf, b) ?? 99);
    }).slice(0, 16);
    const table = h('table', { class: 'xp-matrix xp-rank-matrix' });
    const head = h('tr', null, h('th', { scope: 'col', class: 'xp-corner', text: 'Learning' }));
    cols.forEach((c) => head.append(h('th', { scope: 'col', class: st.stage === c.stage ? 'is-current' : '' }, h('button', { type: 'button', class: 'xp-step-btn', 'data-stage': String(c.stage), title: 'Jump to stage ' + (c.stage + 1) }, h('span', { class: 'xp-step-l', text: c.label })))));
    const tbody = h('tbody');
    rows.forEach((id) => {
      const fin = cols[cols.length - 1].get(id);
      const tr = h('tr', { 'data-id': id, tabindex: '0', class: fin ? 'is-final' : '' });
      tr.append(h('th', { scope: 'row', class: 'xp-lane-h xp-rowh', title: byId.get(id).title }, h('span', { text: byId.get(id).title.length > 44 ? byId.get(id).title.slice(0, 43) + '…' : byId.get(id).title })));
      cols.forEach((c) => {
        const r = c.get(id);
        tr.append(h('td', { class: (st.stage === c.stage ? 'is-current ' : '') + (r ? 'has-rank rk-' + Math.min(r, 4) : '') }, r ? String(r) : '·'));
      });
      tbody.append(tr);
    });
    table.append(h('thead', null, head), tbody);
    clear(matrixCard).append(
      h('div', { class: 'xp-toolbar' }, h('h4', { text: 'Rank trace: where each learning sits at every stage' })),
      h('div', { class: 'xp-matrix-wrap', tabindex: '0', role: 'region', 'aria-label': 'Rank trace table (scrollable)' }, table),
      h('p', { class: 'xp-note', text: 'A dot means the stage did not rank it. Highlighted rows are what reaches the model. Click a column header to jump to its stage.' }),
    );
    matrixCard.querySelectorAll('[data-stage]').forEach((b) => b.addEventListener('click', () => go(parseInt(b.dataset.stage, 10))));
    wireHighlight(matrixCard);
  }

  // ------------------------------------------------------------- controls
  function renderControls() {
    stageSeg.querySelectorAll('.xp-stagetab').forEach((b, i) => {
      const on = i === st.stage;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
      b.classList.toggle('is-past', i < st.stage);
    });
    panel.setAttribute('aria-labelledby', 'xp-tab-' + STAGES[st.stage].id);
    scrub.value = String(st.stage);
    scrub.setAttribute('aria-valuetext', STAGES[st.stage].label);
    btnPrev.disabled = st.stage === 0;
    btnNext.disabled = st.stage === STAGES.length - 1;
  }

  function writeHash() {
    const q = new URLSearchParams();
    q.set('q', st.query);
    if (st.profile !== 'prompt') q.set('p', st.profile);
    if (st.stage) q.set('s', String(st.stage));
    if (st.rrfK !== C.RRF_K) q.set('k', String(st.rrfK));
    if (st.lambda !== C.MMR_LAMBDA) q.set('l', String(st.lambda));
    if (!st.mmr) q.set('mmr', '0');
    if (st.floors) q.set('f', '1');
    if (st.limit !== null) q.set('n', String(st.limit));
    if (st.maxChars !== null) q.set('c', String(st.maxChars));
    if (st.maxTokens) q.set('t', String(st.maxTokens));
    if (st.minOverlap !== null) q.set('o', String(st.minOverlap));
    if (st.tags) q.set('tags', st.tags);
    if (st.project) q.set('proj', st.project);
    try {
      history.replaceState(null, '', '#' + q.toString());
    } catch (_) {
      /* ignore */
    }
  }

  function render() {
    renderControls();
    renderStage();
    renderMatrix();
    writeHash();
  }

  function go(i) {
    st.stage = Math.min(STAGES.length - 1, Math.max(0, i));
    render();
  }

  stageSeg.querySelectorAll('.xp-stagetab').forEach((b) => b.addEventListener('click', () => go(parseInt(b.dataset.i, 10))));
  stageSeg.addEventListener('keydown', (e) => {
    const k = e.key;
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(k)) return;
    e.preventDefault();
    const n = k === 'Home' ? 0 : k === 'End' ? STAGES.length - 1 : st.stage + (k === 'ArrowRight' ? 1 : -1);
    go(n);
    const t = stageSeg.querySelectorAll('.xp-stagetab')[st.stage];
    if (t) t.focus();
  });
  scrub.addEventListener('input', () => go(parseInt(scrub.value, 10)));
  btnPrev.addEventListener('click', () => go(st.stage - 1));
  btnNext.addEventListener('click', () => go(st.stage + 1));

  let timer = null;
  function submit() {
    st.query = input.value.trim() || st.query;
    recompute();
  }
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    submit();
  });
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(submit, 250);
  });
  chips.querySelectorAll('[data-q]').forEach((b) =>
    b.addEventListener('click', () => {
      input.value = b.dataset.q;
      st.query = b.dataset.q;
      recompute();
    }),
  );

  buildSettings();
  recompute();
}

const root = document.getElementById('xp-recall-walkthrough');
const dataEl = document.getElementById('xp-recall-corpus');
if (root && dataEl) init(root, JSON.parse(dataEl.textContent));
