"""The docs that a security review reads are checked against the tree:
the migration list equals the directory, every variable a setup table names
has a placeholder in .env.example, the hooks README's allow-rules row is the
library's rule list, and the egress page names every claude -p call site."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SETUP = REPO / "docs" / "setup.md"
README = REPO / "README.md"
EGRESS = REPO / "docs" / "WHAT-LEAVES-THE-MACHINE.md"
HOOKS_README = REPO / "plugin" / "hooks" / "README.md"
ENV_EXAMPLE = REPO / ".env.example"
MIGRATIONS = REPO / "supabase" / "migrations"
LIB = REPO / "plugin" / "hooks" / "lib" / "writer_argv.sh"


def test_setup_md_migration_table_and_psql_block_equal_the_directory() -> None:
    files = sorted(p.name for p in MIGRATIONS.glob("*.sql"))
    assert files, MIGRATIONS
    text = SETUP.read_text(encoding="utf-8")
    section = text[text.index("## 3. Apply the migrations"):text.index("## 4. Seed")]
    table = re.findall(r"^\| (\d+) \| `(\d{4}_[a-z0-9_]+\.sql)` \|", section, re.MULTILINE)
    assert [name for _, name in table] == files, (table, files)
    assert [int(n) for n, _ in table] == list(range(1, len(files) + 1))
    block = re.findall(r"-f supabase/migrations/(\S+\.sql)", section)
    assert block == files, (block, files)


def _env_names(markdown: str, header_marker: str) -> set[str]:
    section = markdown[markdown.index(header_marker):]
    names: set[str] = set()
    in_table = False
    for line in section.splitlines():
        if line.startswith("| `"):
            in_table = True
            names |= set(re.findall(r"`((?:REFLECT|SUPABASE|DATABASE)_[A-Z_]+)`", line.split("|")[1]))
        elif in_table and not line.startswith("|"):
            break
    return names


def test_every_documented_variable_has_a_placeholder_in_env_example() -> None:
    placeholders = {m.group(1) for m in re.finditer(r"^([A-Z_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE)}
    setup_vars = _env_names(SETUP.read_text(encoding="utf-8"), "### Required values")
    broker_vars = _env_names(README.read_text(encoding="utf-8"), "| Variable | Meaning | Default |")
    assert setup_vars and broker_vars
    missing = (setup_vars | broker_vars) - placeholders
    assert not missing, f".env.example lacks {sorted(missing)}"


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs bash")
def test_hooks_readme_allow_rules_row_is_the_library_rule_list() -> None:
    proc = subprocess.run(["bash", "-c", 'source "$1"; drain_writer_rules; printf "%s\\0" "${WRITER_RULES[@]}"', "x", str(LIB)],
                          capture_output=True, check=True, timeout=30)
    rules = [p.decode() for p in proc.stdout.split(b"\0")[:-1]]
    row = next(line for line in HOOKS_README.read_text(encoding="utf-8").splitlines() if line.startswith("| Writer allow rules |"))
    documented = re.findall(r"`([^`]+)`", row.split("|")[3])
    assert documented == rules, f"README row {documented} != lib rules {rules}"


def test_egress_page_names_every_claude_p_call_site() -> None:
    spec = importlib.util.spec_from_file_location("call_sites", REPO / "plugin" / "tests" / "test_claude_p_call_sites.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    text = EGRESS.read_text(encoding="utf-8")
    table = text[text.index("### Every `claude -p` path"):text.index("## 2. Model weights")]
    for path in module.CALL_SITES:
        assert f"`{path}`" in table, f"egress page does not list {path}"
    assert "REFLECT_NESTED=1" in table
