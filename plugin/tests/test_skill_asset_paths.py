"""Every asset/script path a SKILL.md cites must actually resolve.

These paths are read by a model at runtime, so a wrong one never raises. It
just sends the writer shopping. reflect/SKILL.md cited
`assets/learning_template.md` six times, but assets/ lives at the PLUGIN ROOT,
not next to SKILL.md, so every drain run burned ~3 turns: guess
plugin/skills/reflect/assets/ (missing), fall back to `find`, then read the
real path. Against an 8-turn cap that is most of the budget, and the run hit
max_turns and wrote no learning while still costing ~$0.60.

Two resolution bases are legitimate, so the test checks that a path resolves
against ONE of them rather than mandating a spelling:

  skill-local   plugin/skills/<skill>/scripts/x   -> a bare `scripts/x` is fine
  plugin root   plugin/assets/x                   -> needs
                                                     ${CLAUDE_PLUGIN_ROOT}/plugin/assets/x

The install root is the repo root (hooks resolve ${CLAUDE_PLUGIN_ROOT}/plugin/
...), which is why plugin-root paths carry the `plugin/` segment.
"""

import re
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]      # plugin/
_REPO_ROOT = _PLUGIN_ROOT.parent                        # == ${CLAUDE_PLUGIN_ROOT}
_SKILLS = sorted(_PLUGIN_ROOT.glob("skills/*/SKILL.md"))

_DIRS = ("assets", "scripts", "references")

_ROOT_REF = re.compile(r'\$\{CLAUDE_PLUGIN_ROOT[^}]*\}/([A-Za-z0-9_./-]+)')
_BARE_REF = re.compile(r'`(' + "|".join(_DIRS) + r')/([A-Za-z0-9_./-]+)`')
# A runnable script path invoked inside a ```bash fence, e.g.
# `plugins/reflect/skills/recall/scripts/recall.py` — no backticks, no anchor,
# so it resolves against the model's cwd and fails. This is the class the
# inline-backtick guard above misses.
_FENCE_SCRIPT = re.compile(r'^\s*(?:python3?\s+|uv\s+run\s+)?([A-Za-z0-9_][\w./-]*\.py)\b', re.M)


@pytest.mark.parametrize("skill", _SKILLS, ids=lambda p: p.parent.name)
def test_bare_refs_resolve_skill_locally(skill: Path):
    """A bare `scripts/x` is only legible if it sits next to the SKILL.md."""
    broken = []
    for m in _BARE_REF.finditer(skill.read_text()):
        rel = f"{m.group(1)}/{m.group(2)}"
        if (skill.parent / rel).exists():
            continue                                  # skill-local, resolves
        where = "plugin root" if (_PLUGIN_ROOT / rel).exists() else "nowhere"
        broken.append(f"{rel} (actually at: {where})")
    assert not broken, (
        f"{skill.parent.name}/SKILL.md cites {broken} relative to itself, but they "
        f"do not resolve there. The model cannot follow the path and will spend "
        f"turns hunting. Use ${{CLAUDE_PLUGIN_ROOT}}/plugin/<dir>/... for "
        f"plugin-root files."
    )


@pytest.mark.parametrize("skill", _SKILLS, ids=lambda p: p.parent.name)
def test_fence_script_paths_are_anchored(skill: Path):
    """A .py invoked in a code fence must carry an anchor, not a bare path.

    A bare `plugins/reflect/.../recall.py` in a ```bash block resolves against
    the user's cwd and fails. Every runnable script path must start with an
    anchor (${CLAUDE_PLUGIN_ROOT} or a bootstrap {{HOME_TOOL_DIR}} placeholder)
    or be a genuinely skill-local relative path that exists next to SKILL.md.
    """
    unanchored = []
    for m in _FENCE_SCRIPT.finditer(skill.read_text()):
        path = m.group(1)
        if path.startswith(("${", "{{", "/", "~")):
            continue                                  # anchored / absolute
        if (skill.parent / path).exists():
            continue                                  # genuinely skill-local
        unanchored.append(path)
    assert not unanchored, (
        f"{skill.parent.name}/SKILL.md runs unanchored script path(s) {unanchored} "
        f"in a code fence; they resolve against the model's cwd and fail. Prefix "
        f"with ${{CLAUDE_PLUGIN_ROOT}}/plugin/..."
    )


@pytest.mark.parametrize("skill", _SKILLS, ids=lambda p: p.parent.name)
def test_plugin_root_refs_resolve(skill: Path):
    """Every ${CLAUDE_PLUGIN_ROOT}/... path must exist on disk."""
    for m in _ROOT_REF.finditer(skill.read_text()):
        rel = m.group(1)
        if any(ch in rel for ch in "<>${"):           # placeholder, not a path
            continue
        assert (_REPO_ROOT / rel).exists(), (
            f"{skill.parent.name}/SKILL.md cites ${{CLAUDE_PLUGIN_ROOT}}/{rel}, "
            f"which does not exist. Plugin-root paths need the 'plugin/' segment "
            f"(the install root is the repo root, not plugin/)."
        )
