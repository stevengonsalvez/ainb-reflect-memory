"""recall reads the transcript path the drain writes (source_transcript,
else provenance.source_path), and still the fleet importer's source_path."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "recall" / "scripts"))
import recall  # noqa: E402


class _Lrn:
    def __init__(self, fm):
        self.frontmatter = fm


def test_source_transcript_wins_then_provenance_then_legacy_source_path() -> None:
    assert recall._fleet_source_path(_Lrn({"source_transcript": "/t/s.jsonl", "source_path": "src/auth.rs"})) == "/t/s.jsonl"
    assert recall._fleet_source_path(_Lrn({"provenance": {"source_path": "/t/s.jsonl"}, "source_path": "src/auth.rs"})) == "/t/s.jsonl"
    assert recall._fleet_source_path(_Lrn({"source_path": "fleet/patterns.jsonl"})) == "fleet/patterns.jsonl"
    assert recall._fleet_source_path(_Lrn({})) == ""
