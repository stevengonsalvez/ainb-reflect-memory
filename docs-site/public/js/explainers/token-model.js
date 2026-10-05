// Pure cost model for the token-economics explainer. No DOM, importable from node.
//
// Every default below is tagged with where it comes from:
//   code        read from the shipped code/config (path in `ref`)
//   measured    a number reported in plugin/CHANGELOG.md
//   assumption  no value exists in the repo; editable, labelled in the UI

export const PRICES = {
  haiku: { label: 'Haiku', in: 0.8, out: 4.0 },
  sonnet: { label: 'Sonnet', in: 3.0, out: 15.0 },
  opus: { label: 'Opus', in: 15.0, out: 75.0 },
};

// Anthropic prompt-cache multipliers on the input price (also quoted in
// plugin/scripts/reflect_cost.py: cache_read 0.1x, cache_creation 1.25-2x).
export const CACHE_READ = 0.1;
export const CACHE_WRITE = 1.25;

export const DAYS_PER_MONTH = 30;

// key -> { v: default, src, ref, ... UI metadata }
export const FIELDS = [
  // ---- usage
  { key: 'sessionsPerDay', group: 'Usage', label: 'Sessions per day', v: 6, min: 1, max: 60, step: 1, src: 'assumption', note: 'How many agent sessions you start per day.' },
  { key: 'promptsPerSession', group: 'Usage', label: 'Prompts per session', v: 25, min: 1, max: 200, step: 1, src: 'assumption', note: 'User prompts per session. Each one runs the UserPromptSubmit recall hook.' },
  { key: 'sessionModel', group: 'Usage', label: 'Session model', v: 'sonnet', type: 'select', src: 'assumption', note: 'The model that reads the injected learnings.' },

  // ---- recall
  { key: 'startLearnings', group: 'Recall', label: 'Learnings injected at SessionStart', v: 3, min: 0, max: 10, step: 1, src: 'code', ref: 'SESSION_START_LIMIT = 3 in plugin/skills/recall/hooks/session_start_recall.py' },
  { key: 'learningsPerPrompt', group: 'Recall', label: 'New learnings injected per prompt (average)', v: 1, min: 0, max: 2, step: 0.1, src: 'assumption', ref: 'USER_PROMPT_LIMIT = 3 blocks in user_prompt_submit_recall.py, but the markdown header line counts as one block, so at most 2 learnings per prompt (verified by calling filter_to_new). The hook also skips ids already injected this session, so the average sits lower.' },
  { key: 'tokensPerLearning', group: 'Recall', label: 'Tokens per injected learning', v: 110, min: 30, max: 400, step: 5, src: 'assumption', ref: 'The per-prompt block is capped at 1500 chars (USER_PROMPT_MAX_CHARS), about 375 tokens for up to 3 learnings, so roughly 125 tokens each is the ceiling (about 190 each when only 2 fit).' },
  { key: 'persist', group: 'Recall', label: 'Count re-reads of injected text on later prompts (prompt cache)', v: true, type: 'check', src: 'assumption', note: 'Injected context stays in the conversation, so every later prompt re-reads it at the cache-read price.' },

  // ---- capture + drain
  { key: 'gatePass', group: 'Capture and drain', label: 'Share of sessions the enqueue gate lets through', v: 0.4, min: 0, max: 1, step: 0.05, pct: true, src: 'assumption', ref: 'The gate (plugin/scripts/reflect_gate.py) skips clean no-signal sessions and reflect-on-reflect transcripts. The repo records no pass rate.' },
  { key: 'drainRuns', group: 'Capture and drain', label: 'Drain runs per day', v: 6, min: 1, max: 144, step: 1, src: 'assumption', ref: 'Each SessionStart spawns a detached drain; REFLECT_DRAIN_DEBOUNCE_SEC = 600 caps this at 144/day.' },
  { key: 'perRunMax', group: 'Capture and drain', label: 'Entries per drain run (REFLECT_DRAIN_MAX)', v: 3, min: 1, max: 20, step: 1, src: 'code', ref: 'MAX_PER_RUN default 3 in plugin/hooks/reflect-drain-bg.sh' },
  { key: 'dailyMax', group: 'Capture and drain', label: 'Entries per day cap (REFLECT_DRAIN_DAILY_MAX)', v: 20, min: 1, max: 100, step: 1, src: 'code', ref: 'DAILY_MAX default 20 in plugin/hooks/reflect-drain-bg.sh' },
  { key: 'drainModel', group: 'Capture and drain', label: 'Drain model (REFLECT_DRAIN_MODEL)', v: 'sonnet', type: 'select', src: 'code', ref: 'DRAIN_MODEL default sonnet in reflect-drain-bg.sh; Opus is reserved for escalation and the weekly synthesis pass.' },
  { key: 'writer', group: 'Capture and drain', label: 'Writer (REFLECT_DRAIN_WRITER)', v: 'extract', type: 'writer', src: 'code', ref: 'DRAIN_WRITER default extract since 5.2.5 (plugin/CHANGELOG.md); agentic is the legacy loop.' },
  { key: 'transcriptKB', group: 'Capture and drain', label: 'Transcript size (KB)', v: 400, min: 20, max: 3000, step: 20, src: 'assumption', note: 'Raw session transcript on disk. The 5.2.4 incident transcripts were about 1500 KB.' },
  { key: 'sliceRatio', group: 'Capture and drain', label: 'Slice size as a share of the transcript', v: 0.1, min: 0.02, max: 1, step: 0.01, pct: true, src: 'code', ref: 'The cascade slices to signal-bearing windows, "~10x smaller" (plugin/hooks/README.md).' },
  { key: 'learningsPerDrain', group: 'Capture and drain', label: 'Learnings written per drained transcript', v: 3, min: 0, max: 12, step: 1, src: 'measured', ref: 'Measured 2 to 3 per transcript (CHANGELOG 5.2.3, 5.2.5). Extract writes at most 12 (_MAX_LEARNINGS in drain_extract.py).' },

  // ---- advanced drain internals
  { key: 'sliceCapChars', group: 'Drain internals', adv: true, label: 'Slice cap (chars)', v: 60000, min: 5000, max: 200000, step: 5000, src: 'code', ref: '_MAX_SLICE_CHARS = 60_000 in plugin/scripts/reflect_cascade.py; REFLECT_DRAIN_MAX_INPUT_CHARS = 60000 in the drain.' },
  { key: 'charsPerToken', group: 'Drain internals', adv: true, label: 'Chars per token', v: 4, min: 2, max: 6, step: 0.5, src: 'code', ref: '_est_tokens = len // 4 in recall.py' },
  { key: 'baselineTokens', group: 'Drain internals', adv: true, label: 'Baseline context of the drain child (tokens)', v: 64000, min: 0, max: 150000, step: 1000, src: 'measured', ref: 'About 64K tokens of the claude -p child is baseline (CHANGELOG 5.2.4).' },
  { key: 'baselineCacheHit', group: 'Drain internals', adv: true, label: 'Share of baseline served from cache', v: 0, min: 0, max: 1, step: 0.1, pct: true, src: 'assumption', note: 'Conservative: 0 means every drain pays the cache-write price on the baseline.' },
  { key: 'outPerLearning', group: 'Drain internals', adv: true, label: 'Output tokens per extracted learning', v: 300, min: 50, max: 1000, step: 10, src: 'assumption' },
  { key: 'agenticTurns', group: 'Drain internals', adv: true, label: 'Agentic writer: turns', v: 16, min: 1, max: 40, step: 1, src: 'code', ref: 'REFLECT_DRAIN_MAX_TURNS = 16 in reflect-drain-bg.sh' },
  { key: 'agenticGrowth', group: 'Drain internals', adv: true, label: 'Agentic writer: context growth per turn (tokens)', v: 27000, min: 1000, max: 40000, step: 1000, src: 'measured', ref: 'Calibrated so 20 turns reproduce the 6.8M tokens in CHANGELOG 5.2.3. The 5.2.5 agentic run (17 turns, 1.5M tokens) implies about 1.3K, so treat this as an upper-ish estimate.' },
  { key: 'agenticOutPerTurn', group: 'Drain internals', adv: true, label: 'Agentic writer: output tokens per turn', v: 500, min: 100, max: 3000, step: 100, src: 'assumption' },
  { key: 'fallbackShare', group: 'Drain internals', adv: true, label: 'Extract entries that fall back to agentic', v: 0, min: 0, max: 1, step: 0.05, pct: true, src: 'code', ref: 'Falls back when no slice exists or the trigger is skill_refresh (CHANGELOG 5.2.5). Rare, so 0 by default.' },

  // ---- savings
  { key: 'discoveryTokens', group: 'Savings', label: 'Tokens to rediscover one learning', v: 1500, min: 200, max: 5000, step: 100, src: 'code', ref: 'DEFAULT_DISCOVERY_TOKENS = 1500; per type: bug-fix 3000, anti-pattern 2500, correction 2000, pattern 1500, decision 1200 (recall.py).' },
  { key: 'usefulRate', group: 'Savings', label: 'Share of injected learnings that actually save a rediscovery', v: 0.25, min: 0, max: 1, step: 0.05, pct: true, src: 'assumption', note: 'recall.py shows saved tokens as if every injected learning were useful (100%). That overstates it; this slider is the honest discount.' },
  { key: 'discoveryOutShare', group: 'Savings', adv: true, label: 'Share of rediscovery tokens that are output tokens', v: 0.2, min: 0, max: 1, step: 0.05, pct: true, src: 'assumption', note: 'Prices the saved tokens at a blend of input and output price.' },
];

export function defaults() {
  const o = {};
  for (const f of FIELDS) o[f.key] = f.v;
  return o;
}

const M = 1e6;

function price(model) {
  return PRICES[model] || PRICES.sonnet;
}

export function sliceTokens(p) {
  const chars = Math.min(p.sliceCapChars, p.transcriptKB * 1024 * p.sliceRatio);
  return Math.round(chars / p.charsPerToken);
}

// One drained transcript, single-shot extract writer.
export function extractEntry(p, slice = sliceTokens(p)) {
  const pr = price(p.drainModel);
  const B = p.baselineTokens;
  const h = p.baselineCacheHit;
  const ctx = B + slice;
  const inCost = (pr.in / M) * ((1 - h) * B * CACHE_WRITE + h * B * CACHE_READ + slice * CACHE_WRITE);
  const outTok = p.learningsPerDrain * p.outPerLearning;
  const outCost = (pr.out / M) * outTok;
  return { turns: 1, tokens: ctx + outTok, cost: inCost + outCost };
}

// One drained transcript, legacy agentic loop: every turn re-sends the whole
// growing conversation (cache read) plus the new turn (cache write).
export function agenticEntry(p, slice = sliceTokens(p)) {
  const pr = price(p.drainModel);
  const T = Math.max(1, Math.round(p.agenticTurns));
  const g = p.agenticGrowth;
  const base = p.baselineTokens + slice;
  let tokens = 0;
  let writes = 0;
  let reads = 0;
  for (let t = 1; t <= T; t += 1) {
    const ctx = base + g * (t - 1);
    tokens += ctx;
    if (t === 1) writes += ctx;
    else {
      writes += g;
      reads += base + g * (t - 2);
    }
  }
  const inCost = (pr.in / M) * (writes * CACHE_WRITE + reads * CACHE_READ);
  const outTok = p.agenticOutPerTurn * T;
  const outCost = (pr.out / M) * outTok;
  return { turns: T, tokens: tokens + outTok, cost: inCost + outCost };
}

export function drainEntry(p) {
  const slice = sliceTokens(p);
  const ex = extractEntry(p, slice);
  const ag = agenticEntry(p, slice);
  if (p.writer === 'agentic') return { ...ag, slice };
  const f = p.fallbackShare;
  return {
    turns: (1 - f) * ex.turns + f * ag.turns,
    tokens: (1 - f) * ex.tokens + f * ag.tokens,
    cost: (1 - f) * ex.cost + f * ag.cost,
    slice,
  };
}

export function compute(p) {
  const sp = price(p.sessionModel);

  // ---- recall: injected context, billed to the session model
  const injectEvents = [{ prompt: 0, tokens: p.startLearnings * p.tokensPerLearning }];
  const perPrompt = p.learningsPerPrompt * p.tokensPerLearning;
  let recallTokensSession = injectEvents[0].tokens + perPrompt * p.promptsPerSession;
  let recallBilledSession = injectEvents[0].tokens; // raw tokens (1x) for the display
  let recallCostSession = 0;
  if (p.persist) {
    // SessionStart text is re-read on all P prompts; prompt p text on the P-p later ones.
    const P = p.promptsPerSession;
    const readsStart = P;
    const readsPrompts = (P * (P - 1)) / 2;
    const writeTok = recallTokensSession;
    const readTok = injectEvents[0].tokens * readsStart + perPrompt * readsPrompts;
    recallCostSession = (sp.in / M) * (writeTok * CACHE_WRITE + readTok * CACHE_READ);
    recallBilledSession = writeTok + readTok;
  } else {
    recallCostSession = (sp.in / M) * recallTokensSession;
    recallBilledSession = recallTokensSession;
  }
  const injectedLearningsDay = p.sessionsPerDay * (p.startLearnings + p.learningsPerPrompt * p.promptsPerSession);
  const recall = {
    injectedTokensDay: p.sessionsPerDay * recallTokensSession,
    tokensDay: p.sessionsPerDay * recallBilledSession,
    costDay: p.sessionsPerDay * recallCostSession,
    injectedLearningsDay,
  };

  // ---- capture: hooks are shell commands, the gate is regex, no model call
  const enqueuedDay = p.sessionsPerDay * p.gatePass;
  const capture = { tokensDay: 0, costDay: 0, enqueuedDay };

  // ---- drain
  const capacity = Math.min(p.dailyMax, p.drainRuns * p.perRunMax);
  const processed = Math.min(enqueuedDay, capacity);
  const entry = drainEntry(p);
  const drain = {
    entry,
    capacity,
    processedDay: processed,
    backlogGrowthDay: Math.max(0, enqueuedDay - capacity),
    tokensDay: processed * entry.tokens,
    costDay: processed * entry.cost,
    learningsDay: processed * p.learningsPerDrain,
  };

  // ---- savings
  const blended = (1 - p.discoveryOutShare) * sp.in + p.discoveryOutShare * sp.out;
  const usefulDay = injectedLearningsDay * p.usefulRate;
  const savedTokensDay = usefulDay * p.discoveryTokens;
  const savings = {
    usefulDay,
    tokensDay: savedTokensDay,
    costDay: (savedTokensDay * blended) / M,
    // what recall.py itself would print: every injected learning counted as useful
    codeConventionTokensDay: injectedLearningsDay * Math.max(0, p.discoveryTokens - p.tokensPerLearning),
  };

  const costDay = capture.costDay + drain.costDay + recall.costDay;
  const tokensDay = capture.tokensDay + drain.tokensDay + recall.tokensDay;
  const net = savings.costDay - costDay;
  const breakEvenUseful =
    injectedLearningsDay * p.discoveryTokens * blended > 0
      ? (costDay * M) / (injectedLearningsDay * p.discoveryTokens * blended)
      : Infinity;
  return {
    recall,
    capture,
    drain,
    savings,
    total: { costDay, tokensDay, netDay: net, breakEvenUseful },
    days: DAYS_PER_MONTH,
  };
}

// Calibration against the two measured runs in plugin/CHANGELOG.md.
export function calibration() {
  const base = { ...defaults(), drainModel: 'sonnet' };
  const ex = extractEntry({ ...base, transcriptKB: 1500, sliceRatio: 0.1, learningsPerDrain: 3 });
  const ag = agenticEntry({ ...base, transcriptKB: 400, agenticTurns: 20 });
  return [
    {
      label: 'Extract writer, 1.5 MB transcript, Sonnet (5.2.5)',
      modeled: { tokens: ex.tokens, cost: ex.cost },
      measured: { tokens: 77982, cost: 0.4 },
    },
    {
      label: 'Agentic writer, 20 turns, Sonnet (5.2.3)',
      modeled: { tokens: ag.tokens, cost: ag.cost },
      measured: { tokens: 6.8e6, cost: 4.42 },
    },
  ];
}
