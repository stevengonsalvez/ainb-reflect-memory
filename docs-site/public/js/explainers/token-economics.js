import { h, svg, clear, copyText } from './dom.js';
import { FIELDS, PRICES, CACHE_READ, CACHE_WRITE, DAYS_PER_MONTH, defaults, compute, calibration, extractEntry, agenticEntry, sliceTokens } from './token-model.js';


const PRESETS = [
  { id: 'light', label: 'Light', set: { sessionsPerDay: 2, promptsPerSession: 12, transcriptKB: 150 } },
  { id: 'default', label: 'Default', set: {} },
  { id: 'heavy', label: 'Heavy', set: { sessionsPerDay: 20, promptsPerSession: 60, transcriptKB: 1200, drainRuns: 20 } },
  { id: 'agentic', label: 'Legacy agentic writer', set: { writer: 'agentic' } },
  { id: 'opus', label: 'Opus drain', set: { drainModel: 'opus' } },
];

const SRC = {
  code: { label: 'code', title: 'Default read from the shipped code or config' },
  measured: { label: 'measured', title: 'Number reported in plugin/CHANGELOG.md' },
  assumption: { label: 'assumption', title: 'No value in the repo. Edit it to match your usage.' },
};

function fmtUSD(v) {
  const a = Math.abs(v);
  const s = a >= 100 ? a.toFixed(0) : a >= 1 ? a.toFixed(2) : a >= 0.01 ? a.toFixed(3) : a === 0 ? '0.00' : a.toFixed(4);
  return (v < 0 ? '-$' : '$') + s;
}
function fmtTok(v) {
  const a = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (a >= 1e6) return sign + (a / 1e6).toFixed(a >= 1e7 ? 1 : 2) + 'M';
  if (a >= 1e3) return sign + (a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'K';
  return sign + Math.round(a);
}
function fmtNum(v) {
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
}
function fmtVal(f, v) {
  if (f.type === 'check') return v ? 'on' : 'off';
  if (f.type === 'select') return PRICES[v] ? PRICES[v].label : v;
  if (f.type === 'writer') return v;
  return f.pct ? Math.round(v * 100) + '%' : fmtNum(v);
}

function init(root) {
  const DEF = defaults();
  let p = { ...DEF };
  let metric = 'usd-day';

  // ---- URL state
  function readHash() {
    const q = new URLSearchParams(location.hash.replace(/^#/, ''));
    for (const f of FIELDS) {
      if (!q.has(f.key)) continue;
      const raw = q.get(f.key);
      if (f.type === 'check') p[f.key] = raw === '1';
      else if (f.type === 'select') p[f.key] = PRICES[raw] ? raw : DEF[f.key];
      else if (f.type === 'writer') p[f.key] = raw === 'agentic' ? 'agentic' : 'extract';
      else {
        const n = parseFloat(raw);
        if (Number.isFinite(n)) p[f.key] = clamp(f, n);
      }
    }
    if (q.get('m')) metric = ['usd-day', 'usd-month', 'tok-day'].includes(q.get('m')) ? q.get('m') : metric;
  }
  function hashString() {
    const q = new URLSearchParams();
    for (const f of FIELDS) {
      if (p[f.key] === DEF[f.key]) continue;
      q.set(f.key, f.type === 'check' ? (p[f.key] ? '1' : '0') : String(p[f.key]));
    }
    if (metric !== 'usd-day') q.set('m', metric);
    return q.toString();
  }
  function writeHash() {
    try {
      const s = hashString();
      history.replaceState(null, '', s ? '#' + s : location.pathname + location.search);
    } catch (_) {
      /* ignore */
    }
  }
  function clamp(f, n) {
    if (f.min !== undefined && n < f.min) return f.min;
    if (f.max !== undefined && n > f.max) return f.max;
    return n;
  }

  // ---- skeleton
  const presetBar = h('div', { class: 'xp-toolbar' }, h('div', { class: 'xp-presets', role: 'group', 'aria-label': 'Presets' }, PRESETS.map((pr) => h('button', { type: 'button', class: 'xp-btn', 'data-preset': pr.id, text: pr.label }))), h('button', { type: 'button', class: 'xp-btn', id: 'xp-reset', text: 'Reset all' }));
  const kpis = h('div', { class: 'xp-kpis', 'aria-live': 'polite' });
  const chartCard = h('div', { class: 'xp-card' });
  const drainCard = h('div', { class: 'xp-card' });
  const inputs = h('div', { class: 'xp-inputs' });
  const calCard = h('details', { class: 'xp-card xp-cal' });
  const summaryCard = h('div', { class: 'xp-card' });

  root.append(presetBar, kpis, chartCard, inputs, drainCard, calCard, summaryCard);

  presetBar.querySelectorAll('[data-preset]').forEach((b) =>
    b.addEventListener('click', () => {
      const pr = PRESETS.find((x) => x.id === b.dataset.preset);
      p = { ...DEF, ...pr.set };
      syncInputs();
      update();
    }),
  );
  presetBar.querySelector('#xp-reset').addEventListener('click', () => {
    p = { ...DEF };
    metric = 'usd-day';
    syncInputs();
    update();
  });

  // ---- inputs
  const controls = {}; // key -> { range?, num?, sel?, chk?, out }
  const groups = [...new Set(FIELDS.map((f) => f.group))];
  for (const g of groups) {
    const fields = FIELDS.filter((f) => f.group === g);
    const main = fields.filter((f) => !f.adv);
    const adv = fields.filter((f) => f.adv);
    const fs = h('fieldset', { class: 'xp-fieldset' }, h('legend', { text: g }), main.map(buildField));
    if (adv.length) {
      fs.append(h('details', { class: 'xp-adv' }, h('summary', { text: 'Advanced assumptions (' + adv.length + ')' }), adv.map(buildField)));
    }
    inputs.append(fs);
  }

  function buildField(f) {
    const id = 'xp-f-' + f.key;
    const badge = h('span', { class: 'xp-src src-' + f.src, title: SRC[f.src].title, text: SRC[f.src].label });
    const why = f.ref || f.note ? h('details', { class: 'xp-why' }, h('summary', { text: f.src === 'code' ? 'where this comes from' : 'about this value' }), h('p', { text: [f.ref, f.note].filter(Boolean).join(' ') })) : null;
    const wrap = h('div', { class: 'xp-field' });
    const label = h('label', { for: id, class: 'xp-label' }, f.label, ' ', badge);
    const c = {};
    if (f.type === 'select') {
      const sel = h('select', { id }, Object.entries(PRICES).map(([k, v]) => h('option', { value: k, text: v.label + ' ($' + v.in.toFixed(2) + ' in / $' + v.out.toFixed(2) + ' out per 1M)' })));
      sel.addEventListener('change', () => {
        p[f.key] = sel.value;
        update();
      });
      c.sel = sel;
      wrap.append(label, sel);
    } else if (f.type === 'writer') {
      const sel = h('select', { id }, [h('option', { value: 'extract', text: 'extract (single-shot, default since 5.2.5)' }), h('option', { value: 'agentic', text: 'agentic (legacy multi-step loop)' })]);
      sel.addEventListener('change', () => {
        p[f.key] = sel.value;
        update();
      });
      c.sel = sel;
      wrap.append(label, sel);
    } else if (f.type === 'check') {
      const chk = h('input', { type: 'checkbox', id });
      chk.addEventListener('change', () => {
        p[f.key] = chk.checked;
        update();
      });
      c.chk = chk;
      wrap.className = 'xp-field xp-field-check';
      wrap.append(chk, label);
    } else {
      const range = h('input', { type: 'range', class: 'xp-range', min: f.min, max: f.max, step: f.step, 'aria-label': f.label, tabindex: '-1' });
      const num = h('input', { type: 'number', id, min: f.min, max: f.max, step: f.step, inputmode: 'decimal' });
      const scale = f.pct ? 100 : 1;
      if (f.pct) {
        num.min = String(f.min * 100);
        num.max = String(f.max * 100);
        num.step = String(Math.max(1, Math.round(f.step * 100)));
        range.min = String(f.min * 100);
        range.max = String(f.max * 100);
        range.step = String(Math.max(1, Math.round(f.step * 100)));
      }
      range.addEventListener('input', () => {
        p[f.key] = parseFloat(range.value) / scale;
        num.value = fmtNum(parseFloat(range.value));
        update();
      });
      num.addEventListener('input', () => {
        const n = parseFloat(num.value);
        if (!Number.isFinite(n)) return;
        p[f.key] = clamp(f, n / scale);
        range.value = String(p[f.key] * scale);
        update();
      });
      num.addEventListener('change', () => {
        num.value = fmtNum(p[f.key] * scale);
      });
      c.range = range;
      c.num = num;
      c.scale = scale;
      wrap.append(label, h('div', { class: 'xp-pair' }, range, num, f.pct ? h('span', { class: 'xp-unit', text: '%' }) : null));
    }
    if (why) wrap.append(why);
    controls[f.key] = c;
    return wrap;
  }

  function syncInputs() {
    for (const f of FIELDS) {
      const c = controls[f.key];
      const v = p[f.key];
      if (c.sel) c.sel.value = v;
      else if (c.chk) c.chk.checked = !!v;
      else {
        c.range.value = String(v * c.scale);
        c.num.value = fmtNum(Math.round(v * c.scale * 1000) / 1000);
      }
    }
  }

  // ---- results
  function renderKpis(r) {
    const k = (title, value, sub, cls) =>
      h('div', { class: 'xp-kpi ' + (cls || '') }, h('div', { class: 'xp-eyebrow', text: title }), h('div', { class: 'xp-kpi-v', text: value }), h('div', { class: 'xp-muted xp-kpi-s', text: sub }));
    const mo = r.days;
    clear(kpis).append(
      k('LLM cost per day', fmtUSD(r.total.costDay), fmtUSD(r.total.costDay * mo) + ' per month'),
      k('Est. value of tokens saved', fmtUSD(r.savings.costDay), fmtTok(r.savings.tokensDay) + ' tokens/day'),
      k('Net per month', fmtUSD(r.total.netDay * mo), r.total.netDay >= 0 ? 'savings exceed cost' : 'cost exceeds modeled savings', r.total.netDay >= 0 ? 'is-good' : 'is-bad'),
      k('Break-even usefulness', Number.isFinite(r.total.breakEvenUseful) ? Math.round(r.total.breakEvenUseful * 100) + '%' : 'n/a', 'of injected learnings must save a rediscovery'),
    );
  }

  function barRows(r) {
    const mo = r.days;
    if (metric === 'tok-day') {
      return [
        { label: 'Capture', v: r.capture.tokensDay, kind: 'cost', note: 'no model calls' },
        { label: 'Drain', v: r.drain.tokensDay, kind: 'cost' },
        { label: 'Recall (incl. re-reads)', v: r.recall.tokensDay, kind: 'cost' },
        { label: 'Est. saved', v: r.savings.tokensDay, kind: 'save' },
      ].map((x) => ({ ...x, text: fmtTok(x.v) }));
    }
    const scale = metric === 'usd-month' ? mo : 1;
    const rows = [
      { label: 'Capture', v: r.capture.costDay * scale, kind: 'cost', note: 'no model calls' },
      { label: 'Drain', v: r.drain.costDay * scale, kind: 'cost' },
      { label: 'Recall', v: r.recall.costDay * scale, kind: 'cost' },
      { label: 'Est. saved', v: r.savings.costDay * scale, kind: 'save' },
      { label: 'Net', v: r.total.netDay * scale, kind: r.total.netDay >= 0 ? 'save' : 'loss' },
    ];
    return rows.map((x) => ({ ...x, text: fmtUSD(x.v) }));
  }

  function renderChart(r) {
    const rows = barRows(r);
    const W = 640;
    const labelW = 150;
    const pad = 70;
    const rowH = 36;
    const H = rows.length * rowH + 12;
    const max = Math.max(1e-9, ...rows.map((x) => Math.abs(x.v)));
    const hasNeg = rows.some((x) => x.v < 0);
    const areaW = W - labelW - pad;
    const zero = labelW + (hasNeg ? areaW * 0.35 : 0);
    const span = hasNeg ? areaW * 0.65 : areaW;
    const negSpan = areaW * 0.35;
    const g = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'xp-chart', role: 'img', 'aria-label': 'Bar chart of ' + rows.map((x) => x.label + ' ' + x.text).join(', ') });
    rows.forEach((x, i) => {
      const y = 6 + i * rowH;
      const w = (Math.abs(x.v) / max) * (x.v < 0 ? negSpan : span);
      const bx = x.v < 0 ? zero - w : zero;
      g.append(
        svg('text', { x: labelW - 10, y: y + 20, class: 'xp-chart-label', 'text-anchor': 'end', text: x.label }),
        svg('rect', { x: bx, y: y + 5, width: Math.max(w, x.v === 0 ? 0 : 1.5), height: 18, rx: 3, class: 'xp-bar xp-bar-' + x.kind }),
        svg('text', { x: (x.v < 0 ? bx : bx + w) + (x.v < 0 ? -6 : 6), y: y + 19, class: 'xp-chart-val', 'text-anchor': x.v < 0 ? 'end' : 'start', text: x.text + (x.note ? '  (' + x.note + ')' : '') }),
      );
    });
    g.append(svg('line', { x1: zero, x2: zero, y1: 2, y2: H - 4, class: 'xp-chart-axis' }));
    const seg = h('div', { class: 'xp-segmented', role: 'radiogroup', 'aria-label': 'Chart metric' }, [
      ['usd-day', '$ / day'],
      ['usd-month', '$ / month'],
      ['tok-day', 'tokens / day'],
    ].map(([id, t]) => h('button', { type: 'button', role: 'radio', class: 'xp-seg', 'aria-checked': metric === id ? 'true' : 'false', 'data-m': id, text: t })));
    seg.querySelectorAll('button').forEach((b) =>
      b.addEventListener('click', () => {
        metric = b.dataset.m;
        update();
        const nb = chartCard.querySelector('[data-m="' + metric + '"]');
        if (nb) nb.focus();
      }),
    );
    clear(chartCard).append(h('div', { class: 'xp-toolbar' }, h('h4', { text: 'Cost of reflect vs estimated savings' }), seg), g, h('p', { class: 'xp-note', text: 'Capture is $0: the hooks are shell commands, the enqueue gate is regex, mini-learnings are written without a model call. Savings are modeled, not measured.' }));
  }

  function renderDrain(r) {
    const d = r.drain;
    const slice = sliceTokens(p);
    const ex = extractEntry(p, slice);
    const ag = agenticEntry(p, slice);
    const row = (a, b, c) => h('tr', null, h('th', { scope: 'row', text: a }), h('td', { text: b }), h('td', { text: c }));
    const model = PRICES[p.drainModel].label;
    clear(drainCard).append(
      h('h4', { text: 'Drain detail' }),
      h('p', { class: 'xp-note', text: model + ' drain. Slice ' + fmtTok(slice) + ' tokens (min of ' + p.sliceCapChars / 1000 + 'K chars cap and ' + Math.round(p.sliceRatio * 100) + '% of ' + p.transcriptKB + ' KB), plus about ' + fmtTok(p.baselineTokens) + ' tokens of baseline context.' }),
      h('table', { class: 'xp-table' },
        h('thead', null, h('tr', null, h('th', { text: '' }), h('th', { text: 'extract (default)' }), h('th', { text: 'agentic (legacy)' }))),
        h('tbody', null,
          row('Model turns per transcript', '1', String(ag.turns)),
          row('Tokens per transcript', fmtTok(ex.tokens), fmtTok(ag.tokens)),
          row('Cost per transcript', fmtUSD(ex.cost), fmtUSD(ag.cost)),
          row('Cost ratio', '1x', (ag.cost / Math.max(ex.cost, 1e-9)).toFixed(1) + 'x'),
        ),
      ),
      h('p', { class: 'xp-note' },
        'Per day: ' + fmtNum(Math.round(r.capture.enqueuedDay * 100) / 100) + ' transcripts enqueued, drain capacity ' + d.capacity + ' (min of daily cap ' + p.dailyMax + ' and ' + p.drainRuns + ' runs x ' + p.perRunMax + '). ',
        d.backlogGrowthDay > 0 ? h('strong', { class: 'xp-bad', text: 'Backlog grows by ' + fmtNum(Math.round(d.backlogGrowthDay * 10) / 10) + ' entries/day: raise the cap or run more drains.' }) : 'Capacity covers the enqueue rate, so the queue clears. ',
        ' Expect about ' + fmtNum(Math.round(d.learningsDay * 10) / 10) + ' learnings written per day.',
      ),
    );
  }

  function renderCal() {
    const rows = calibration();
    clear(calCard).append(
      h('summary', null, 'Model check against measured runs'),
      h('p', { class: 'xp-note', text: 'The model uses cache-read at ' + CACHE_READ + 'x and cache-write at ' + CACHE_WRITE + 'x the input price. Run on the two measurements in plugin/CHANGELOG.md:' }),
      h('table', { class: 'xp-table' },
        h('thead', null, h('tr', null, h('th', { text: 'Run' }), h('th', { text: 'Modeled' }), h('th', { text: 'Measured' }), h('th', { text: 'Cost delta' }))),
        h('tbody', null, rows.map((x) => h('tr', null, h('th', { scope: 'row', text: x.label }), h('td', { text: fmtTok(x.modeled.tokens) + ' tok, ' + fmtUSD(x.modeled.cost) }), h('td', { text: fmtTok(x.measured.tokens) + ' tok, ' + fmtUSD(x.measured.cost) }), h('td', { text: Math.round((x.modeled.cost / x.measured.cost - 1) * 100) + '%' })))),
      ),
      h('p', { class: 'xp-note', text: 'The model runs low on dollars because real runs also pay for tool schemas and per-call overhead the repo does not itemize. Treat dollar figures as order-of-magnitude, token figures as the firmer ones.' }),
    );
  }

  function summaryText(r) {
    const mo = r.days;
    const non = FIELDS.filter((f) => p[f.key] !== DEF[f.key]).map((f) => '  - ' + f.label + ': ' + fmtVal(f, p[f.key]) + ' (default ' + fmtVal(f, DEF[f.key]) + ')');
    const lines = [
      'reflect token economics (modeled, ' + mo + '-day month)',
      '',
      'Usage: ' + p.sessionsPerDay + ' sessions/day x ' + p.promptsPerSession + ' prompts, session model ' + PRICES[p.sessionModel].label,
      'Drain: ' + PRICES[p.drainModel].label + ', ' + p.writer + ' writer, ' + p.transcriptKB + ' KB transcripts, ' + fmtNum(Math.round(r.capture.enqueuedDay * 100) / 100) + ' enqueued/day (capacity ' + r.drain.capacity + '/day)',
      '',
      'Capture: ' + fmtUSD(0) + '/day (hooks and gate make no model calls)',
      'Drain:   ' + fmtUSD(r.drain.costDay) + '/day, ' + fmtUSD(r.drain.costDay * mo) + '/month, ' + fmtTok(r.drain.tokensDay) + ' tokens/day',
      'Recall:  ' + fmtUSD(r.recall.costDay) + '/day, ' + fmtUSD(r.recall.costDay * mo) + '/month, ' + fmtTok(r.recall.tokensDay) + ' tokens/day (' + fmtTok(r.recall.injectedTokensDay) + ' injected)',
      'Total:   ' + fmtUSD(r.total.costDay) + '/day, ' + fmtUSD(r.total.costDay * mo) + '/month',
      '',
      'Est. saved: ' + fmtTok(r.savings.tokensDay) + ' tokens/day = ' + fmtUSD(r.savings.costDay) + '/day (' + Math.round(p.usefulRate * 100) + '% of ' + fmtNum(Math.round(r.recall.injectedLearningsDay)) + ' injected learnings assumed useful, ' + p.discoveryTokens + ' tokens each)',
      'Net: ' + fmtUSD(r.total.netDay * mo) + '/month; break-even usefulness ' + (Number.isFinite(r.total.breakEvenUseful) ? Math.round(r.total.breakEvenUseful * 100) + '%' : 'n/a'),
      '',
      'Prices (USD per 1M tokens): Haiku 0.80 in / 4.00 out, Sonnet 3.00 / 15.00, Opus 15.00 / 75.00; cache read ' + CACHE_READ + 'x, write ' + CACHE_WRITE + 'x.',
      non.length ? 'Changed from defaults:' : 'All inputs at defaults.',
      ...non,
    ];
    return lines.join('\n');
  }

  function renderSummary(r) {
    const text = summaryText(r);
    const pre = h('pre', { class: 'xp-summary', tabindex: '0', text });
    const status = h('span', { class: 'xp-muted', role: 'status', 'aria-live': 'polite' });
    const btn = h('button', { type: 'button', class: 'xp-btn xp-btn-primary', text: 'Copy summary' });
    const link = h('button', { type: 'button', class: 'xp-btn', text: 'Copy link to these inputs' });
    btn.addEventListener('click', async () => {
      status.textContent = (await copyText(text)) ? ' Copied.' : ' Copy failed, select the text manually.';
    });
    link.addEventListener('click', async () => {
      writeHash();
      status.textContent = (await copyText(location.href)) ? ' Link copied.' : ' Copy failed.';
    });
    clear(summaryCard).append(h('div', { class: 'xp-toolbar' }, h('h4', { text: 'Summary' }), h('div', { class: 'xp-controls' }, btn, link)), status, pre);
  }

  let first = true;
  function update() {
    const r = compute(p);
    renderKpis(r);
    renderChart(r);
    renderDrain(r);
    if (first) renderCal();
    renderSummary(r);
    writeHash();
    first = false;
  }

  readHash();
  syncInputs();
  update();
}

const root = document.getElementById('xp-token-economics');
if (root) init(root);
