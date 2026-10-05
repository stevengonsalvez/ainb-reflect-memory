---
# S9: no telemetry in frontmatter. This file holds semantic fields only.
# Volatile ranking signals (importance, maturity, recall_count, helpful_count,
# ignored_count, stale_count, last_recalled_at) change on every query, so they
# live in reflect.db's `learning_signals` sidecar table and must NEVER be added
# here: per-query bumps would dirty git-tracked notes and merge-conflict across
# teammates (ByteRover runtime-signals sidecar shape). `updated` below reflects
# real content modifications only; ranking updates never touch it.
# Event-driven edits are allowed: content edits, and the OKF lifecycle fields
# `status`, `verified` and `stale_after`, which change on a rare event
# (supersede, human confirmation) and cost one commit each. Lifecycle fields
# are rewritten only through reflect_kb.okf.set_lifecycle_fields, which
# touches no other line.
#
# OKF v0.2 profile: every note is also an Open Knowledge Format concept.
# `type` is OKF's one required key (always lowercase `learning`); `title`,
# `description`, `generated`, `sources`, `stale_after` below are OKF keys
# reflect derives on write. Every other key is a reflect extension key.
# Timestamps carry an explicit offset (e.g. `Z`).
type: learning
id: lrn-{{SLUG}}-{{HASH6}}
created: {{ISO_TIMESTAMP}}
updated: {{ISO_TIMESTAMP}}
scope: {{SCOPE}}
confidence: {{CONFIDENCE}}
# S3: continuous confidence 0.0–1.0 — the value recall RANKS by; `confidence`
# above is just the display bucket (>=0.8 HIGH, >=0.5 MEDIUM, else LOW).
# Prefer a calibrated float (e.g. 0.75) over snapping to the bucket midpoints
# (HIGH→0.9, MEDIUM→0.6, LOW→0.3) — the midpoints are only the migration
# defaults for legacy tier-only notes.
confidence_num: {{CONFIDENCE_NUM}}
learning_type: {{LEARNING_TYPE}}
# M8: token economics — integer estimate of the tokens the ORIGINATING
# session spent discovering this information (≈ source transcript chars / 4).
# Captured at WRITE time; recall surfaces discovery-vs-re-read cost on every
# injected block. Legacy notes without it fall back to the source transcript
# size, then a per-type category average.
discovery_tokens: {{DISCOVERY_TOKENS}}
title: "{{TITLE}}"
description: "{{KEY_INSIGHT}}"          # OKF: one line, <= ~200 chars (key_insight, else first sentence)
tags: [{{TAGS}}]
symptoms:
  - "{{SYMPTOM_1}}"
key_insight: "{{KEY_INSIGHT}}"
# S1: structured extraction fields (Hindsight fact_extraction shape).
# Typed distillations of the prose body — recall can return just one field
# (`recall.py --field rule`) instead of the whole note. Be SELECTIVE and
# CONCISE: one strong sentence each; only what stays useful 6 months out.
problem: "{{PROBLEM_ONE_LINER}}"        # what went wrong, 1 sentence
root_cause: "{{ROOT_CAUSE}}"            # the underlying cause, 1 sentence
fix: "{{FIX_ONE_LINER}}"                # what resolved it, 1 sentence
rule: "{{RULE}}"                        # imperative do/don't to follow next time
category: "{{CATEGORY}}"                # e.g. build-errors | debugging-sessions
entities: [{{ENTITIES}}]                # specific named tech/tools/errors involved
causal_relations:                       # cause -> effect chains ([] when none)
  - source: "{{CAUSE_ENTITY}}"
    target: "{{EFFECT_ENTITY}}"
    type: caused_by                     # or causes | enables | prevents (S2 typed links)
links: []
source_episodes: [{{EPISODE_ID}}]
superseded_by: null
# A3: per-row TTL. ISO timestamp after which the hourly forget sweep archives
# this learning; null = permanent. Set ONLY for clearly time-bounded knowledge
# ("avoid X service during the incident", "valid for the current migration /
# sprint / quarter") — durable rules must stay null.
forget_after: null
# OKF lifecycle: absolute instant mirroring forget_after (omit when null).
# stale_after: "{{ISO_TIMESTAMP}}"
# OKF lifecycle (S9 carve-out, mutable): draft | stable | deprecated; absent
# means stable. `reflect serve` archive sets deprecated, restore clears it.
# status: stable
# OKF trust (S9 carve-out, mutable): who confirmed the note, newest last.
# verified: [{by: "human:{{USER_ID}}", at: "{{ISO_TIMESTAMP}}"}]
# OKF trust: the writer actor (`<producer>/<version>`) and created time.
generated: {by: "{{WRITER}}/{{VERSION}}", at: "{{ISO_TIMESTAMP}}"}
# OKF provenance: source_path (or `session:<id>`) as a sources entry.
sources: [{id: source, resource: "{{SOURCE_PATH}}"}]
provenance:
  source_tool: "{{SOURCE_TOOL}}"      # claude | codex | copilot | gemini
  source_path: "{{SOURCE_PATH}}"
  content_hash: "{{CONTENT_HASH}}"
  detected_at: {{ISO_TIMESTAMP}}
  source_memory_ids: [{{EPISODE_ID}}]  # unique; UPDATE appends, never duplicates
  proof_count: 1                       # CREATE starts at 1; UPDATE increments
---

## Problem

{{PROBLEM_DESCRIPTION}}

## Solution

{{SOLUTION_STEPS}}

## Anti-Pattern

{{ANTI_PATTERN}}

## Context

{{ADDITIONAL_CONTEXT}}
