// Hook wiring + session script for the hook-timeline explainer.
//
// Every event name and "wired" flag below is verified against:
//   claude  -> .claude-plugin/plugin.json                (hooks block)
//   codex   -> plugin/codex-hooks.json
//   copilot -> plugin/copilot-hooks.json
// Script behaviour is taken from the docstrings in plugin/hooks/*.py and
// plugin/skills/recall/hooks/*.py. A node check in the docs-site repo
// (scripts are not shipped) compares WIRING against the three JSON files.

export const REPO_BLOB = 'https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/';

// Lanes = what reflect does at a hook.
export const LANES = [
  { id: 'recall', label: 'Recall injection', note: 'additionalContext sent to the model' },
  { id: 'capture', label: 'Capture (no LLM)', note: 'regex/state-file work, zero model tokens' },
  { id: 'queue', label: 'Drain enqueue', note: 'append to pending_reflections.jsonl' },
  { id: 'drain', label: 'Drain run', note: 'detached background consumer (LLM)' },
];

export const HARNESSES = {
  claude: {
    label: 'Claude Code',
    config: '.claude-plugin/plugin.json',
    // canonical id -> event name as written in that harness's hook config
    names: {
      sessionStart: 'SessionStart',
      userPrompt: 'UserPromptSubmit',
      preTool: 'PreToolUse',
      permission: 'PermissionRequest',
      notification: 'Notification',
      postTool: 'PostToolUse',
      postToolFail: 'PostToolUseFailure',
      subagentStart: 'SubagentStart',
      subagentStop: 'SubagentStop',
      preCompact: 'PreCompact',
      postCompact: 'PostCompact',
      stop: 'Stop',
      sessionEnd: 'SessionEnd',
    },
  },
  codex: {
    label: 'Codex',
    config: 'plugin/codex-hooks.json',
    names: {
      sessionStart: 'SessionStart',
      userPrompt: 'UserPromptSubmit',
      preTool: 'PreToolUse',
      permission: 'PermissionRequest',
      postTool: 'PostToolUse',
      subagentStart: 'SubagentStart',
      subagentStop: 'SubagentStop',
      preCompact: 'PreCompact',
      postCompact: 'PostCompact',
      stop: 'Stop',
    },
  },
  copilot: {
    label: 'Copilot CLI',
    config: 'plugin/copilot-hooks.json',
    names: {
      sessionStart: 'sessionStart',
      userPrompt: 'userPromptSubmitted',
      preTool: 'preToolUse',
      permission: 'permissionRequest',
      notification: 'notification',
      postTool: 'postToolUse',
      postToolFail: 'postToolUseFailure',
      subagentStart: 'subagentStart',
      subagentStop: 'subagentStop',
      preCompact: 'preCompact',
      stop: 'agentStop',
      sessionEnd: 'sessionEnd',
      errorOccurred: 'errorOccurred',
    },
  },
};

// What each canonical hook runs (same scripts in all three harnesses).
// effects: which lane lights up, plus a one-line description and the state
// change applied to the session-state counters.
export const HOOKS = {
  sessionStart: {
    scripts: [
      'plugin/skills/recall/hooks/session_start_recall.py',
      'plugin/hooks/reflect-drain-bg.sh',
    ],
    lanes: ['recall', 'drain'],
    does:
      'Builds a query from cwd, branch and recent commits, runs recall.py (limit 3, 1500 chars, min-overlap 0.2) and injects the hits. In the same event, spawns reflect-drain-bg.sh detached (nohup, 5s hook timeout) so the queue from earlier sessions gets drained without blocking startup.',
  },
  userPrompt: {
    scripts: ['plugin/skills/recall/hooks/user_prompt_submit_recall.py'],
    lanes: ['recall', 'capture'],
    does:
      'Uses the prompt itself as the recall query (skipped under 12 chars). Over-fetches 9, drops ids already injected this session, keeps the first 3 blocks (the header line is one, so at most 2 learnings) within 1500 chars. Before recall it checks the armed watchers: if a tool failed or a permission prompt was just shown and this prompt reads like a correction or a decision, it writes a mini-learning to disk with no LLM call.',
  },
  preTool: {
    scripts: ['plugin/hooks/pretooluse_context.py'],
    lanes: ['recall'],
    does:
      'Narrow policy lookup, not broad recall. Reads deterministic rules from REFLECT_POLICY_FILE or ~/.reflect/policy-rules.jsonl, adds small model-visible context, or denies when an exact rule is a high-confidence deny.',
  },
  permission: {
    scripts: ['plugin/hooks/permission_request_reflect.py'],
    lanes: ['recall', 'capture'],
    does:
      'Permission-pattern lookup and watcher arming: remembers that a permission decision is pending so the next prompt can be captured as a policy learning.',
  },
  notification: {
    scripts: ['plugin/hooks/notification_reflect.py'],
    lanes: ['capture'],
    does:
      'If the notification is a permission prompt, arms ~/.reflect/permission-armed/<session>.json. The next UserPromptSubmit turns a reply like "yes always" or "no never" into a permission-pattern learning.',
  },
  postTool: {
    scripts: ['plugin/hooks/posttooluse_minilearning.py'],
    lanes: ['capture'],
    does:
      'Fires after every tool call. If the result was a failure (non-zero exit, error status) it arms ~/.reflect/armed/<session>.json. Empty stdout, always exit 0.',
  },
  postToolFail: {
    scripts: ['plugin/hooks/posttoolusefailure_minilearning.py'],
    lanes: ['capture'],
    does:
      'Explicit failure hook: arms the same ~/.reflect/armed/<session>.json watcher with reason "failure" (secrets scrubbed, payload truncated to 500 chars).',
  },
  subagentStart: {
    scripts: ['plugin/hooks/subagent_start_recall.py'],
    lanes: ['recall'],
    does:
      'Subagent-scoped recall bootstrap: query shaped by agent type and parent task, limit 3, 1500 chars (REFLECT_SUBAGENT_RECALL_LIMIT / _MAX_CHARS), 5s timeout.',
  },
  subagentStop: {
    scripts: ['plugin/hooks/subagent_stop_reflect.py'],
    lanes: ['queue'],
    does: 'Queues the subagent transcript for the background drain. Does not run /reflect.',
  },
  preCompact: {
    scripts: ['plugin/hooks/precompact_reflect.py'],
    lanes: ['queue'],
    does:
      'Runs with --auto --verbose. The context is about to be compacted away, so it passes the $0 enqueue gate (reflect_gate.py) and appends the transcript to the queue. Pure side effect: no additionalContext.',
  },
  postCompact: {
    scripts: ['plugin/hooks/postcompact_bookkeeping.py'],
    lanes: [],
    does:
      'Bookkeeping only: no recall, no queue append, no drain. Optional dedupe reset behind REFLECT_POSTCOMPACT_RESET_DEDUPE=1.',
  },
  stop: {
    scripts: ['plugin/hooks/stop_reflect.py'],
    lanes: ['capture', 'queue'],
    does:
      'Deterministic slot update (gated by REFLECT_SLOTS: TODOs, tool counts, touched files), then the enqueue gate. Skips if an entry for this session_id is already queued, so a PreCompact enqueue is never duplicated.',
  },
  sessionEnd: {
    scripts: ['plugin/hooks/session_end_reflect.py'],
    lanes: ['queue'],
    does:
      'Final queue producer. Same gate and dedupe as Stop; catches sessions that end without a Stop having enqueued.',
  },
  errorOccurred: {
    scripts: ['plugin/hooks/error_occurred_reflect.py'],
    lanes: ['capture'],
    does: 'Copilot-only error breadcrumb sink.',
  },
};

// The scripted session. Each step is one moment; "events" are the canonical
// hooks that fire at it. If a harness does not wire an event it is shown as
// absent, and `fallback` (if given) explains what covers for it.
export const STEPS = [
  {
    id: 'start',
    label: 'Session starts',
    story: 'You open the agent in a repo.',
    events: ['sessionStart'],
  },
  {
    id: 'p1',
    label: 'Prompt 1',
    story: 'You type: "fix the flaky checkout test".',
    events: ['userPrompt'],
  },
  {
    id: 'pre',
    label: 'Tool call',
    story: 'The agent is about to run a shell command.',
    events: ['preTool'],
  },
  {
    id: 'perm',
    label: 'Permission prompt',
    story: 'The harness asks you to approve the command.',
    events: ['permission', 'notification'],
  },
  {
    id: 'fail',
    label: 'Tool fails',
    story: 'The command exits non-zero.',
    events: ['postToolFail', 'postTool'],
    // Codex wires only PostToolUse, and posttooluse_minilearning.py itself
    // detects failed results, so the arming still happens.
    pick: 'firstWired',
  },
  {
    id: 'p2',
    label: 'Prompt 2 (correction)',
    story: 'You reply: "no, use the fake clock instead".',
    events: ['userPrompt'],
  },
  {
    id: 'sub',
    label: 'Subagent',
    story: 'The agent delegates to a subagent and it finishes.',
    events: ['subagentStart', 'subagentStop'],
  },
  {
    id: 'compact',
    label: 'Compaction',
    story: 'The context fills up and the harness compacts it.',
    events: ['preCompact', 'postCompact'],
  },
  {
    id: 'stop',
    label: 'Agent stops',
    story: 'The agent finishes its turn.',
    events: ['stop'],
  },
  {
    id: 'end',
    label: 'Session ends',
    story: 'You close the session.',
    events: ['sessionEnd'],
  },
  {
    id: 'next',
    label: 'Next session',
    story: 'Later, a new session starts. Its SessionStart hook spawns the drain.',
    events: ['sessionStart'],
    drain: true,
  },
];

// State transitions per event. Applied only when the event is wired.
// state: { injected, injectedTokens, armed, permArmed, mini, queue, slots, drained }
export function applyEvent(state, evId, stepId, wired) {
  const s = { ...state };
  switch (evId) {
    case 'sessionStart':
      if (stepId === 'start') {
        s.injected += 3;
        s.injectedTokens += 330;
      } else if (stepId === 'next') {
        // detached drain consumes the queue
        s.drained = s.queue;
        s.queue = 0;
      }
      break;
    case 'userPrompt':
      if (stepId === 'p1') {
        s.injected += 2;
        s.injectedTokens += 220;
      } else if (stepId === 'p2') {
        s.injected += 1;
        s.injectedTokens += 110;
        if (s.armed) {
          s.mini += 1;
          s.armed = false;
        }
        if (s.permArmed) {
          s.mini += 1;
          s.permArmed = false;
        }
      }
      break;
    case 'permission':
    case 'notification':
      s.permArmed = true;
      break;
    case 'postTool':
    case 'postToolFail':
      if (stepId === 'fail') s.armed = true;
      break;
    case 'subagentStart':
      s.injected += 1;
      s.injectedTokens += 110;
      break;
    case 'subagentStop':
      s.queue = Math.max(s.queue, 1);
      break;
    case 'preCompact':
      s.queue = Math.max(s.queue, 1);
      break;
    case 'stop':
      s.slots = true;
      s.queue = Math.max(s.queue, 1);
      break;
    case 'sessionEnd':
      s.queue = Math.max(s.queue, 1);
      break;
    default:
  }
  return s;
}

export const INITIAL_STATE = {
  injected: 0,
  injectedTokens: 0,
  armed: false,
  permArmed: false,
  mini: 0,
  queue: 0,
  slots: false,
  drained: 0,
};

// Resolve which canonical events actually fire at a step for a harness.
export function resolveStep(harnessId, step) {
  const h = HARNESSES[harnessId];
  const wired = step.events.filter((e) => e in h.names);
  const missing = step.events.filter((e) => !(e in h.names));
  let fires = wired;
  if (step.pick === 'firstWired') fires = wired.slice(0, 1);
  return { fires, missing };
}

// Replays STEPS for a harness and returns the state after each step.
export function simulate(harnessId) {
  const out = [];
  let state = { ...INITIAL_STATE };
  for (const step of STEPS) {
    const { fires, missing } = resolveStep(harnessId, step);
    for (const ev of fires) state = applyEvent(state, ev, step.id, true);
    out.push({ step, fires, missing, state });
  }
  return out;
}

// Events a harness wires that never appear in the scripted session.
export function unscripted(harnessId) {
  const h = HARNESSES[harnessId];
  const used = new Set(STEPS.flatMap((s) => s.events));
  return Object.keys(h.names).filter((e) => !used.has(e));
}
