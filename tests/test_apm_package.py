"""APM distribution channel: package generator + real `apm install -g` behaviour.

Two layers:

* Generator tests (no external tool): run ``scripts/build_apm_package.py`` and
  check the package it writes. Always run.
* Install tests: run the real ``apm`` CLI against the generated package with a
  FAKE ``$HOME`` and a throwaway project dir, so nothing under the real
  ``~/.claude``, ``~/.codex``, ``~/.copilot`` or ``~/.cursor`` is ever touched.
  They skip, with the install command in the message, when ``apm`` is not on
  PATH. CI installs the pinned version (.github/workflows/apm.yml).

What the install tests lock in is the spike result: one hook file per agent,
the whole runtime bundled under ``.apm/hooks/rt/``, 13/10/13/10 events for
Claude/Codex/Copilot/Cursor, and no collateral damage to a user's own hooks.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "build_apm_package.py"
PLUGIN_SKILLS = REPO_ROOT / "plugin" / "skills"

APM_PIN = "apm-cli==0.33.0"
NO_APM = f"apm not on PATH; install with: uv tool install {APM_PIN}"

TARGETS = "claude,codex,copilot,cursor"
EXPECTED_EVENTS = {"claude": 13, "codex": 10, "copilot": 13, "cursor": 10}
CURSOR_EVENTS = {
    "sessionStart",
    "sessionEnd",
    "beforeSubmitPrompt",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "subagentStart",
    "subagentStop",
    "preCompact",
    "stop",
}
# Parent-env vars the sandbox may inherit. Everything else (notably every *_HOME and
# *_CONFIG_DIR that moves where an agent keeps its config) is dropped.
ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "SYSTEMROOT")
# Vars that relocate agent/APM config or state; the leak test sets them all to bogus dirs.
LEAKY_ENV_VARS = (
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "COPILOT_HOME",
    "CURSOR_CONFIG_DIR",
    "HERMES_HOME",
    "APM_HOME",
    "APM_CACHE_DIR",
    "APM_TEMP_DIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
)
RUNTIME_MARK = "reflect/.apm/hooks/rt/"  # every reflect hook command contains this
USER_STOP = "echo user-stop-hook"


# --------------------------------------------------------------------------- helpers


def _build(out: Path) -> Path:
    subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    return out


def _tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file():
            h.update(f"{rel}\0{p.stat().st_mode & 0o111}\0".encode())
            h.update(p.read_bytes())
        else:
            h.update(f"{rel}/\0".encode())
    return h.hexdigest()


def _skill_names() -> list[str]:
    return sorted(d.name for d in PLUGIN_SKILLS.iterdir() if (d / "SKILL.md").is_file())


def _commands(node) -> list[str]:
    """Every ``command`` string anywhere in a hooks document."""
    if isinstance(node, dict):
        found = [node["command"]] if isinstance(node.get("command"), str) else []
        return found + [c for v in node.values() for c in _commands(v)]
    if isinstance(node, list):
        return [c for v in node for c in _commands(v)]
    return []


def _events_with_reflect(doc: dict) -> set[str]:
    return {
        ev
        for ev, groups in doc["hooks"].items()
        if any(RUNTIME_MARK in c for c in _commands(groups))
    }


def _all_reflect_commands(home: Path) -> list[str]:
    cmds: list[str] = []
    for rel in (".claude/settings.json", ".codex/hooks.json", ".cursor/hooks.json"):
        p = home / rel
        if p.exists():
            cmds += [c for c in _commands(json.loads(p.read_text())) if RUNTIME_MARK in c]
    for p in (home / ".copilot" / "hooks").glob("*.json"):
        cmds += [c for c in _commands(json.loads(p.read_text())) if RUNTIME_MARK in c]
    return cmds


class Sandbox:
    """A fake HOME with pre-existing user hooks, plus a throwaway project dir."""

    def __init__(self, root: Path, package: Path):
        self.home = root / "home"
        self.proj = root / "proj"
        self.package = package
        for d in (
            self.home / ".claude",
            self.home / ".codex",
            self.home / ".cursor",
            self.home / ".copilot" / "hooks",
            self.proj,
        ):
            d.mkdir(parents=True, exist_ok=True)
        user_hook = {"type": "command", "command": USER_STOP}
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps(
                {"model": "opus", "hooks": {"Stop": [{"matcher": "", "hooks": [user_hook]}]}}
            )
        )
        (self.home / ".codex" / "hooks.json").write_text(
            json.dumps({"hooks": {"Stop": [{"matcher": None, "hooks": [user_hook]}]}})
        )
        (self.home / ".cursor" / "hooks.json").write_text(
            json.dumps({"version": 1, "hooks": {"stop": [{"command": USER_STOP}]}})
        )
        (self.home / ".copilot" / "hooks" / "user.json").write_text(
            json.dumps(
                {"version": 1, "hooks": {"agentStop": [{"type": "command", "command": USER_STOP}]}}
            )
        )
        # Built from an allowlist, never from **os.environ: apm honours
        # CLAUDE_CONFIG_DIR, CODEX_HOME, COPILOT_HOME, APM_HOME, XDG_*, etc., and any of
        # them set in the developer's or CI's shell would send writes to real config dirs.
        self.env = {
            **{k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ},
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "NO_COLOR": "1",
        }

    def apm(self, *args: str) -> subprocess.CompletedProcess:
        # A wide terminal keeps APM from wrapping long paths in its output.
        env = {**self.env, "COLUMNS": "400"}
        return subprocess.run(
            ["apm", *args],
            cwd=self.proj,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    def install(self) -> subprocess.CompletedProcess:
        res = self.apm("install", "-g", str(self.package), "--target", TARGETS)
        assert res.returncode == 0, f"apm install failed:\n{res.stdout}\n{res.stderr}"
        return res

    def uninstall(self) -> subprocess.CompletedProcess:
        res = self.apm("uninstall", "-g", f"_local/{self.package.name}")
        assert res.returncode == 0, f"apm uninstall failed:\n{res.stdout}\n{res.stderr}"
        return res

    def load(self, rel: str) -> dict:
        return json.loads((self.home / rel).read_text())


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def package(tmp_path_factory) -> Path:
    # The leaf dir name becomes the package name APM installs under.
    return _build(tmp_path_factory.mktemp("apm-build") / "reflect")


@pytest.fixture(scope="module")
def installed(package, tmp_path_factory) -> Sandbox:
    if shutil.which("apm") is None:
        pytest.skip(NO_APM)
    box = Sandbox(tmp_path_factory.mktemp("apm-install"), package)
    box.install()
    return box


# --------------------------------------------------------------------------- generator


def test_generator_is_idempotent_and_overwrites(tmp_path):
    out = tmp_path / "reflect"
    _build(out)
    first = _tree_digest(out)
    (out / "stale.txt").write_text("left over from an older build")
    _build(out)
    assert not (out / "stale.txt").exists(), "rebuild must start from a clean directory"
    assert _tree_digest(out) == first


def test_generator_refuses_to_wipe_an_unrelated_directory(tmp_path):
    victim = tmp_path / "not-a-build"
    victim.mkdir()
    (victim / "precious.txt").write_text("keep me")
    # An apm.yml alone must not be taken as proof the directory is ours.
    (victim / "apm.yml").write_text("name: someone-elses-package\n")
    res = subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(victim)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert (victim / "precious.txt").read_text() == "keep me"


def test_generator_wipes_only_what_it_marked(tmp_path):
    out = tmp_path / "reflect"
    _build(out)
    assert (out / ".reflect-apm-build").is_file()
    (out / "stale.txt").write_text("x")
    (out / ".reflect-apm-build").unlink()  # no marker: no longer provably ours
    res = subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert (out / "stale.txt").exists(), "unmarked directory must not be deleted"
    # An existing empty dir and a plain file are handled without deleting anything.
    empty = tmp_path / "empty"
    empty.mkdir()
    _build(empty)
    assert (empty / "apm.yml").is_file()
    a_file = tmp_path / "a-file"
    a_file.write_text("keep")
    res = subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(a_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0
    assert a_file.read_text() == "keep"


def _load_generator():
    spec = importlib.util.spec_from_file_location("build_apm_package", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cursor_mapping_fails_loudly_on_an_unmapped_event():
    gen = _load_generator()
    hook = {"matcher": "", "hooks": [{"type": "command", "command": "x"}]}
    # Known events (mapped or deliberately dropped) are fine.
    ok = {"hooks": {"Stop": [hook], "Notification": [hook]}}
    assert set(gen.to_cursor(ok)["hooks"]) == {"stop"}
    # A brand-new Claude event must not be silently dropped for Cursor.
    with pytest.raises(SystemExit, match="BrandNewEvent"):
        gen.to_cursor({"hooks": {"BrandNewEvent": [hook]}})
    assert gen.CURSOR_DROPPED_EVENTS.isdisjoint(gen.CURSOR_EVENT_MAP)


def test_generator_honours_the_plugin_dir_argument(package, tmp_path):
    """Exclusions are relative to the plugin dir passed in, not the repo's own."""
    gen = _load_generator()
    copy = tmp_path / "plugin-copy"
    shutil.copytree(REPO_ROOT / "plugin", copy, ignore=shutil.ignore_patterns("__pycache__"))
    out = gen.build(tmp_path / "out" / "reflect", plugin_dir=copy)
    assert _tree_digest(out) == _tree_digest(package)


def test_package_layout(package):
    assert (package / "apm.yml").is_file()
    assert {p.name for p in (package / ".apm" / "skills").iterdir()} == set(_skill_names())
    for name in ("claude", "codex", "copilot", "cursor"):
        assert (package / ".apm" / "hooks" / f"{name}-hooks.json").is_file()
    rt = package / ".apm" / "hooks" / "rt"
    assert (rt / "scripts" / "hook_input.py").is_file(), (
        "runtime scripts/ must ship under .apm/hooks/rt/"
    )
    assert (rt / "reflect.toml").is_file()
    assert os.access(rt / "hooks" / "reflect-drain-bg.sh", os.X_OK), "shell hook lost its exec bit"
    for excluded in ("tests", "adapters", ".claude-plugin", "codex-hooks.json", "plugin.json"):
        assert not (rt / excluded).exists(), f"{excluded} must not be bundled"
    assert not (rt / "scripts" / "tests").exists()
    assert not list(package.rglob("__pycache__"))


@pytest.mark.parametrize("agent", sorted(EXPECTED_EVENTS))
def test_generated_hook_files_event_counts(package, agent):
    doc = json.loads((package / ".apm" / "hooks" / f"{agent}-hooks.json").read_text())
    assert len(doc["hooks"]) == EXPECTED_EVENTS[agent]


def test_every_generated_hook_command_resolves_inside_the_package(package):
    pattern = re.compile(r"\$\{PLUGIN_ROOT\}/(\S+?\.(?:py|sh))")
    seen = 0
    for hooks_file in (package / ".apm" / "hooks").glob("*-hooks.json"):
        text = hooks_file.read_text()
        assert "${CLAUDE_PLUGIN_ROOT}" not in text, (
            f"{hooks_file.name} still has the Claude root var"
        )
        for rel in pattern.findall(text):
            assert (package / rel).is_file(), f"{hooks_file.name} points at missing {rel}"
            seen += 1
    assert seen > 40


def test_copilot_keeps_its_harness_env_and_cursor_is_camel_case(package):
    copilot = (package / ".apm" / "hooks" / "copilot-hooks.json").read_text()
    assert "REFLECT_HARNESS=copilot" in copilot
    cursor = json.loads((package / ".apm" / "hooks" / "cursor-hooks.json").read_text())
    assert cursor["version"] == 1
    assert set(cursor["hooks"]) == CURSOR_EVENTS


# --------------------------------------------------------------------------- apm install


@pytest.mark.skipif(shutil.which("apm") is None, reason=NO_APM)
def test_install_event_counts_per_target(installed):
    claude = installed.load(".claude/settings.json")
    codex = installed.load(".codex/hooks.json")
    cursor = installed.load(".cursor/hooks.json")
    copilot_files = [
        f for f in (installed.home / ".copilot" / "hooks").glob("*.json") if f.name != "user.json"
    ]
    assert len(copilot_files) == 1, copilot_files
    copilot = json.loads(copilot_files[0].read_text())

    assert len(_events_with_reflect(claude)) == EXPECTED_EVENTS["claude"]
    assert len(_events_with_reflect(codex)) == EXPECTED_EVENTS["codex"]
    assert len(_events_with_reflect(copilot)) == EXPECTED_EVENTS["copilot"]
    assert len(_events_with_reflect(cursor)) == EXPECTED_EVENTS["cursor"]

    # Cursor must get camelCase names (APM writes PascalCase for a universal file).
    assert set(cursor["hooks"]) == CURSOR_EVENTS
    assert not {e for e in cursor["hooks"] if e[0].isupper()}
    # Copilot keeps the harness env prefix its recall hooks branch on.
    assert any("REFLECT_HARNESS=copilot" in c for c in _commands(copilot))
    assert not {e for e in copilot["hooks"] if e[0].isupper()}


@pytest.mark.skipif(shutil.which("apm") is None, reason=NO_APM)
def test_install_deploys_skills(installed):
    names = _skill_names()
    assert len(names) == 10
    for root in (".claude/skills", ".agents/skills"):
        got = sorted(p.name for p in (installed.home / root).iterdir())
        assert got == names, f"{root}: {got}"
        for n in names:
            assert (installed.home / root / n / "SKILL.md").is_file()


@pytest.mark.skipif(shutil.which("apm") is None, reason=NO_APM)
def test_installed_hook_commands_point_at_files_that_exist(installed):
    """The silent-failure mode from the spike: hooks wired, runtime missing."""
    cmds = _all_reflect_commands(installed.home)
    assert len(cmds) > 40
    pattern = re.compile(r"(/\S+?\.(?:py|sh))")
    for cmd in cmds:
        paths = pattern.findall(cmd)
        assert paths, cmd
        for p in paths:
            assert p.startswith(str(installed.home)), f"hook path escapes the fake HOME: {p}"
            assert Path(p).is_file(), f"hook points at a missing file: {p}"
    for target in (".claude", ".codex", ".cursor"):
        rt = installed.home / target / "hooks" / "reflect" / ".apm" / "hooks" / "rt"
        assert (rt / "scripts" / "hook_input.py").is_file(), (
            f"{target}: runtime scripts/ not deployed"
        )


@pytest.mark.skipif(shutil.which("apm") is None, reason=NO_APM)
def test_install_preserves_user_hooks_and_settings(installed):
    claude = installed.load(".claude/settings.json")
    assert claude["model"] == "opus"
    assert USER_STOP in _commands(claude["hooks"]["Stop"])
    assert USER_STOP in _commands(installed.load(".codex/hooks.json")["hooks"]["Stop"])
    assert USER_STOP in _commands(installed.load(".cursor/hooks.json")["hooks"]["stop"])
    assert USER_STOP in (installed.home / ".copilot" / "hooks" / "user.json").read_text()


@pytest.mark.skipif(shutil.which("apm") is None, reason=NO_APM)
def test_uninstall_is_clean(package, tmp_path):
    box = Sandbox(tmp_path, package)
    before = {
        rel: (box.home / rel).read_text()
        for rel in (
            ".claude/settings.json",
            ".codex/hooks.json",
            ".cursor/hooks.json",
            ".copilot/hooks/user.json",
        )
    }
    box.install()
    assert _all_reflect_commands(box.home), (
        "install wired nothing; the uninstall check would be vacuous"
    )
    box.uninstall()

    assert _all_reflect_commands(box.home) == []
    # User content is semantically identical to what was there before install.
    for rel, text in before.items():
        assert json.loads((box.home / rel).read_text()) == json.loads(text), rel
    names = _skill_names()
    for root in (".claude/skills", ".agents/skills"):
        left = [n for n in names if (box.home / root / n).exists()]
        assert left == [], f"{root} still has {left}"
    for target in (".claude", ".codex", ".cursor"):
        assert not (box.home / target / "hooks" / "reflect").exists()
    assert not [f for f in (box.home / ".copilot" / "hooks").iterdir() if f.name != "user.json"]


@pytest.mark.skipif(shutil.which("apm") is None, reason=NO_APM)
def test_sandbox_ignores_config_relocating_env_vars(package, tmp_path, monkeypatch):
    """Even with every config-relocating var set in the parent env, apm writes only
    under the fake HOME and never to the paths those vars point at."""
    bogus_root = tmp_path / "bogus-real-config"
    for var in LEAKY_ENV_VARS:
        monkeypatch.setenv(var, str(bogus_root / var))
    box = Sandbox(tmp_path / "box", package)
    for var in LEAKY_ENV_VARS:
        if var.startswith("XDG_"):  # redirected into the fake HOME, not dropped
            assert str(box.env.get(var, box.home)).startswith(str(box.home)), var
        else:
            assert var not in box.env, f"{var} leaked into the sandbox env"
    box.install()
    box.uninstall()
    assert not bogus_root.exists(), (
        f"apm wrote outside the fake HOME: {list(bogus_root.rglob('*'))}"
    )
    assert _all_reflect_commands(box.home) == []
    # The install itself did land in the fake HOME (not vacuous).
    assert (box.home / ".apm").is_dir()
