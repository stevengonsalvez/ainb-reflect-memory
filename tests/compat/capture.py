#!/usr/bin/env python3
"""Capture what one checkout of reflect installs and does.

Standalone (stdlib plus the tree it is pointed at), so the compat gate can run
the SAME capture against the merge-base checkout and against the branch, then
diff the two. Never import branch code here: everything comes from ``--tree``.

    capture.py --tree <repo root> --home <throwaway HOME> --kind <kind> \\
               [--fixtures <dir>] [--python <interpreter>]

Kinds:
  install-claude | install-codex | install-copilot
      Run that harness adapter's install into HOME; report the installed tree
      (exec bit + hash of normalized text), every hook command, and whether
      each hook command's script path exists in the layout.
  behaviour
      On a KB seeded from the tree's e2e fixture: reflect add on a legacy note
      and on a note carrying a fixture secret, reindex and search in Mode 1,
      the SessionStart recall hook, the cascade slice and bounded input on a
      recorded transcript, and the extract writer with a canned model reply.

Output is one JSON object on stdout. Machine-specific strings (HOME, the tree,
timestamps, content-hash filename suffixes) are normalized so two captures
compare cleanly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # tests/ for _support
from _support.hermetic import hermetic_env, minimal_path

HARNESS_DIR = {"claude": ".claude", "codex": ".codex", "copilot": ".copilot", "hermes": ".hermes"}
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
_DOC_HASH_RE = re.compile(r"-[0-9a-f]{6}(\.(?:md|entities\.yaml))$")
# Any install-time marker that survived rendering, of either kind.
# The two install-time marker shapes rendering resolves; runtime template
# variables in asset templates ({{DATE}}) are not markers.
_UNRESOLVED_RE = re.compile(r"\{\{HOME_TOOL_DIR\}\}|\$\{CLAUDE_PLUGIN_ROOT")
FAKE_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"


class Capture:
    def __init__(self, tree: Path, home: Path, fixtures: Path, python: str) -> None:
        self.tree = tree.resolve()
        self.home = home.resolve()
        self.fixtures = fixtures.resolve()
        self.python = python
        self.kb = self.home / ".learnings"
        self.state = self.home / ".reflect"
        (self.kb / "documents").mkdir(parents=True, exist_ok=True)
        self.state.mkdir(exist_ok=True)
        (self.home / "bin").mkdir(exist_ok=True)
        self.reflect_bin = self._write_reflect_shim()

    # ------------------------------------------------------------------ env
    def _write_reflect_shim(self) -> Path:
        """``reflect`` that runs THIS tree's CLI through the test interpreter."""
        shim = self.home / "bin" / "reflect"
        shim.write_text(
            "#!/bin/sh\n"
            f'export PYTHONPATH="{self.tree / "src"}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
            f'exec "{self.python}" -m reflect_kb.cli.main "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        return shim

    def env(self) -> dict[str, str]:
        env = hermetic_env(
            kb_dir=self.kb, state_dir=self.state, cache_home=self.home / ".cache",
            base={}, home=self.home,
            path=str(self.home / "bin") + os.pathsep + minimal_path("uv", "git"),
        )
        env["REFLECT_DRAIN_NO_DELEGATE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # A crash in a captured process (a SIGILL from a native wheel on some
        # runners) prints a traceback naming the module instead of a bare -4.
        env["PYTHONFAULTHANDLER"] = "1"
        env["PYTHONPATH"] = str(self.tree / "src")
        # The hook caps each recall subprocess (default 30s); a cold uv env
        # plus a cold embedding model needs more, and a timeout reads as an
        # empty inject, which is a false behaviour diff.
        env["REFLECT_RECALL_TIMEOUT"] = "300"
        return env

    def run(self, cmd: list[str], *, cwd: Path | None = None, stdin: str | None = None,
            timeout: int = 1800) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, env=self.env(), cwd=str(cwd or self.home), input=stdin,
                              capture_output=True, text=True, timeout=timeout, check=False)

    def norm(self, text: str) -> str:
        # Fixture data is shared by both captures and lives in the branch's
        # tests/ dir, so it gets its own token before the tree token.
        out = text.replace(str(self.home), "$HOME")
        out = out.replace(str(self.fixtures.parents[1]), "$TESTS")
        out = out.replace(str(self.tree), "$TREE")
        return _TS_RE.sub("<TS>", out)

    # -------------------------------------------------------------- install
    def install(self, harness: str) -> dict[str, Any]:
        adapter = self.tree / "plugin" / "adapters" / harness / f"{harness}_adapter.py"
        proc = self.run([self.python, str(adapter), "install", "--home", str(self.home)])
        if proc.returncode != 0:
            raise SystemExit(f"{harness} install failed:\n{proc.stdout}\n{proc.stderr}")
        root = self.home / HARNESS_DIR[harness]
        tree: dict[str, Any] = {}
        unresolved: dict[str, list[str]] = {}
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = str(path.relative_to(root))
            if "__pycache__" in rel:
                continue
            raw = path.read_bytes()
            try:
                text = self.norm(raw.decode("utf-8"))
            except UnicodeDecodeError:
                tree[rel] = {"exec": os.access(path, os.X_OK), "sha": hashlib.sha256(raw).hexdigest()[:16]}
                continue
            entry: dict[str, Any] = {"exec": os.access(path, os.X_OK)}
            if path.name == "SKILL.md":
                # Full text, so a whitelist can prove new == render(old).
                entry["text"] = text
            else:
                entry["sha"] = hashlib.sha256(text.encode()).hexdigest()[:16]
            tree[rel] = entry
            # Every installed text file must be free of install-time markers:
            # hook snippets and reference docs are handed to the model too.
            found = sorted(set(_UNRESOLVED_RE.findall(text)))
            if found:
                unresolved[rel] = found
        hooks: dict[str, list[str]] = {}
        for cfg in sorted(list(root.glob("*.json")) + list((root / "hooks").glob("*.json"))):
            hooks[str(cfg.relative_to(root))] = self._hook_commands(cfg)
        return {
            "harness": harness,
            "tree": tree,
            "hooks": hooks,
            "hook_paths": self._hook_paths_exist(hooks),
            "unresolved": unresolved,
        }

    def _hook_commands(self, cfg: Path) -> list[str]:
        out: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                cmd = node.get("command")
                if isinstance(cmd, str):
                    out.append(cmd)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        try:
            walk(json.loads(cfg.read_text(encoding="utf-8")))
        except ValueError:
            return ["<unparseable>"]
        return sorted(self.norm(c) for c in out)

    def _hook_paths_exist(self, hooks: dict[str, list[str]]) -> dict[str, bool]:
        """For every path token in a hook command that lives under HOME: does it exist?"""
        found: dict[str, bool] = {}
        for cmds in hooks.values():
            for cmd in cmds:
                for token in re.findall(r"\$HOME/[^\s\"')&;]+", cmd):
                    real = Path(token.replace("$HOME", str(self.home)))
                    found[token] = real.exists()
        return found

    # ------------------------------------------------------------ behaviour
    def behaviour(self) -> dict[str, Any]:
        for src in (self.tree / "tests" / "e2e" / "fixture-kb" / "documents").iterdir():
            shutil.copy(src, self.kb / "documents" / src.name)
        out: dict[str, Any] = {}
        # Index, search and recall run on the pristine fixture KB first, so
        # their captures do not depend on notes whose text a later branch is
        # allowed to change (redaction). add and extract come after and are
        # compared on their own written files.
        out["reindex"], out["search"] = self._reindex_and_search()
        out["recall"] = self._recall_hook()
        out["add"] = self._add()
        out["cascade"] = self._cascade()
        out["extract"] = self._extract()
        return out

    def _notes(self) -> dict[str, str]:
        return {
            _DOC_HASH_RE.sub(r"-<hash>\1", p.name): self.norm(p.read_text(encoding="utf-8"))
            for p in sorted((self.kb / "documents").glob("*")) if p.is_file()
        }

    def _add(self) -> dict[str, Any]:
        before = set(self._notes())
        results = {}
        for note in (self.tree / "tests" / "samples" / "tokio-runtime-nested-panic.md",
                     self.fixtures / "legacy-note-with-secret.md"):
            proc = self.run([str(self.reflect_bin), "add", "--force", str(note)])
            # Only the exit code is compared; rich wraps paths across lines in
            # stderr, which defeats HOME normalization. Keep stderr on failure.
            results[note.name] = {"exit": proc.returncode}
            if proc.returncode != 0:
                results[note.name]["stderr_tail"] = self.norm(proc.stderr[-400:])
        after = self._notes()
        return {"runs": results, "added": {k: v for k, v in after.items() if k not in before}}

    def _reindex_and_search(self) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            __import__("nano_graphrag")
        except ImportError:
            return {"skipped": "graph extra not installed"}, {"skipped": "graph extra not installed"}
        proc = self.run([str(self.reflect_bin), "reindex", "--force"])
        m = re.search(r"Indexed (\d+) documents", proc.stdout + proc.stderr)
        reindex = {"exit": proc.returncode, "indexed": int(m.group(1)) if m else None}
        proc = self.run([str(self.reflect_bin), "search", "jwt token expiry", "--format", "json"])
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            payload = {}
        context = payload.get("context") or ""
        search = {
            "exit": proc.returncode,
            "mode": payload.get("mode"),
            "ranked": re.findall(r"^id:\s*(\S+)", context, re.MULTILINE),
        }
        return reindex, search

    def _recall_hook(self) -> dict[str, Any]:
        project = self.home / "proj"
        project.mkdir(exist_ok=True)
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        self.run([*git, "init", "-q", "--initial-branch=main"], cwd=project)
        self.run([*git, "commit", "-q", "--allow-empty", "-m", "fix jwt auth token expiry check"],
                 cwd=project)
        hook = self.tree / "plugin" / "skills" / "recall" / "hooks" / "session_start_recall.py"
        # Warm uv's script env for recall.py so the hook's first call is not a
        # cold dependency resolve.
        recall = self.tree / "plugin" / "skills" / "recall" / "scripts" / "recall.py"
        if shutil.which("uv", path=self.env()["PATH"]) and recall.exists():
            self.run(["uv", "run", "--quiet", str(recall), "--help"], cwd=project, timeout=600)
        proc = self.run([self.python, str(hook)], cwd=project,
                        stdin=json.dumps({"cwd": str(project), "session_id": "compat"}), timeout=600)
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except ValueError:
            payload = {"unparseable": proc.stdout[:200]}
        hso = payload.get("hookSpecificOutput") if isinstance(payload, dict) else None
        context = (hso or {}).get("additionalContext", "") if isinstance(hso, dict) else ""
        return {
            "exit": proc.returncode,
            "keys": sorted(payload) if isinstance(payload, dict) else type(payload).__name__,
            "hook_keys": sorted(hso) if isinstance(hso, dict) else [],
            "context_nonempty": bool(context.strip()),
            "context": self.norm(context),
            "stderr_tail": self.norm(proc.stderr[-300:]) if (proc.returncode != 0 or not context.strip()) else "",
        }

    def _cascade(self) -> dict[str, Any]:
        script = (
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import reflect_cascade\n"
            "t, out, bout = sys.argv[2], sys.argv[3], sys.argv[4]\n"
            "prep = reflect_cascade.prepare(t, out_path=out)\n"
            "fields = {k: (str(v) if v is not None else None) for k, v in vars(prep).items()"
            " if isinstance(v, (str, int, float, bool)) or v is None}\n"
            "b = reflect_cascade.bound_transcript(t, out_path=bout, max_chars=4000)\n"
            "print(json.dumps({'prep': fields, 'bounded': {k: v for k, v in b.items() if k != 'path'}}))\n"
        )
        slice_out, bounded_out = self.home / "slice.txt", self.home / "bounded.txt"
        proc = self.run([self.python, "-c", script, str(self.tree / "plugin" / "scripts"),
                         str(self.fixtures.parent.parent / "fixtures" / "transcripts" / "recorded-session.jsonl"),
                         str(slice_out), str(bounded_out)])
        if proc.returncode != 0:
            return {"exit": proc.returncode, "stderr_tail": self.norm(proc.stderr[-400:])}
        data = json.loads(proc.stdout)
        data["prep"] = {k: (self.norm(v) if isinstance(v, str) else v) for k, v in data["prep"].items()}
        data["slice"] = self.norm(slice_out.read_text(encoding="utf-8")) if slice_out.exists() else None
        data["bounded"]["text"] = self.norm(bounded_out.read_text(encoding="utf-8")) if bounded_out.exists() else None
        data["exit"] = 0
        return data

    def _extract(self) -> dict[str, Any]:
        actions = {"actions": [{
            "action": "CREATE", "reason": "new durable rule",
            "learning": {
                "title": "Registry tokens come from the keychain", "category": "deploy",
                "key_insight": "Read REGISTRY_TOKEN from the keychain at push time; never export it",
                "problem": f"deploy.sh exported REGISTRY_TOKEN={FAKE_TOKEN} into the shell",
                "root_cause": "token echoed by the deploy script", "fix": "read from keychain",
                "rule": "never export registry tokens", "confidence_num": 0.9,
                "tags": ["deploy", "secrets"], "entities": ["deploy.sh", "keychain"],
            },
        }]}
        envelope = {"type": "result", "is_error": False, "result": json.dumps(actions),
                    "num_turns": 1, "total_cost_usd": 0.01,
                    "usage": {"input_tokens": 100, "output_tokens": 50}}
        stub = self.home / "bin" / "claude"
        stub.write_text("#!/usr/bin/env bash\ncat <<'EOF'\n" + json.dumps(envelope) + "\nEOF\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        slice_f = self.home / "extract-slice.txt"
        slice_f.write_text("# slice\nuser: never export the registry token\nassistant: agreed\n")
        transcript = self.fixtures.parent.parent / "fixtures" / "transcripts" / "recorded-session.jsonl"
        script = (
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import drain_extract\n"
            "drain_extract._now_iso = lambda: '2026-08-30T12:00:00+00:00'\n"
            "s = drain_extract.run(slice_path=sys.argv[2], transcript=sys.argv[3], session_id='rec-1',"
            " model='stub', timeout=120, claude_bin=sys.argv[4], reflect_bin=sys.argv[5], cwd=sys.argv[6])\n"
            "print(json.dumps({k: s.get(k) for k in ('created','updated','deleted','retryable_failure','errors')}))\n"
        )
        before = set(self._notes())
        proc = self.run([self.python, "-c", script, str(self.tree / "plugin" / "scripts"), str(slice_f),
                         str(transcript), str(stub), str(self.reflect_bin), str(self.home)], timeout=900)
        if proc.returncode != 0:
            return {"exit": proc.returncode, "stderr_tail": self.norm(proc.stderr[-400:])}
        summary = json.loads(proc.stdout)
        summary["errors"] = [self.norm(str(e)) for e in summary.get("errors") or []]
        after = self._notes()
        return {"exit": 0, "summary": summary, "written": {k: v for k, v in after.items() if k not in before}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", required=True, type=Path)
    ap.add_argument("--home", required=True, type=Path)
    ap.add_argument("--kind", required=True)
    ap.add_argument("--fixtures", type=Path, default=HERE / "fixtures")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    cap = Capture(a.tree, a.home, a.fixtures, a.python)
    if a.kind.startswith("install-"):
        result = cap.install(a.kind.split("-", 1)[1])
    elif a.kind == "behaviour":
        result = cap.behaviour()
    else:
        ap.error(f"unknown kind {a.kind!r}")
    json.dump(result, sys.stdout, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
