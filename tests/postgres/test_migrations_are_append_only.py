"""Shipped migrations are never edited in place: an incremental upgrade
(supabase db push, any migration table) skips an applied file, so a change
inside 0003 after it shipped never reached an upgraded database (the read
functions kept their 0001 bodies). Every change lands in a new file, and the
files below are frozen by digest; a new migration adds its own line here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

SHIPPED = {
    "0001_reflect_memory_phase1.sql": "dd3cbab1c84dcc459cc4141d65338bb93d854c3295a10f6beda94efb0d9fc99a",
    "0002_nanographrag_pgvector.sql": "5d5aa74f5789f585f7d4cb371947ef33d437d15a6a2823ce729582226b16cfee",
    "0003_classification_force_rls.sql": "a822d2ce559974d0d51b6aba2a9f321bf4da2a524a0c45d048a9b3b01741ecc7",
    "0004_broker_and_writer_roles.sql": "752bd7c357bbef93e1714d65e00f3c20e05ea2261ef3795a767d98e6e976208a",
    "0005_rls_policies_initplan.sql": "6cbcd01b1706888fc4547642050c5b3f4c9be9659b25ab48fd0f27f37326275a",
    "0006_read_functions_shareable_floor.sql": "4cea78e5f994f1fa22ff23397f5557f23c102833acacb0af91e0cc6e3708d2f6",
}


def test_every_shipped_migration_is_byte_identical() -> None:
    present = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in MIGRATIONS.glob("*.sql")}
    changed = {n for n, d in SHIPPED.items() if present.get(n) != d}
    assert not changed, f"shipped migrations edited in place (add a new file instead): {sorted(changed)}"
    unlisted = set(present) - set(SHIPPED)
    assert not unlisted, f"new migration files must be listed here with their digest: {sorted(unlisted)}"


def test_0003_defines_no_read_function() -> None:
    text = (MIGRATIONS / "0003_classification_force_rls.sql").read_text(encoding="utf-8")
    for fn in ("search_memory", "search_entities", "entity_neighborhood"):
        assert f"function reflect_memory.{fn}" not in text, fn
