#!/usr/bin/env python3
# ABOUTME: Stdlib-only secret redaction for the plugin scripts (the drain's
# ABOUTME: LLM-bound slice and bounded input). Vendored from the engine's
# ABOUTME: reflect_kb.issues.sanitize.redact_secrets; a parity test pins them equal.
"""Redact credentials from text that is about to leave the machine.

The plugin scripts are stdlib-only by contract (they run under the harness's
python, where reflect-kb may live in an isolated tool venv), so the engine's
``redact_secrets`` cannot be imported here. This module carries the same
pattern table, the same generic KEY=value rule and the same capture posture
(``looks_like_credential``); ``plugin/tests/test_secret_redact_parity.py``
runs both on the fixture corpus and the over-redaction cases and asserts
identical output, with the vendored path called explicitly, so they cannot
drift apart silently.

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
        re.compile(r"https://hooks\.slack\.com/services/[^\s)\]\"',]+"),
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
# A numeral prefix on a word (1password, 2fa, 3rd): letters and digits, but
# not the shape of a credential segment.
_NUMERAL_WORD_RE = re.compile(r"^\d{1,2}[A-Za-z]+$")
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


def _mixes_letters_and_digits(text: str) -> bool:
    return any(ch.isalpha() for ch in text) and any(ch.isdigit() for ch in text)


def looks_like_credential(value: str) -> bool:
    """The capture posture: never lose a legitimate value. A generic
    KEY=value match is treated as a credential only when the value has the
    shape of one: 12 or more characters that are not a number or a path; an
    ALL_CAPS identifier only counts as one when it carries an underscore or
    is shorter than 16 (a base32 seed, upper hex or a licence key is all
    caps too); a hyphen, underscore or dot separated slug is a credential
    when any segment of four or more characters mixes letters and digits
    (a numeral-prefixed word such as 1password is not); otherwise a value
    that mixes letters and digits, or a long high-entropy one, is one."""
    v = value.strip()
    if len(v) < 12 or _NUMBER_RE.match(v) or _PATH_RE.match(v):
        return False
    if _ALL_CAPS_IDENT_RE.match(v) and ("_" in v or len(v) < 16):
        return False
    if _SLUG_RE.match(v):
        return any(
            len(seg) >= 4 and _mixes_letters_and_digits(seg) and not _NUMERAL_WORD_RE.match(seg)
            for seg in re.split(r"[-_.]", v)
        )
    if v.startswith(("/", "./", "~/")) or "(" in v:
        return False
    if _mixes_letters_and_digits(v):
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
        return f"{m.group(1)}{m.group(2)}{m.group(3)}<REDACTED:generic_secret>{m.group(3)}"

    return _GENERIC_SECRET_RE.sub(_generic, out)


# Resolved once: the engine's canonical function when reflect-kb is
# importable, else the vendored table above. A per-call import attempt cost
# more than the redaction itself when the engine was absent.
try:
    from reflect_kb.issues.sanitize import redact_secrets as _engine_redact_secrets
except ImportError:  # stdlib-only layout
    _engine_redact_secrets = None


def redact_secrets_text(text: str) -> str:
    """Secrets-only redaction: tokens, keys, PEM blocks, generic KEY=value.

    Paths, ids, emails and commit shas survive (a transcript slice needs
    them). The engine's ``redact_secrets`` runs when importable; the
    vendored copy otherwise, and the parity test keeps the two identical.
    """
    if _engine_redact_secrets is not None:
        return _engine_redact_secrets(text).text
    return _redact_local(text)
