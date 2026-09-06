"""Every installed text file is rendered for its layout, not only SKILL.md."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
sys.path.insert(0, str(PLUGIN / "adapters"))
from base import UNRESOLVED_MARKER, AdapterBase, render_for_layout


class _BaseRender:
    """A stand-in adapter that renders the way the base does."""

    def render_for_layout(self, text: str, dst: Path) -> str:
        return render_for_layout(text, dst)


def test_install_file_renders_text_and_keeps_bytes_and_mode(tmp_path: Path) -> None:
    src = tmp_path / "src" / "settings-snippet.json"
    src.parent.mkdir()
    src.write_text('{"command": "uv run {{HOME_TOOL_DIR}}/skills/recall/hooks/x.py ${CLAUDE_PLUGIN_ROOT}/hooks/y.py"}')
    src.chmod(0o755)
    dst = tmp_path / "home" / ".claude" / "skills" / "reflect" / "hooks" / "settings-snippet.json"
    AdapterBase.install_file(_BaseRender(), src, dst)
    out = dst.read_text()
    assert not UNRESOLVED_MARKER.search(out), out
    assert f"{tmp_path}/home/.claude/skills/recall/hooks/x.py" in out
    assert f"{tmp_path}/home/.claude/skills/reflect/hooks/y.py" in out
    assert os.access(dst, os.X_OK)
    blob = tmp_path / "src" / "img.bin"
    blob.write_bytes(b"\xff\xfe{{HOME_TOOL_DIR}}")
    AdapterBase.install_file(_BaseRender(), blob, tmp_path / "home" / ".claude" / "skills" / "reflect" / "assets" / "img.bin")
    assert (tmp_path / "home" / ".claude" / "skills" / "reflect" / "assets" / "img.bin").read_bytes() == b"\xff\xfe{{HOME_TOOL_DIR}}"


def test_render_resolves_both_anchor_spellings_at_any_depth(tmp_path: Path) -> None:
    dst = Path("/h/.codex/skills/reflect/references/deep/doc.md")
    text = "a ${CLAUDE_PLUGIN_ROOT}/plugin/references/x.md b ${CLAUDE_PLUGIN_ROOT}/hooks/y.sh c ${CLAUDE_PLUGIN_ROOT}/plugin/skills/recall/scripts/z.py"
    out = render_for_layout(text, dst)
    assert out == "a /h/.codex/skills/reflect/references/x.md b /h/.codex/skills/reflect/hooks/y.sh c /h/.codex/skills/recall/scripts/z.py"


def test_install_file_renders_through_the_subclass_override(tmp_path: Path) -> None:
    """install_file is an instance method: an adapter that overrides
    render_for_layout sees it applied to every synced file, not only the
    SKILL.md it renders itself."""
    class Marked:
        def render_for_layout(self, text: str, dst: Path) -> str:
            return "RENDERED-BY-SUBCLASS\n" + text

    src = tmp_path / "src" / "note.md"
    src.parent.mkdir()
    src.write_text("body {{HOME_TOOL_DIR}}\n", encoding="utf-8")
    dst = tmp_path / "home" / ".claude" / "skills" / "reflect" / "note.md"
    AdapterBase.install_file(Marked(), src, dst)
    assert dst.read_text(encoding="utf-8").startswith("RENDERED-BY-SUBCLASS\n")
