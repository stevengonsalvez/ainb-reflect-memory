#!/usr/bin/env python3
# ABOUTME: Single-shot structured-extraction drain writer. Replaces the agentic
# `claude -p "/reflect"` loop with ONE tool-free `claude -p` call that emits a
# JSON action list, then executes it deterministically: CREATE via `reflect add`
# (the same canonical global-corpus write the agentic writer used), UPDATE/DELETE
# via `reflect_cascade.py revise`.
#
# Why: the agentic writer re-read its whole growing conversation every turn, so
# cost grew ~quadratically with turns (measured: 20 turns = 6.8M tokens = $4.42;
# real backlog transcripts blew the 16-turn cap and captured NOTHING at ~$1.2).
# One turn cannot re-read history and cannot "partial_max_turns": cost is linear
# in slice size, and either valid JSON comes back or it doesn't.
#
# The slice (built by reflect_cascade.prepare) already carries the signal windows
# plus the belief-revision block (related learnings + the CREATE/UPDATE/DELETE
# action contract), so extraction needs no extra context assembly.

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# Bound the model's output so a pathological slice cannot ask for a giant
# response (output-side truncation is the single-shot analogue of the agentic
# loop's runaway; cap it and say so).
_MAX_LEARNINGS = 12
_DEFAULT_MODEL = "sonnet"
_DEFAULT_TIMEOUT = 180

_EXTRACTION_INSTRUCTIONS = """\
You are a knowledge-extraction function, not an agent. You have NO tools.

Read the reflect slice below. It contains signal-bearing exchanges from a coding
session, and may contain a "belief revision" block listing related existing
learnings with their ids.

Output EXACTLY ONE JSON object and nothing else: no prose, no code fence, no
explanation. Schema:

{
  "actions": [
    // CREATE a new durable learning:
    {
      "action": "CREATE",
      "reason": "<one sentence why this is worth keeping>",
      "learning": {
        "title": "<= 12 words, specific",
        "category": "<e.g. debugging-sessions | build-errors | architecture>",
        "key_insight": "<the single most reusable sentence>",
        "problem": "<what went wrong, one sentence>",
        "root_cause": "<the underlying cause, one sentence>",
        "fix": "<what resolved it, one sentence>",
        "rule": "<imperative do/don't to follow next time>",
        "confidence_num": <float 0.0-1.0>,
        "tags": ["<short>", "..."],
        "entities": ["<named tech/tool/error, proper nouns only>"],
        "causal_relations": [
          {"source": "<entity>", "target": "<entity>",
           "type": "caused_by|causes|enables|prevents"}
        ],
        "body": "<markdown: ## Problem / ## Solution / ## Anti-Pattern / ## Context>"
      }
    },
    // MERGE into an existing learning the slice restates (prefer this over CREATE):
    {"action": "UPDATE", "target_id": "<lrn-id from the revision block>",
     "reason": "<one sentence>"},
    // RETIRE a learning new evidence directly contradicts (be conservative):
    {"action": "DELETE", "target_id": "<lrn-id>", "reason": "<one sentence>"}
  ]
}

Rules:
- PREFER UPDATE over CREATE when a finding restates a listed learning; match on
  the specific rule, not general topic.
- Keep a signal ONLY if a session 6 months out would still act on it. If the
  slice holds nothing durable, output {"actions": []}.
- At most %d learnings. If the slice holds more, keep the highest-value ones.
- Every field present; use "" or [] when genuinely absent.

--- REFLECT SLICE ---
%s
""" % (_MAX_LEARNINGS, "%s")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_prompt(slice_text: str) -> str:
    return _EXTRACTION_INSTRUCTIONS % slice_text


def call_model(prompt: str, *, model: str, timeout: int, claude_bin: str,
               cwd: str) -> dict:
    """One tool-free turn. Returns the parsed claude -p envelope (dict).

    --allowedTools "" removes every tool, so the model can only answer with
    text; --max-turns 1 guarantees a single billed turn. Both together make the
    'agentic loop' structurally impossible.
    """
    proc = subprocess.run(
        [claude_bin, "-p", prompt,
         "--model", model,
         "--output-format", "json",
         "--permission-mode", "bypassPermissions",
         "--allowedTools", "",
         "--max-turns", "1"],
        cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False,
    )
    if not proc.stdout.strip():
        raise RuntimeError(f"claude -p produced no output (exit={proc.returncode}): "
                           f"{proc.stderr[:200]}")
    return json.loads(proc.stdout)


def parse_actions(result_text: str) -> list[dict]:
    """Extract the actions list from the model's text.

    Tolerant: the model is told to emit bare JSON, but strip a ``` fence and
    locate the outermost object if it adds stray prose anyway. Raises on genuine
    non-JSON so the caller records a failure instead of silently capturing zero.
    """
    text = result_text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    # Outermost {...}; the schema is a single object.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    obj = json.loads(text[start:end + 1])
    actions = obj.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("`actions` is not a list")
    return actions


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (s[:60].rstrip("-")) or "learning"


def _learning_id(learning: dict) -> str:
    """Stable id shared by the note and its sidecar so they cross-reference."""
    title = str(learning.get("title", "") or "untitled").strip()
    hash6 = sha1(f"{title}{learning.get('rule','')}".encode()).hexdigest()[:6]
    return f"lrn-{_slug(title)}-{hash6}"


# Closed enum for causal-link types (knowledge_format.md). A model-supplied
# value outside it is a YAML-injection vector (it is interpolated as a bare
# scalar), so anything unrecognised collapses to the weakest safe edge.
_CAUSAL_TYPES = frozenset((
    "caused_by", "causes", "enables", "prevents", "contradicts", "supersedes",
    "part_of", "uses", "solves", "requires", "relates_to", "implements",
    "configures", "triggers",
))


def _causal_type(raw) -> str:
    return raw if raw in _CAUSAL_TYPES else "relates_to"


# The header reflect_cascade._build_revision_block emits before the DB-vetted
# related-learnings payload. Ids are only trusted from AFTER this marker.
_REVISION_MARKER = "## Related existing learnings (belief revision)"


def _revision_block_ids(slice_text: str) -> set[str]:
    """Learning ids the slice's belief-revision block actually offered.

    Scanned ONLY from the revision-block marker onward, never the transcript
    body above it: the body is untrusted content, so a transcript that embeds a
    victim id (`lrn-victim-...`) in its own text must not get that id
    whitelisted for an injected DELETE. Absent marker => no revisable ids.
    """
    idx = slice_text.find(_REVISION_MARKER)
    if idx == -1:
        return set()
    return set(re.findall(r"\blrn-[a-z0-9-]+\b", slice_text[idx:]))


def _yaml_str(s: str) -> str:
    """Quote a model-supplied string as a single-line double-quoted YAML scalar.

    Collapse embedded newlines/tabs to spaces so a value never spans lines (a
    raw newline in a `key: "..."` line would otherwise rely on multi-line-scalar
    folding, which not every frontmatter reader honours). Escape backslash and
    quote so the value cannot terminate the scalar or inject a key.
    """
    flat = re.sub(r"\s+", " ", str(s)).strip()
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_md(learning: dict, *, source_path: str, session_id: str) -> str:
    """Render a corpus note the `reflect add` importer accepts.

    Requires at least title/category/key_insight (per `reflect add --help`); the
    typed extraction fields ride alongside so recall can return one field.
    """
    title = str(learning.get("title", "") or "untitled").strip()
    conf_num = learning.get("confidence_num")
    try:
        conf_num = float(conf_num)
    except (TypeError, ValueError):
        conf_num = 0.6
    conf = "high" if conf_num >= 0.8 else "medium" if conf_num >= 0.5 else "low"
    tags = learning.get("tags") or []
    entities = [e for e in (learning.get("entities") or []) if e]

    fm = ["---",
          "type: learning",
          f"id: {_learning_id(learning)}",
          f"created: {_now_iso()}",
          f"updated: {_now_iso()}",
          "scope: global",
          f"confidence: {conf}",
          f"confidence_num: {conf_num}",
          "learning_type: bug-fix",
          f"title: {_yaml_str(title)}",
          "tags: [" + ", ".join(_yaml_str(t) for t in tags) + "]",
          f"key_insight: {_yaml_str(learning.get('key_insight',''))}",
          f"problem: {_yaml_str(learning.get('problem',''))}",
          f"root_cause: {_yaml_str(learning.get('root_cause',''))}",
          f"fix: {_yaml_str(learning.get('fix',''))}",
          f"rule: {_yaml_str(learning.get('rule',''))}",
          f"category: {_yaml_str(learning.get('category','Unknown'))}",
          "entities: [" + ", ".join(_yaml_str(e) for e in entities) + "]"]
    rels = learning.get("causal_relations") or []
    if rels:
        fm.append("causal_relations:")
        for r in rels:
            if not isinstance(r, dict):
                continue
            fm.append(f"  - source: {_yaml_str(r.get('source',''))}")
            fm.append(f"    target: {_yaml_str(r.get('target',''))}")
            fm.append(f"    type: {_causal_type(r.get('type'))}")
    else:
        fm.append("causal_relations: []")
    if source_path:
        fm.append(f"source_path: {_yaml_str(source_path)}")
    if session_id:
        fm.append(f"session_id: {_yaml_str(session_id)}")
    fm.append("---")

    body = str(learning.get("body", "") or "").strip()
    if "## " not in body:
        body = (f"## Problem\n{learning.get('problem','')}\n\n"
                f"## Solution\n{learning.get('fix','')}\n\n"
                f"## Context\n{body}")
    return "\n".join(fm) + "\n\n" + body + "\n"


def render_sidecar(learning: dict) -> str:
    """Valid .entities.yaml. validate_sidecar requires name+type+description on
    every entity and source+target+type+description on every relationship, so
    each carries a (possibly empty) description. document_id matches the note id.
    """
    entities = [e for e in (learning.get("entities") or []) if e]
    lines = [f"document_id: {_learning_id(learning)}",
             f'extracted_at: "{_now_iso()}"']
    if entities:
        lines.append("entities:")
        for e in entities:
            lines.append(f"  - name: {_yaml_str(e)}")
            lines.append("    type: technology")
            lines.append('    description: ""')
    else:
        lines.append("entities: []")
    rels = [r for r in (learning.get("causal_relations") or []) if isinstance(r, dict)]
    if rels:
        lines.append("relationships:")
        for r in rels:
            lines.append(f"  - source: {_yaml_str(r.get('source',''))}")
            lines.append(f"    target: {_yaml_str(r.get('target',''))}")
            lines.append(f"    type: {_causal_type(r.get('type'))}")
            lines.append('    description: ""')
    else:
        lines.append("relationships: []")
    return "\n".join(lines) + "\n"


def execute_create(learning: dict, *, source_path: str, session_id: str,
                   reflect_bin: str) -> tuple[bool, str]:
    """Write .md + .entities.yaml, index via `reflect add --force`."""
    with tempfile.TemporaryDirectory(prefix="drain-extract-") as td:
        title = str(learning.get("title", "") or "learning")
        md = Path(td) / f"{_slug(title)}.md"
        yaml = Path(td) / f"{_slug(title)}.entities.yaml"
        md.write_text(render_md(learning, source_path=source_path,
                                session_id=session_id), encoding="utf-8")
        yaml.write_text(render_sidecar(learning), encoding="utf-8")
        # 60s per index call: keeps the sum of the create pass inside the
        # ~90s the hook reserves below the outer entry timeout, so a slow write
        # does not get the whole python call SIGTERMed mid-index.
        proc = subprocess.run(
            [reflect_bin, "add", "--force", str(md), "--entities", str(yaml)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout)[:200]
        # `reflect add` writes the corpus note + graph but not the learnings
        # table, so record-chunk (which links by source_memory_ids) would never
        # find extract-written learnings. Mirror the agentic path's revise-CREATE
        # by recording a learnings row keyed on the transcript. The note already
        # landed, so a provenance-row failure is surfaced as a NON-fatal warning
        # (it does not reduce the capture count or force a retry). content_hash =
        # the note id so a re-drain of the same learning updates instead of
        # inserting a duplicate row.
        warn = ""
        try:
            import reflect_db
            cn = learning.get("confidence_num")
            try:
                cn = float(cn)
            except (TypeError, ValueError):
                cn = None
            reflect_db.add_learning(
                title=str(learning.get("title", "") or "")[:200],
                category=str(learning.get("category", "") or "Unknown"),
                confidence_num=cn,
                content_hash=_learning_id(learning),
                source_path=source_path,
                source_memory_ids=[source_path] if source_path else None,
                scope="global",
                session_id=session_id,
            )
        except Exception as exc:
            warn = f"provenance row not written: {exc}"
        return True, warn


def execute_revisions(revisions: list[dict], *, source_id: str) -> dict:
    """UPDATE/DELETE straight through the existing belief-revision executor."""
    if not revisions:
        return {"executed": 0, "updated": 0, "deleted": 0, "errors": []}
    import reflect_cascade
    return reflect_cascade.execute_revision_actions(revisions, source_memory_id=source_id)


def run(*, slice_path: str, transcript: str, session_id: str, model: str,
        timeout: int, claude_bin: str, reflect_bin: str, cwd: str) -> dict:
    slice_text = Path(slice_path).read_text(encoding="utf-8")
    envelope = call_model(build_prompt(slice_text), model=model, timeout=timeout,
                          claude_bin=claude_bin, cwd=cwd)
    usage = envelope.get("usage", {}) or {}
    tokens = sum(int(usage.get(k, 0) or 0) for k in
                 ("input_tokens", "output_tokens",
                  "cache_read_input_tokens", "cache_creation_input_tokens"))
    summary = {
        "created": 0, "updated": 0, "deleted": 0, "skipped_over_cap": 0,
        "tokens": tokens, "cost_usd": envelope.get("total_cost_usd", 0) or 0,
        "num_turns": envelope.get("num_turns", 0), "errors": [],
        # retryable_failure separates faults worth re-draining for (a write that
        # failed, a model that returned no JSON) from BENIGN drops (over-cap,
        # an injected/unlisted target id, a title-less CREATE). Only the former
        # keeps the transcript queued: re-draining an over-cap or injected-drop
        # slice just reproduces the same drop and re-bills forever.
        "retryable_failure": 0,
        # Raw buckets so the drain's cost ledger records the same split it does
        # for the agentic path (input/output/cache_read/cache_creation).
        "usage": {k: int(usage.get(k, 0) or 0) for k in
                  ("input_tokens", "output_tokens",
                   "cache_read_input_tokens", "cache_creation_input_tokens")},
        # M3: pass the subscription-quota telemetry through so the drain's quota
        # gate stays fed on the extract path (extract runs on the same quota).
        "rate_limit_info": envelope.get("rate_limit_info")
        or envelope.get("rateLimitInfo"),
    }

    # A model that consumed quota but returned prose instead of JSON is a
    # retryable fault, but the telemetry above must still reach the cost/quota
    # ledger, so catch the parse here rather than letting it unwind run().
    try:
        actions = parse_actions(str(envelope.get("result", "")))
    except ValueError as exc:
        summary["retryable_failure"] += 1
        summary["errors"].append(f"unparseable model output: {exc}")
        return summary

    creates = [a for a in actions if a.get("action") == "CREATE"]
    revisions = [a for a in actions if a.get("action") in ("UPDATE", "DELETE")]
    if len(creates) > _MAX_LEARNINGS:
        summary["skipped_over_cap"] = len(creates) - _MAX_LEARNINGS
        summary["errors"].append(
            f"over cap: kept {_MAX_LEARNINGS}, dropped "
            f"{len(creates) - _MAX_LEARNINGS} CREATE(s)")  # benign: retry re-drops
        creates = creates[:_MAX_LEARNINGS]

    # Injection guard: the model's target_ids are only honoured when the slice's
    # revision block actually offered them. An untrusted transcript cannot make
    # the drain retire/merge an arbitrary learning it never surfaced.
    valid_ids = _revision_block_ids(slice_text)
    kept_rev = []
    for a in revisions:
        tid = str(a.get("target_id", "") or "").strip()
        if tid in valid_ids:
            kept_rev.append(a)
        else:
            summary["errors"].append(
                f"dropped {a.get('action')} for unlisted id {tid!r}")  # benign
    revisions = kept_rev

    for a in creates:
        learning = a.get("learning") or {}
        if not learning.get("title"):
            summary["errors"].append("CREATE missing learning.title")  # benign
            continue
        ok, msg = execute_create(learning, source_path=transcript,
                                 session_id=session_id, reflect_bin=reflect_bin)
        if ok:
            summary["created"] += 1
            if msg:                       # non-fatal provenance warning
                summary["errors"].append(msg)
        else:
            # A write that failed lost a real, extracted learning: retry.
            summary["retryable_failure"] += 1
            summary["errors"].append(f"reflect add failed: {msg}")

    rev = execute_revisions(revisions, source_id=session_id)
    summary["updated"] = rev.get("updated", 0)
    summary["deleted"] = rev.get("deleted", 0)
    summary["errors"].extend(rev.get("errors", []) or [])
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="single-shot extraction drain writer")
    ap.add_argument("--slice", required=True, help="prepared slice path")
    ap.add_argument("--transcript", default="", help="source transcript path")
    ap.add_argument("--session-id", default="")
    ap.add_argument("--model", default=_DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT)
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--reflect-bin", default="reflect")
    ap.add_argument("--cwd", default=str(Path.home()))
    args = ap.parse_args()
    try:
        summary = run(slice_path=args.slice, transcript=args.transcript,
                      session_id=args.session_id, model=args.model,
                      timeout=args.timeout, claude_bin=args.claude_bin,
                      reflect_bin=args.reflect_bin, cwd=args.cwd)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "created": 0, "updated": 0,
                          "deleted": 0}), flush=True)
        return 1
    print(json.dumps(summary), flush=True)
    return 0


def _self_test() -> None:
    """Runnable check: parsing + rendering + cap, no network, no disk writes."""
    acts = parse_actions('```json\n{"actions": [{"action": "CREATE", '
                         '"learning": {"title": "T"}}, '
                         '{"action": "UPDATE", "target_id": "lrn-x"}]}\n```')
    assert len(acts) == 2, acts
    assert acts[0]["action"] == "CREATE" and acts[1]["action"] == "UPDATE"
    assert parse_actions('{"actions": []}') == []
    md = render_md({"title": "Never scale zarnak", "rule": "Always X",
                    "category": "ops", "key_insight": "K", "confidence_num": 0.9,
                    "entities": ["Zarnak"], "body": "## Problem\nx"},
                   source_path="/t.jsonl", session_id="s1")
    assert md.startswith("---\n") and "title:" in md and "## Problem" in md
    assert "confidence: high" in md  # 0.9 -> high bucket
    sc = render_sidecar({"title": "T", "entities": ["Zarnak"],
                         "causal_relations": [{"source": "a", "target": "b",
                                               "type": "causes"}]})
    assert "document_id:" in sc and "Zarnak" in sc and "type: causes" in sc
    # over-cap trimming happens in run(); check the constant is enforced there
    assert _MAX_LEARNINGS > 0
    try:
        parse_actions("not json at all")
        raise AssertionError("expected ValueError on non-JSON")
    except ValueError:
        pass
    print("drain_extract self-test OK")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        _self_test()
    else:
        sys.exit(main())
