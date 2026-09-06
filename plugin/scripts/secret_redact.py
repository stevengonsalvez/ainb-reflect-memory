#!/usr/bin/env python3
# ABOUTME: Stdlib-only secret redaction for the plugin scripts (the drain's
# ABOUTME: LLM-bound slice and bounded input). Vendored from the engine's
# ABOUTME: reflect_kb.issues.sanitize.redact_secrets; a parity test pins them equal.
"""Redact credentials from text that is about to leave the machine.

The plugin scripts are stdlib-only by contract (they run under the harness's
python, where reflect-kb may live in an isolated tool venv), so the engine's
``redact_secrets`` cannot be imported here. This module carries the same
pattern table, the same generic KEY=value rule and the same capture posture
(``looks_like_credential``); ``plugin/tests/test_secret_redact.py``
asserts the two stay identical, so they cannot drift apart silently.

The engine's function is preferred when importable, so a full-stack install
always runs the canonical code.
"""

from __future__ import annotations

import re

__all__ = ["redact_secrets_text"]

# -- vendored from reflect_kb.issues.sanitize (do not edit here; edit the engine) --
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Every prefix rule is anchored at a word boundary: without it ``task-abc...``
    # matched the ``sk-`` rule from its second letter.
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), "<REDACTED:anthropic_key>", "anthropic_key"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}"), "<REDACTED:openai_key>", "openai_key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "<REDACTED:openai_key>", "openai_key"),
    # Fine-grained GitHub PATs (github_pat_<22>_<59>) must precede the classic
    # gh[posru]_ rule, the underscore mid-body and the ``i`` after ``gh`` make
    # them invisible to that pattern, so a bare token would otherwise pass
    # through unredacted into a published issue.
    (
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}"),
        "<REDACTED:github_token>",
        "github_token",
    ),
    (re.compile(r"\bgh[posru]_[A-Za-z0-9]{20,}"), "<REDACTED:github_token>", "github_token"),
    # GitLab personal-access / pipeline tokens (glpat-…). No generic-keyword
    # anchor, so without this prefix rule a bare token leaks into a published
    # issue unredacted.
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}"), "<REDACTED:gitlab_token>", "gitlab_token"),
    # Google API keys (AIza…). Fixed 39-char total in practice; match ≥30 of
    # body to stay tolerant without being greedy.
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}\b"), "<REDACTED:google_api_key>", "google_api_key"),
    # npm automation/access tokens (npm_…).
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"), "<REDACTED:npm_token>", "npm_token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "<REDACTED:slack_token>", "slack_token"),
    # Slack incoming-webhook URLs carry a posting credential in the path.
    (
        re.compile(r"https://hooks\.slack\.com/services/\S+"),
        "<REDACTED:slack_webhook>",
        "slack_webhook",
    ),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<REDACTED:aws_key>", "aws_key"),
    # Stripe secret/restricted API keys (sk_live_…, rk_test_…). No generic-keyword
    # anchor, so without this prefix rule a bare live key leaks unredacted.
    (
        re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b"),
        "<REDACTED:stripe_key>",
        "stripe_key",
    ),
    (
        re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b"),
        "<REDACTED:telegram_token>",
        "telegram_token",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        "<REDACTED:jwt>",
        "jwt",
    ),
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
            r".*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
            re.DOTALL,
        ),
        "<REDACTED:private_key>",
        "private_key",
    ),
    # ``Authorization: Bearer <token>``, strip the credential, keep the scheme
    # so the line still reads. Runs after the more specific token rules above so
    # a ``Bearer eyJ…`` JWT is already redacted by the time we get here; this
    # catches opaque bearer tokens with no recognizable prefix. Only the token
    # is replaced (group 1, the ``Bearer `` scheme, is preserved).
    (
        re.compile(r"(\bBearer\s+)[A-Za-z0-9_\-.=+/]{12,}"),
        r"\1<REDACTED:bearer_token>",
        "bearer_token",
    ),
]

# Generic KEY=value / KEY: value secret. The keyword set and the {12,} value
# length threshold are load-bearing (mirrors agent-deck GENERIC_SECRET_RE).
# The ``(?!<REDACTED:)`` guard stops this coarse rule from re-redacting a
# placeholder a more specific earlier rule already wrote (e.g. a PEM block that
# happened to follow the literal text "key:").
# The keyword is allowed to be EMBEDDED in a longer identifier. ``_`` is a word
# char, so a strict ``\b(key)\b`` boundary fails on ``AWS_SECRET_ACCESS_KEY``,
# ``GITLAB_TOKEN``, ``GOOGLE_API_KEY``, ``NPM_TOKEN``, the keyword sits between
# word chars, never on a boundary, so the whole assignment passes through
# unredacted. But matching the keyword as a bare substring with NO boundary at
# all over-matches plain English words that happen to contain it, ``author``,
# ``monkey``, ``keyboard``, as false-positive "secrets". The keyword must
# instead sit at an identifier *sub-token* boundary: either non-letter-bounded
# (``AWS_SECRET_ACCESS_KEY``, underscores either side) or a camelCase
# transition (``apiKey``, ``authToken``, a capitalized keyword immediately
# after a lowercase letter). Surrounding ``\w*`` still lets the keyword match
# anywhere inside the identifier; group 1 captures the FULL env-var name so
# it's preserved verbatim and only the value is stripped.
# The key may carry a closing quote before the separator and the value an
# opening one, so the JSON form (``"api_key": "..."``) that a JSONL transcript
# uses matches as well as ``KEY=value`` and ``key: value``.
_GENERIC_SECRET_RE = re.compile(
    r"\b(\w*(?:"
    r"(?i:(?<![A-Za-z])(?:token|key|secret|password|passwd|api[_-]?key|auth(?:orization)?)(?![A-Za-z]))"
    r"|(?<=[a-z])(?:Token|Key|Secret|Password|Passwd|ApiKey|Auth|Authorization)(?![a-z])"
    r")\w*)"
    r"(['\"]?\s*[:=]\s*)"
    r"(['\"]?)(?!<REDACTED:)([^\s'\"]{12,})\3"
)


_ALL_CAPS_IDENT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)+$")
_NUMBER_RE = re.compile(r"^[0-9][0-9._-]*$")
# A path: slash-separated segments of at most 15 word characters each
# (exports/2026/report.csv, ~/.config/gh/hosts.yml). A base64 credential with
# slashes has long segments and never looks like this.
_PATH_RE = re.compile(r"^(?:~|\.{1,2})?/?[\w.-]{1,15}(?:/[\w.-]{1,15})+/?$")


def _shannon_bits(value: str) -> float:
    import math

    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def looks_like_credential(value: str) -> bool:
    """The capture posture: never lose a legitimate value. A generic
    KEY=value match is treated as a credential only when the value has the
    shape of one: 16 or more characters that are not an ALL_CAPS identifier,
    not a plain slug of hyphen, underscore or dot separated words, not a
    number, and either mix letters with digits or carry high entropy."""
    v = value.strip()
    if len(v) < 16 or _ALL_CAPS_IDENT_RE.match(v) or _NUMBER_RE.match(v) or _PATH_RE.match(v):
        return False
    if _SLUG_RE.match(v) and not any(ch.isdigit() for ch in v.replace("-", "").replace("_", "").replace(".", "")):
        return False
    if _SLUG_RE.match(v):
        segments = re.split(r"[-_.]", v)
        if all(len(seg) <= 8 and (seg.isalpha() or seg.isdigit() or seg[0].isalpha()) for seg in segments):
            return False  # user-123-profile-v2: a slug with a counter, not a credential
    letters = any(ch.isalpha() for ch in v)
    digits = any(ch.isdigit() for ch in v)
    if letters and digits and not v.startswith(("/", "./", "~/")) and "(" not in v:
        return True
    return len(v) >= 20 and _shannon_bits(v) >= 3.9


# ``key_insight`` contains the substring ``key`` at a sub-token boundary, so a
# one-word insight of 12+ chars would otherwise be redacted as a credential.
_CAPTURE_EXEMPT_KEYS = frozenset({"key_insight"})
# -- end vendored --


def _redact_local(text: str) -> str:
    out = text
    for pattern, replacement, _kind in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)

    def _generic(m: re.Match[str]) -> str:
        # The capture posture, same test as the engine's redact_secrets.
        if m.group(1).lower() in _CAPTURE_EXEMPT_KEYS or not looks_like_credential(m.group(4)):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}<REDACTED:generic_secret>"

    return _GENERIC_SECRET_RE.sub(_generic, out)


def redact_file(src: str, dst: str) -> int:
    """Write a redacted copy of ``src`` to ``dst``; return the number of
    characters that changed (0 when nothing matched)."""
    with open(src, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    out = redact_secrets_text(text)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(out)
    return abs(len(text) - len(out)) if out != text else 0


def redact_secrets_text(text: str) -> str:
    """Secrets-only redaction: tokens, keys, PEM blocks, generic KEY=value.

    Paths, ids, emails and commit shas survive (a transcript slice needs
    them). Uses the engine's ``redact_secrets`` when importable.
    """
    try:
        from reflect_kb.issues.sanitize import redact_secrets

        return redact_secrets(text).text
    except ImportError:
        return _redact_local(text)


if __name__ == "__main__":  # secret_redact.py --in FILE --out FILE
    import argparse
    import sys

    _ap = argparse.ArgumentParser(description="write a redacted copy of a file")
    _ap.add_argument("--in", dest="src", required=True)
    _ap.add_argument("--out", dest="dst", required=True)
    _args = _ap.parse_args()
    try:
        _changed = redact_file(_args.src, _args.dst)
    except OSError as exc:
        print(f"redaction failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(_changed)
