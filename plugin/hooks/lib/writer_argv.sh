#!/usr/bin/env bash
# writer_argv.sh: the exact claude -p argv the drain's agentic writer runs, the
# allow rules and the PreToolUse guard it runs under, and the prompt addendum
# that makes the /reflect skill's steps and those rules agree.
#
# Zero side effects: defining functions and defaults is all this file does,
# so both the drain hook (sourced at its top) and the compat gate (sourced
# directly) read the same argv and cannot drift. The Python twin is
# drain_extract.writer_argv.
#
# Permission surface, pinned here and nowhere else:
#   --permission-mode default    an explicit flag takes precedence over the
#                                operator's settings.json defaultMode (proven
#                                live on CLI 2.1.263 against a HOME whose
#                                settings say bypassPermissions); a tool
#                                outside the rules is denied
#   --settings <inline JSON>     the hook-owned allow rules below plus a
#                                PreToolUse hook (scripts/drain_guard.py) that
#                                decides every Bash call before it runs, on
#                                the normalised command, with a reason; passed
#                                as one argv element so multi-word rules survive
#   --strict-mcp-config          no MCP servers from the operator's config
#
# Setting sources are deliberately KEPT: `--setting-sources ""` unregisters
# plugins and personal skills, so `claude -p "/reflect ..."` answers
# "Unknown command: /reflect" with zero turns and wedges the queue (proven
# live). The operator's permission rules still load, but the explicit mode
# flag and the deny-by-default of headless mode decide what runs. The
# operator's hooks load too; every reflect hook exits at once under
# REFLECT_NESTED=1, which the drain exports to the writer, so a nested claude
# never fires a drain of its own (recursion).
#
# One command surface: every scripted step of the skill is
# `reflect skill-step <step> ...` (the reflect CLI resolves the scripts), so
# the rules carry no path spelling and no python3 at all.
#
# Inputs (each with the default below so the file also works sourced alone):
#   CLAUDE_BIN            claude binary            (default: claude)
#   DRAIN_MODEL           --model alias            (default: DRAIN_DEFAULT_MODEL)
#   MAX_TURNS             --max-turns budget       (default: DRAIN_DEFAULT_MAX_TURNS)
#   DRAIN_SCRIPTS_DIR     the skill's scripts dir  (default: <this lib>/../../scripts,
#                         which is <plugin root>/scripts under the plugin
#                         runtime and ~/.claude/skills/reflect/scripts under an
#                         adapter install); names the guard hook and is
#                         exported to the writer as REFLECT_SKILL_SCRIPTS_DIR
#   REFLECT_DRAIN_ALLOWED_TOOLS  comma-separated override of the whole allow list
#
# bash 3.2 compatible (no mapfile, no associative arrays).

# The one home of the writer's model and turn budget defaults; the hook reads
# these (REFLECT_DRAIN_MODEL / REFLECT_DRAIN_MAX_TURNS override them). num_turns
# counts assistant messages, not tool calls, so ~N/2 tool calls fit in N turns;
# the writer's minimum honest workflow is ~7 tool calls, 16 completes with
# headroom (measured: 13 turns to a written learning).
DRAIN_DEFAULT_MODEL="sonnet"
DRAIN_DEFAULT_MAX_TURNS=16

# The writer runs with cwd = DRAIN_CWD ($HOME). Paths below are relative to
# that cwd or rooted at ~; the /reflect skill's steps write nowhere else.
_WRITER_NOTE_SCOPE="docs/solutions/**"              # step 6.2, 6.3: note + sidecar
_WRITER_EPISODE_SCOPE="~/.reflect/episodes/**"      # step 7: episode note
_WRITER_AGENT_SCOPE="~/.claude/agents/**"           # step 6.1: behavioral edits
_WRITER_SKILL_SCOPE="~/.claude/skills/**/SKILL.md"  # step 2.5 edit, skill_refresh

# Steps the drain deliberately does NOT grant. A denial on one of these is
# expected and never changes the run's outcome; the prompt addendum tells the
# writer to skip them. Kept as data so the tests can walk SKILL.md against it.
_WRITER_EXCLUDED="step 2.5 shell loop over skills (use Glob and Read instead)
step 2 edits to the global rules file ~/.claude/CLAUDE.md (an operator's rules are never edited headlessly)
step 3 new skill creation under ~/.claude/skills/<new>/ (a new skill needs a human; existing SKILL.md edits are granted)
step 6.8 git commit (the KB is indexed by reflect add; nothing to commit from a neutral cwd)
memory files under .agents/ or ~/.claude/projects/ (the drain has no project cwd)
uv run and python3 (every scripted step is a reflect skill-step command)
anything not listed in the allow rules"

# drain_writer_scripts_dir: where the skill's scripts live for this layout.
drain_writer_scripts_dir() {
    if [[ -n "${DRAIN_SCRIPTS_DIR:-}" ]]; then
        printf '%s' "$DRAIN_SCRIPTS_DIR"
    else
        local here
        here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
        local resolved
        resolved="$(cd "$here/../../scripts" 2>/dev/null && pwd -P)" || resolved="$here/../../scripts"
        printf '%s' "$resolved"
    fi
}

# drain_writer_guard: the PreToolUse hook script, by absolute path.
drain_writer_guard() {
    printf '%s/drain_guard.py' "$(drain_writer_scripts_dir)"
}

# drain_writer_rules: fills WRITER_RULES with the allow rules, one per element.
drain_writer_rules() {
    WRITER_RULES=()
    if [[ -n "${REFLECT_DRAIN_ALLOWED_TOOLS:-}" ]]; then
        local IFS=','
        # shellcheck disable=SC2206
        WRITER_RULES=(${REFLECT_DRAIN_ALLOWED_TOOLS})
        return 0
    fi
    WRITER_RULES=(
        Read Grep Glob
        "Write(${_WRITER_NOTE_SCOPE})" "Edit(${_WRITER_NOTE_SCOPE})"
        "Write(${_WRITER_EPISODE_SCOPE})"
        "Edit(${_WRITER_AGENT_SCOPE})"
        "Edit(${_WRITER_SKILL_SCOPE})"
        "Bash(reflect skill-step:*)" "Bash(reflect add:*)" "Bash(reflect search:*)"
    )
}

_drain_json_escape() {
    local esc="${1//\\/\\\\}"
    esc="${esc//\"/\\\"}"
    printf '%s' "$esc"
}

# drain_writer_settings_json: the inline --settings document: WRITER_RULES as
# permissions.allow, and the guard as a PreToolUse hook on Bash. The guard is
# always installed, override or not: it decides Bash calls, the rules the rest.
drain_writer_settings_json() {
    local out="" rule guard
    for rule in "${WRITER_RULES[@]}"; do
        out="${out:+$out,}\"$(_drain_json_escape "$rule")\""
    done
    guard="$(_drain_json_escape "python3 $(drain_writer_guard)")"
    printf '{"permissions":{"defaultMode":"default","allow":[%s]},"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"%s","timeout":15}]}]}}' \
        "$out" "$guard"
}

# drain_writer_prompt_addendum: appended to every agentic prompt. It states
# the one command surface and lists the steps the drain does not grant, so
# the writer skips them instead of tripping denials.
drain_writer_prompt_addendum() {
    cat <<'ADDENDUM'


Headless drain rules (this run has no user to approve; treat every proposal as approved):
- Every scripted step of the skill is one plain command: reflect skill-step <step> ... (steps:
  validate-sidecar, index, metrics, state, revise, observe). No python3, no uv run, no shell
  variables, no cd, no pipes: the exact command as the skill spells it.
- Index each note with exactly: reflect skill-step index <note> <sidecar>
  (it validates the sidecar strictly, then runs reflect add with --force).
- Allowed: Read, Grep, Glob; Write and Edit under docs/solutions/; Write under ~/.reflect/episodes/;
  Edit of ~/.claude/agents/*.md and of an existing ~/.claude/skills/*/SKILL.md; reflect skill-step;
  reflect add; reflect search.
- Skip: the step 2.5 shell loop (use Glob and Read to list skills), edits to ~/.claude/CLAUDE.md,
  creating a new skill, step 6.8 git commit, memory files under .agents/ or ~/.claude/projects/.
  Do not run any other command.
ADDENDUM
}

# drain_writer_prompt <base prompt>: the base prompt plus the addendum.
drain_writer_prompt() {
    printf '%s%s' "$1" "$(drain_writer_prompt_addendum)"
}

# drain_agentic_writer_argv <prompt>: fills WRITER_ARGV.
drain_agentic_writer_argv() {
    local prompt="$1"
    drain_writer_rules
    WRITER_ARGV=(
        "${CLAUDE_BIN:-claude}"
        -p "$prompt"
        --model "${DRAIN_MODEL:-$DRAIN_DEFAULT_MODEL}"
        --output-format json
        --permission-mode default
        --settings "$(drain_writer_settings_json)"
        --strict-mcp-config
        --max-turns "${MAX_TURNS:-$DRAIN_DEFAULT_MAX_TURNS}"
    )
}
