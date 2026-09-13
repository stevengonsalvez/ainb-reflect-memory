#!/usr/bin/env bash
# writer_argv.sh: the exact claude -p argv the drain's agentic writer runs.
#
# Zero side effects: defining a function is all this file does, so both the
# drain hook (sourced at its top) and the compat gate (sourced directly) read
# the same argv and cannot drift. The Python twin is drain_extract.writer_argv.
#
# Inputs are the hook's config variables; each has the hook's default so the
# function also works when sourced alone:
#   CLAUDE_BIN    claude binary            (default: claude)
#   DRAIN_MODEL   --model alias            (default: sonnet)
#   MAX_TURNS     --max-turns budget       (default: 16)
#
# drain_agentic_writer_argv <prompt> fills the WRITER_ARGV array. An array, not
# a printed string, so a prompt with spaces or newlines survives; bash 3.2
# compatible (no mapfile, no associative arrays).
drain_agentic_writer_argv() {
    local prompt="$1"
    WRITER_ARGV=(
        "${CLAUDE_BIN:-claude}"
        -p "$prompt"
        --model "${DRAIN_MODEL:-sonnet}"
        --output-format json
        --permission-mode bypassPermissions
        --max-turns "${MAX_TURNS:-16}"
    )
}
