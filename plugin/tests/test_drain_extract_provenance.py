"""The drain writes repo, commit and source_path from the session's checkout."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import drain_extract


def _repo(tmp_path: Path, remote: str) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True)
    (root / "src").mkdir()
    (root / "src" / "auth.rs").write_text("fn main() {}\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    subprocess.run([*git, "remote", "add", "origin", remote], check=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return root, sha


def test_git_provenance_normalises_ssh_and_https_remotes(tmp_path: Path) -> None:
    root, sha = _repo(tmp_path, "git@github.com:acme/widgets.git")
    assert drain_extract.git_provenance(str(root)) == {"repo": "acme/widgets", "commit": sha}
    subprocess.run(["git", "-C", str(root), "remote", "set-url", "origin", "https://github.com/acme/widgets"], check=True)
    assert drain_extract.git_provenance(str(root))["repo"] == "acme/widgets"
    assert drain_extract.git_provenance(str(tmp_path)) == {}


@pytest.mark.parametrize("url,repo", [
    ("https://github.com/acme/widgets.git", "acme/widgets"),
    ("git@github.com:acme/widgets.git", "acme/widgets"),
    ("https://gitlab.example.com/group/subgroup/project.git", "group/subgroup/project"),
    ("git@gitlab.example.com:group/subgroup/project.git", "group/subgroup/project"),
    ("https://dev.azure.com/org/project/_git/repo", "org/project/_git/repo"),
    ("git@ssh.dev.azure.com:v3/org/project/repo", "v3/org/project/repo"),
    ("ssh://git@github.com:22/acme/widgets.git/", "acme/widgets"),
    ("https://github.com/acme", ""),  # no repository segment
    ("not a remote", ""),
])
def test_repo_from_remote_keeps_the_full_path(url: str, repo: str) -> None:
    assert drain_extract.repo_from_remote(url) == repo


def test_render_md_writes_the_pin_keys_only_when_all_three_are_known(tmp_path: Path) -> None:
    root, sha = _repo(tmp_path, "git@github.com:acme/widgets.git")
    prov = drain_extract.git_provenance(str(root))
    learning = {"title": "JWT expiry", "category": "auth", "key_insight": "k", "file": "src/auth.rs"}
    md = drain_extract.render_md(learning, source_path="/tmp/t.jsonl", session_id="s", provenance=prov)
    import re

    def has(key: str, value: str) -> bool:
        return re.search(rf'^{key}: "?{re.escape(value)}"?$', md, re.MULTILINE) is not None

    assert has("repo", "acme/widgets") and has("commit", sha) and has("source_path", "src/auth.rs"), md
    assert has("source_transcript", "/tmp/t.jsonl")
    from reflect_kb.pinning import pinned_source_uri

    fm = {"repo": "acme/widgets", "commit": sha, "source_path": "src/auth.rs"}
    assert pinned_source_uri(fm) == f"acme/widgets@{sha}:src/auth.rs"
    no_file = drain_extract.render_md({**learning, "file": ""}, source_path="/tmp/t.jsonl", session_id="s", provenance=prov)
    assert "repo:" not in no_file and "source_path:" not in no_file
    traversal = drain_extract.render_md({**learning, "file": "../etc/passwd"}, source_path="", session_id="s", provenance=prov)
    assert "source_path:" not in traversal


def test_pin_falls_back_to_nested_provenance() -> None:
    from reflect_kb.pinning import pinned_source_uri

    fm = {"provenance": {"repo": "acme/widgets", "commit": "a" * 40, "source_path": "src/x.py"}}
    assert pinned_source_uri(fm) == "acme/widgets@" + "a" * 40 + ":src/x.py"
