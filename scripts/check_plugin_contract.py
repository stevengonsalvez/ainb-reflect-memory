#!/usr/bin/env python3
"""Verify the plugin package still ships every file its consumers depend on.

Two classes of contract, both broken silently in the field when violated:

1. Manifest-referenced files — every ``${CLAUDE_PLUGIN_ROOT}/...`` path named
   in a plugin manifest's hook commands must exist (and be executable when it
   is a shell script). A missing hook fails at session start with no error
   surfaced to the user.

2. External-consumer files — paths read by tooling OUTSIDE the manifests
   (e.g. the user statusline resolves ``plugin/scripts/reflect_timeline.sh``
   inside the installed plugin cache). The manifests never mention these, so
   a layout refactor can move them without any test noticing — v5.2.1 shipped
   without the timeline helper at its contracted path and every statusline
   lost the reflect dashboard. Add a path here when something outside this
   repo starts depending on it; removing or moving one is a breaking change
   that needs a coordinated consumer update.

Runs on stdlib only (CI plugin-manifest job + release gate). Exit 0 = intact.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Manifest path -> the directory ${CLAUDE_PLUGIN_ROOT} resolves to for it.
MANIFESTS = {
    REPO / ".claude-plugin" / "plugin.json": REPO,
    REPO / "plugin" / ".claude-plugin" / "plugin.json": REPO / "plugin",
    REPO / "plugin" / ".codex-plugin" / "plugin.json": REPO / "plugin",
}

# repo-relative path -> must be executable
EXTERNAL_CONTRACT = {
    # ~/.claude/statusline.sh timeline dashboard fragment
    "plugin/scripts/reflect_timeline.sh": True,
    # hermes/codex adapters deploy this into the harness home
    "plugin/reflect.toml": False,
}

_ROOT_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}(/[^\s\"']+)")


def _referenced_paths(manifest: Path) -> set[str]:
    text = manifest.read_text()
    json.loads(text)  # fail loudly on an unparseable manifest
    return set(_ROOT_REF.findall(text))


def main() -> int:
    problems: list[str] = []

    for manifest, base in MANIFESTS.items():
        if not manifest.exists():
            continue
        try:
            refs = _referenced_paths(manifest)
        except (json.JSONDecodeError, OSError) as exc:
            problems.append(f"{manifest.relative_to(REPO)}: unreadable manifest ({exc})")
            continue
        for ref in sorted(refs):
            target = base / ref.lstrip("/")
            rel = f"{manifest.relative_to(REPO)} -> {ref}"
            if not target.exists():
                problems.append(f"missing hook file: {rel}")
            elif ref.endswith(".sh") and not os.access(target, os.X_OK):
                problems.append(f"hook script not executable: {rel}")

    for rel, needs_exec in EXTERNAL_CONTRACT.items():
        target = REPO / rel
        if not target.exists():
            problems.append(f"missing external-contract file: {rel}")
        elif needs_exec and not os.access(target, os.X_OK):
            problems.append(f"external-contract file not executable: {rel}")

    if problems:
        print("plugin contract BROKEN — the published package would break consumers:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("plugin contract OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
