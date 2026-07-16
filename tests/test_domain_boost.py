"""F3: unit tests for domain/authority norms and quarantine filtering.

``domain_norm`` mirrors R16's ``project_norm``: a matching hint boosts to the
ceiling, everything else (different domain, no hint, domainless note) sits at
the neutral 0.5 so a hintless query is byte-identical to the pre-F3 ordering.
``authority_norm`` ranks law/promoted over advisory over archived, with unknown
sitting neutral. Quarantine excludes fleet-imported notes from the default
recall scope; the fleet-context path opts in.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugin" / "skills" / "recall" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from recall import (  # noqa: E402
    Learning,
    authority_norm,
    domain_norm,
    filter_by_quarantine,
)

# corpus.py is the M7 saved-filter slice — its own quarantine exclusion.
from reflect_kb.recall.corpus import CorpusFilter  # noqa: E402


def _learning(**fm) -> Learning:
    return Learning(chunk_text=fm.get("title", ""), frontmatter=fm)


# --- domain_norm ----------------------------------------------------------


def test_domain_match_is_ceiling():
    assert domain_norm("personal", "personal") == 1.0


def test_domain_mismatch_is_neutral():
    assert domain_norm("personal", "coding") == 0.5


def test_no_hint_is_neutral():
    assert domain_norm(None, "coding") == 0.5
    assert domain_norm("", "coding") == 0.5


def test_domainless_note_is_neutral():
    assert domain_norm("personal", "") == 0.5


def test_domain_match_is_case_insensitive():
    assert domain_norm("Personal", "personal") == 1.0


# --- authority_norm -------------------------------------------------------


def test_authority_tiers_ordered():
    assert authority_norm("law") == 1.0
    assert authority_norm("promoted") == 1.0
    assert authority_norm("advisory") == 0.5
    assert authority_norm("archived") == 0.0


def test_authority_unknown_is_neutral():
    assert authority_norm("") == 0.5
    assert authority_norm("whatever") == 0.5


# --- Learning frontmatter properties --------------------------------------


def test_learning_quarantine_bool_and_string():
    assert _learning(quarantine=True).quarantine is True
    assert _learning(quarantine="true").quarantine is True
    assert _learning(quarantine="no").quarantine is False
    assert _learning().quarantine is False


def test_learning_domain_and_authority_normalized():
    lrn = _learning(domain="Personal", authority="LAW")
    assert lrn.domain == "personal"
    assert lrn.authority == "law"
    assert _learning().domain == ""
    assert _learning().authority == ""


# --- filter_by_quarantine -------------------------------------------------


def test_quarantine_excluded_by_default():
    clean = _learning(title="clean")
    quar = _learning(title="quar", quarantine=True)
    out = filter_by_quarantine([clean, quar], include_quarantined=False)
    assert out == [clean]


def test_quarantine_included_when_flagged():
    clean = _learning(title="clean")
    quar = _learning(title="quar", quarantine=True)
    out = filter_by_quarantine([clean, quar], include_quarantined=True)
    assert out == [clean, quar]


# --- CorpusFilter quarantine exclusion ------------------------------------


def test_corpus_filter_excludes_quarantined_by_default():
    filt = CorpusFilter()
    assert filt.matches({"quarantine": True}, None) is False
    assert filt.matches({"category": "x"}, None) is True


def test_corpus_filter_admits_quarantined_when_opted_in():
    filt = CorpusFilter(include_quarantined=True)
    assert filt.matches({"quarantine": True}, None) is True


def test_corpus_filter_roundtrips_include_quarantined():
    filt = CorpusFilter(include_quarantined=True)
    assert CorpusFilter.from_dict(filt.to_dict()).include_quarantined is True
