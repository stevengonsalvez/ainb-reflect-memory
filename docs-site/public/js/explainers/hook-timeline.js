import { h, clear, prefersReducedMotion } from './dom.js';
import { LANES, HARNESSES, HOOKS, STEPS, simulate, unscripted, REPO_BLOB } from './hook-data.js';


function init(root) {
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));
  const startHarness = HARNESSES[params.get('h')] ? params.get('h') : 'claude';
  const startStep = Math.min(STEPS.length - 1, Math.max(0, parseInt(params.get('s') || '0', 10) || 0));

  const st = { harness: startHarness, step: startStep, playing: false, timer: null };
  const reduced = prefersReducedMotion();

  const harnessNames = Object.keys(HARNESSES);
  const radios = harnessNames.map((id) =>
    h('button', {
      type: 'button',
      role: 'radio',
      class: 'xp-seg',
      'data-id': id,
      'aria-checked': 'false',
      tabindex: '-1',
      text: HARNESSES[id].label,
    }),
  );
  const seg = h('div', { class: 'xp-segmented', role: 'radiogroup', 'aria-label': 'Harness' }, radios);

  const btnPrev = h('button', { type: 'button', class: 'xp-btn', 'aria-label': 'Previous step', text: 'Prev' });
  const btnPlay = h('button', { type: 'button', class: 'xp-btn xp-btn-primary', 'aria-pressed': 'false', text: 'Play' });
  const btnNext = h('button', { type: 'button', class: 'xp-btn', 'aria-label': 'Next step', text: 'Next' });
  const scrub = h('input', {
    type: 'range',
    class: 'xp-range',
    min: '0',
    max: String(STEPS.length - 1),
    step: '1',
    value: String(st.step),
    'aria-label': 'Session step',
  });
  const scrubLabel = h('div', { class: 'xp-scrub-label', 'aria-hidden': 'true' });

  const matrixWrap = h('div', { class: 'xp-matrix-wrap', tabindex: '0', role: 'region', 'aria-label': 'Hook lanes by session step (scrollable)' });
  const detail = h('div', { class: 'xp-card xp-detail', 'aria-live': 'polite' });
  const stateBox = h('div', { class: 'xp-card xp-state' });
  const footer = h('p', { class: 'xp-note' });

  root.append(
    h('div', { class: 'xp-toolbar' }, seg, h('div', { class: 'xp-controls' }, btnPrev, btnPlay, btnNext)),
    h('div', { class: 'xp-scrub' }, scrub, scrubLabel),
    matrixWrap,
    h('div', { class: 'xp-grid-2' }, detail, stateBox),
    footer,
  );

  function sim() {
    return simulate(st.harness);
  }

  function renderMatrix(rows) {
    const hdef = HARNESSES[st.harness];
    const table = h('table', { class: 'xp-matrix' });
    const thead = h('thead');
    const head = h('tr');
    head.append(h('th', { scope: 'col', class: 'xp-corner', text: 'Lane' }));
    rows.forEach((r, i) => {
      const evNames = r.fires.map((e) => hdef.names[e]);
      const absent = r.fires.length === 0;
      const b = h(
        'button',
        {
          type: 'button',
          class: 'xp-step-btn' + (absent ? ' is-absent' : ''),
          'data-i': String(i),
          'aria-current': i === st.step ? 'step' : null,
          title: absent ? 'No hook wired for this moment in ' + hdef.label : evNames.join(' + '),
        },
        h('span', { class: 'xp-step-n', text: String(i + 1) }),
        h('span', { class: 'xp-step-l', text: r.step.label }),
        h('span', { class: 'xp-step-es' }, absent ? h('code', { class: 'xp-step-e', text: 'not wired' }) : evNames.map((n) => h('code', { class: 'xp-step-e', text: n }))),
      );
      head.append(h('th', { scope: 'col', class: i === st.step ? 'is-current' : (i < st.step ? 'is-past' : '') }, b));
    });
    thead.append(head);
    const tbody = h('tbody');
    for (const lane of LANES) {
      const tr = h('tr');
      tr.append(h('th', { scope: 'row', class: 'xp-lane-h' }, h('span', { class: 'xp-lane-dot lane-' + lane.id }), h('span', { text: lane.label })));
      rows.forEach((r, i) => {
        const hits = r.fires.filter((e) => {
          const lanes = HOOKS[e].lanes;
          if (lane.id === 'drain') return r.step.drain && lanes.includes('drain');
          return lanes.includes(lane.id);
        });
        const td = h('td', { class: (i === st.step ? 'is-current ' : '') + (i < st.step ? 'is-past' : '') });
        if (hits.length) {
          const label = hits.map((e) => hdef.names[e]).join(', ') + ': ' + lane.label;
          td.append(h('span', { class: 'xp-dot lane-' + lane.id, role: 'img', 'aria-label': label, title: label }));
        } else if (r.missing.length && lane.id === 'capture' && r.fires.length === 0) {
          td.append(h('span', { class: 'xp-gap', 'aria-label': 'not wired', title: 'not wired in ' + hdef.label, text: '/' }));
        }
        tr.append(td);
      });
      tbody.append(tr);
    }
    table.append(thead, tbody);
    clear(matrixWrap).append(table);
    table.querySelectorAll('.xp-step-btn').forEach((b) =>
      b.addEventListener('click', () => {
        stop();
        go(parseInt(b.dataset.i, 10));
      }),
    );
  }

  function renderDetail(rows) {
    const r = rows[st.step];
    const hdef = HARNESSES[st.harness];
    clear(detail);
    detail.append(
      h('div', { class: 'xp-eyebrow', text: 'Step ' + (st.step + 1) + ' of ' + STEPS.length }),
      h('h4', { class: 'xp-h', text: r.step.label }),
      h('p', { class: 'xp-story', text: r.step.story }),
    );
    if (r.fires.length === 0) {
      detail.append(
        h('p', { class: 'xp-warn' }, h('strong', { text: 'No hook fires here in ' + hdef.label + '. ' }), 'The wiring file (' + hdef.config + ') has no entry for this moment.'),
      );
    }
    for (const e of r.fires) {
      const hk = HOOKS[e];
      detail.append(
        h(
          'div',
          { class: 'xp-hookrow' },
          h('div', { class: 'xp-hookname' }, h('code', { text: hdef.names[e] }), ' ', ...hk.lanes.filter((l) => l !== 'drain' || r.step.drain).map((l) => h('span', { class: 'xp-chip lane-' + l, text: LANES.find((x) => x.id === l).label }))),
          h('p', { text: hk.does }),
          h('p', { class: 'xp-scripts' }, ...hk.scripts.flatMap((s, i) => [i ? ', ' : '', h('a', { href: REPO_BLOB + s, rel: 'noopener', text: s })])),
        ),
      );
    }
    if (r.step.id === 'next') {
      detail.append(
        h('p', { class: 'xp-note' }, 'reflect-drain-bg.sh takes a lock, gates and slices each queued transcript, then runs the single-shot extract writer on the model in REFLECT_DRAIN_MODEL (default sonnet). See the token economics explainer for what that costs.'),
      );
    }
    for (const e of r.missing) {
      const why = missingNote(st.harness, e);
      detail.append(h('p', { class: 'xp-warn' }, h('strong', { text: (HARNESSES.claude.names[e] || e) + ' is not wired in ' + hdef.label + '. ' }), why));
    }
  }

  function missingNote(harness, ev) {
    const m = {
      notification: 'Codex has no Notification entry, so permission prompts are only caught through PermissionRequest.',
      postToolFail: 'Codex has no PostToolUseFailure entry; PostToolUse arms the failure watcher instead.',
      postCompact: 'Copilot has no postCompact entry; nothing is lost, the hook is bookkeeping only.',
      sessionEnd: 'Codex has no SessionEnd entry. Stop still enqueues the transcript, so the drain does not depend on it.',
    };
    return m[ev] || 'Not in the harness wiring file.';
  }

  function renderState(rows) {
    const s = rows[st.step].state;
    clear(stateBox);
    const row = (k, v, hint) =>
      h('div', { class: 'xp-kv' }, h('dt', { text: k }), h('dd', null, h('strong', { text: v }), hint ? h('span', { class: 'xp-muted', text: ' ' + hint }) : null));
    stateBox.append(
      h('div', { class: 'xp-eyebrow', text: 'Session state after this step (illustrative counts)' }),
      h(
        'dl',
        { class: 'xp-kvs' },
        row('Learnings injected', String(s.injected), '~' + s.injectedTokens + ' tokens'),
        row('Failure watcher armed', s.armed ? 'yes' : 'no', '~/.reflect/armed'),
        row('Permission watcher armed', s.permArmed ? 'yes' : 'no', '~/.reflect/permission-armed'),
        row('Mini-learnings written', String(s.mini), 'no LLM call'),
        row('Slots updated', s.slots ? 'yes' : 'no', 'REFLECT_SLOTS'),
        row('Queue entries (this session)', String(s.queue), 'deduped by session id'),
        row('Transcripts drained', String(s.drained)),
      ),
    );
  }

  function renderFooter() {
    const hdef = HARNESSES[st.harness];
    const names = Object.keys(hdef.names).length;
    const extra = unscripted(st.harness);
    clear(footer).append(
      h('strong', { text: hdef.label + ': ' }),
      names + ' hook events wired in ',
      h('a', { href: REPO_BLOB + hdef.config, rel: 'noopener' }, h('code', { text: hdef.config })),
      extra.length ? '. Wired but not in this script: ' + extra.map((e) => hdef.names[e]).join(', ') + '.' : '.',
    );
  }

  function renderControls() {
    radios.forEach((r) => {
      const on = r.dataset.id === st.harness;
      r.setAttribute('aria-checked', on ? 'true' : 'false');
      r.tabIndex = on ? 0 : -1;
    });
    scrub.value = String(st.step);
    scrub.setAttribute('aria-valuetext', STEPS[st.step].label);
    scrubLabel.textContent = st.step + 1 + ' / ' + STEPS.length + ': ' + STEPS[st.step].label;
    btnPlay.textContent = st.playing ? 'Pause' : 'Play';
    btnPlay.setAttribute('aria-pressed', st.playing ? 'true' : 'false');
    detail.setAttribute('aria-live', st.playing ? 'off' : 'polite');
    btnPrev.disabled = st.step === 0;
    btnNext.disabled = st.step === STEPS.length - 1;
  }

  function render() {
    const rows = sim();
    renderControls();
    renderMatrix(rows);
    renderDetail(rows);
    renderState(rows);
    renderFooter();
    try {
      history.replaceState(null, '', '#h=' + st.harness + '&s=' + st.step);
    } catch (_) {
      /* ignore */
    }
    const cur = matrixWrap.querySelector('th.is-current');
    if (cur && matrixWrap.scrollTo) {
      const left = cur.offsetLeft - matrixWrap.clientWidth / 2 + cur.clientWidth / 2;
      matrixWrap.scrollTo({ left: Math.max(0, left), behavior: reduced ? 'auto' : 'smooth' });
    }
  }

  function go(i) {
    st.step = Math.min(STEPS.length - 1, Math.max(0, i));
    render();
  }
  function stop() {
    if (st.timer) clearInterval(st.timer);
    st.timer = null;
    st.playing = false;
  }
  function play() {
    if (st.step >= STEPS.length - 1) st.step = 0;
    st.playing = true;
    render();
    st.timer = setInterval(() => {
      if (st.step >= STEPS.length - 1) {
        stop();
        render();
        return;
      }
      go(st.step + 1);
    }, 1600);
  }

  btnPlay.addEventListener('click', () => (st.playing ? (stop(), render()) : play()));
  btnPrev.addEventListener('click', () => {
    stop();
    go(st.step - 1);
  });
  btnNext.addEventListener('click', () => {
    stop();
    go(st.step + 1);
  });
  scrub.addEventListener('input', () => {
    stop();
    go(parseInt(scrub.value, 10));
  });
  radios.forEach((r) => r.addEventListener('click', () => setHarness(r.dataset.id)));
  seg.addEventListener('keydown', (e) => {
    const keys = ['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End'];
    if (!keys.includes(e.key)) return;
    e.preventDefault();
    let i = harnessNames.indexOf(st.harness);
    if (e.key === 'Home') i = 0;
    else if (e.key === 'End') i = harnessNames.length - 1;
    else i = (i + (e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1) + harnessNames.length) % harnessNames.length;
    setHarness(harnessNames[i]);
    radios[i].focus();
  });
  function setHarness(id) {
    st.harness = id;
    render();
  }

  render();
}

const root = document.getElementById('xp-hook-timeline');
if (root) init(root);
