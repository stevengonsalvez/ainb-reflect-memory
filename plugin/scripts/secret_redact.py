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
    # Userinfo in a URL (``postgresql://app:hunter2@db``, ``redis://:pw@host``):
    # the password is replaced, the scheme, user and host stay readable. First,
    # so a password that also looks like a token is still read as a password.
    # A placeholder (``…``, ``***``, ``PASS``) or an env reference
    # (``$PGPASSWORD``) is not a password and stays.
    (
        re.compile(
            r"\b([A-Za-z][A-Za-z0-9+.\-]{1,20}://)([^\s:/@'\"<>]{0,256}):"
            r"(?![\u2026.*]{1,8}@|\$\{?\w{1,64}\}?@|[A-Z][A-Z_]{0,31}@)[^\s/@'\"<>]{1,256}@"
        ),
        r"\1\2:<REDACTED:url_password>@",
        "url_password",
    ),
    # Every prefix rule is anchored at a word boundary: without it ``task-abc...``
    # matched the ``sk-`` rule from its second letter.
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"), "<REDACTED:anthropic_key>", "anthropic_key"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}"), "<REDACTED:openai_key>", "openai_key"),
    (re.compile(r"\bsk-[A-Za-z0-9_]{20,}"), "<REDACTED:openai_key>", "openai_key"),
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
    # GitLab personal-access / pipeline tokens (glpat-...). No generic-keyword
    # anchor, so without this prefix rule a bare token leaks into a published
    # issue unredacted.
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}"), "<REDACTED:gitlab_token>", "gitlab_token"),
    # Google API keys (AIza...). Fixed 39-char total in practice; match 30 or more
    # of body to stay tolerant without being greedy.
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}\b"), "<REDACTED:google_api_key>", "google_api_key"),
    # npm automation/access tokens (npm_...).
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"), "<REDACTED:npm_token>", "npm_token"),
    # Hugging Face access tokens (hf_...).
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), "<REDACTED:huggingface_token>", "huggingface_token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "<REDACTED:slack_token>", "slack_token"),
    # Slack and Discord incoming-webhook URLs carry a posting credential in the
    # path, over http as well as https.
    (
        re.compile(r"https?://hooks\.slack\.com/services/[^\s)\]\"',]+"),
        "<REDACTED:slack_webhook>",
        "slack_webhook",
    ),
    (
        re.compile(r"https?://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/[^\s)\]\"',]+"),
        "<REDACTED:discord_webhook>",
        "discord_webhook",
    ),
    # Long-lived (AKIA) and temporary STS (ASIA) access key ids.
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<REDACTED:aws_key>", "aws_key"),
    # Stripe secret/restricted API keys (sk_live_..., rk_test_...). No generic-keyword
    # anchor, so without this prefix rule a bare live key leaks unredacted.
    (
        re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b"),
        "<REDACTED:stripe_key>",
        "stripe_key",
    ),
    # Webhook signing secrets (whsec_...).
    (re.compile(r"\bwhsec_[A-Za-z0-9+/=]{24,}"), "<REDACTED:webhook_secret>", "webhook_secret"),
    (
        re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b"),
        "<REDACTED:telegram_token>",
        "telegram_token",
    ),
    # A token starts where a run of token characters starts (not after a
    # hyphen inside one), so a long ``eyJ-eyJ-...`` run with no dot is tried
    # once instead of once per ``eyJ``, which was quadratic.
    (
        re.compile(r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        "<REDACTED:jwt>",
        "jwt",
    ),
    # Any PEM private key (RSA, EC, OPENSSH, ENCRYPTED, PGP ... BLOCK). A block
    # pasted without its END line is redacted to the end of the text. Both
    # bodies are bounded (a 16384-bit RSA key is about 13 KB), so a text of
    # unterminated BEGIN lines costs one bounded scan per redaction instead
    # of one scan to the end per line.
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----"
            r"(?:[\s\S]{0,16000}?-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----|[\s\S]{0,16000})"
        ),
        "<REDACTED:private_key>",
        "private_key",
    ),
    # ``Authorization: Basic <base64>``: the scheme stays, the credential goes.
    (
        re.compile(r"(\b(?i:authorization)['\"]?\s*[:=]\s*['\"]?(?i:basic)\s+)[A-Za-z0-9+/=]{8,}"),
        r"\1<REDACTED:basic_auth>",
        "basic_auth",
    ),
    # ``Authorization: Bearer <token>``, strip the credential, keep the scheme
    # so the line still reads. Runs after the more specific token rules above so
    # a ``Bearer eyJ...`` JWT is already redacted by the time we get here; this
    # catches opaque bearer tokens with no recognizable prefix. Only the token
    # is replaced (group 1, the ``Bearer `` scheme, is preserved). Any case;
    # the token carries a digit or is 15 characters long, so prose such as
    # "bearer authentication" (14) is not a token but a purely alphabetic
    # opaque token is.
    (
        re.compile(
            r"(\b(?i:bearer)\s+)"
            r"(?=[A-Za-z0-9_\-.=+/]{0,256}\d|[A-Za-z0-9_\-.=+/]{15})[A-Za-z0-9_\-.=+/]{12,}"
        ),
        r"\1<REDACTED:bearer_token>",
        "bearer_token",
    ),
]

# Generic KEY=value / KEY: value secret. The keyword set is load-bearing
# (mirrors agent-deck GENERIC_SECRET_RE). The value floor is 8 characters so a
# password or secret key catches short passphrases; every other key still
# needs 12 (``_publish_keeps`` and ``looks_like_credential`` enforce it).
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
# after a lowercase letter). Surrounding ``\w`` runs still let the keyword match
# anywhere inside the identifier; group 1 captures the FULL env-var name so
# it's preserved verbatim and only the value is stripped.
# Both runs are bounded (64): unbounded, a single identifier made of repeated
# keywords (``key_key_key...``) backtracked quadratically. No possessive
# quantifiers: the plugin copy must compile on a python older than 3.11. An identifier with its keyword more than 64 characters in is
# not matched.
# The key may carry a closing quote before the separator and the value an
# opening one, so the JSON form (``"api_key": "..."``) that a JSONL transcript
# uses matches as well as ``KEY=value`` and ``key: value``, and so does the
# escaped form inside a JSON string (``{\"api_key\": \"...\"}``).
_GENERIC_SECRET_RE = re.compile(
    r"\b(\w{0,64}(?:"
    r"(?i:(?<![A-Za-z])(?:token|key|secret|password|passwd|api[_-]?key|auth(?:orization)?)(?![A-Za-z]))"
    r"|(?<=[a-z])(?:Token|Key|Secret|Password|Passwd|ApiKey|Auth|Authorization)(?![a-z])"
    r")\w{0,64})"
    r"((?:\\?['\"])?\s*[:=]\s*)"
    r"((?:\\?['\"])?)(?!<REDACTED:)([^\s'\"]{8,})\3"
)


_ALL_CAPS_IDENT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9]+(?:[-_.:][A-Za-z0-9]+)+$")
_NUMBER_RE = re.compile(r"^[0-9][0-9._-]*$")
# A numeral prefix on a word (1password, 2fa, 3rd): letters and digits, but
# not the shape of a credential segment.
_NUMERAL_WORD_RE = re.compile(r"^\d{1,2}[A-Za-z]+$")
# A path: slash-separated segments of at most 15 word characters each
# (exports/2026/report.csv, ~/.config/gh/hosts.yml). A base64 credential with
# slashes has long segments and never looks like this.
_PATH_RE = re.compile(r"^(?:~|\.{1,2})?/?[\w.-]{1,15}(?:/[\w.-]{1,15})+/?$")
# Values that name or identify something rather than grant access: a UUID, a
# content digest, a URL with no userinfo and no query, a dotted identifier
# (settings.page.title2, users.id2fk_constraint).
_UUID_VALUE_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DIGEST_RE = re.compile(r"^(?:md5|sha1|sha224|sha256|sha384|sha512):[0-9a-fA-F]{16,}$")
_PLAIN_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]{1,20}://[^\s@?#]+$")
_DOTTED_IDENT_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
# A key that holds a password or secret outright: its value is a credential
# from 8 characters, words and passphrases included. ``password_hint`` or
# ``secret_name`` describe one and keep the ordinary rule.
_STRICT_KEY_RE = re.compile(r"(?:^|[_\-.])(?i:password|passwd|secret)$|(?<=[a-z])(?:Password|Passwd|Secret)$")
# A key that names a credential rather than an identifier. Many services issue
# UUID-shaped tokens, so under one of these names a UUID is key material and
# the UUID exemption must not rescue it; under ``idempotencyKey``,
# ``cache_key`` or ``request_id`` the same value names something and stays.
# The keyword sits at a sub-token boundary, as in _GENERIC_SECRET_RE, so
# ``author`` is not an ``auth`` and ``monkey_id`` is not a key.
_CREDENTIAL_KEY_RE = re.compile(
    r"(?i:(?<![A-Za-z])(?:token|secret|password|passwd|api[_-]?key|auth(?:orization)?)(?![A-Za-z]))"
    r"|(?<=[a-z])(?:Token|Secret|Password|Passwd|ApiKey|Auth|Authorization)(?![a-z])"
)


def _shannon_bits(value: str) -> float:
    import math

    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _mixes_letters_and_digits(text: str) -> bool:
    return any(ch.isalpha() for ch in text) and any(ch.isdigit() for ch in text)


def _random_segment(seg: str) -> bool:
    """A slug segment shaped like generated key material: letters and digits
    alternating (Ab12Cd34, AB12C), not an identifier with a trailing or
    embedded number (user42, oauth2, abc1, id2fk)."""
    if len(seg) < 4 or not _mixes_letters_and_digits(seg) or _NUMERAL_WORD_RE.match(seg):
        return False
    # zip, not itertools.pairwise: the plugin copy runs on python 3.9.
    flips = sum(1 for a, b in zip(seg, seg[1:]) if a.isdigit() != b.isdigit())  # noqa: RUF007
    return flips >= 3 or (flips >= 2 and any(ch.isupper() for ch in seg))


def _names_a_reference(value: str) -> bool:
    return bool(
        _PATH_RE.match(value) or _DIGEST_RE.match(value) or _PLAIN_URL_RE.match(value)
        or _DOTTED_IDENT_RE.match(value)
    )


def looks_like_credential(value: str) -> bool:
    """The capture posture: never lose a legitimate value. A generic
    KEY=value match is treated as a credential only when the value has the
    shape of one: 12 or more characters that are not a number, a path, a
    UUID, a content digest (sha256:...), a URL without userinfo or query, or a
    dotted identifier; an ALL_CAPS identifier only counts as one when it is
    shorter than 16 without an underscore (a base32 seed, upper hex or a
    licence key is all caps too) or is three or more equal underscore groups
    of four or more (ABCD_EFGH_IJKL); a hyphen, underscore, dot or colon
    separated slug is a credential when a segment alternates letters and
    digits or two segments of four or more mix them (a numeral-prefixed word
    such as 1password is not); otherwise a value that mixes letters and
    digits, or a long high-entropy one, is one."""
    v = value.strip()
    if len(v) < 12 or _NUMBER_RE.match(v) or _UUID_VALUE_RE.match(v) or _names_a_reference(v):
        return False
    if _ALL_CAPS_IDENT_RE.match(v) and ("_" in v or len(v) < 16):
        groups = v.split("_")
        return len(groups) >= 3 and len(groups[0]) >= 4 and len({len(g) for g in groups}) == 1
    if _SLUG_RE.match(v):
        segs = re.split(r"[-_.:]", v)
        mixed = [s for s in segs if len(s) >= 4 and _mixes_letters_and_digits(s) and not _NUMERAL_WORD_RE.match(s)]
        return len(mixed) >= 2 or any(_random_segment(s) for s in segs)
    if v.startswith(("/", "./", "~/")) or "(" in v:
        return False
    if _mixes_letters_and_digits(v):
        return True
    return len(v) >= 20 and _shannon_bits(v) >= 3.9


# ``key_insight`` contains the substring ``key`` at a sub-token boundary, so a
# one-word insight of 12+ chars would otherwise be redacted as a credential.
_CAPTURE_EXEMPT_KEYS = frozenset({"key_insight"})


def _capture_keeps(key: str, value: str) -> bool:
    """True when the capture posture leaves a generic KEY=value match alone."""
    if key.lower() in _CAPTURE_EXEMPT_KEYS:
        return True
    v = value.strip()
    if _STRICT_KEY_RE.search(key):
        # Only a value that points at the credential survives: a path, a URL,
        # a settings attribute, an env var name, a call or a $reference.
        return (
            len(v) < 8 or _names_a_reference(v) or "(" in v or v.startswith("$")
            or bool(_ALL_CAPS_IDENT_RE.match(v) and "_" in v)
        )
    # looks_like_credential exempts every UUID by shape, so the key decides:
    # a UUID under a token, auth or api-key name is a credential.
    if _UUID_VALUE_RE.match(v) and _CREDENTIAL_KEY_RE.search(key):
        return False
    return not looks_like_credential(v)


# -- end vendored --


def _redact_local(text: str) -> str:
    out = text
    for pattern, replacement, _kind in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)

    def _generic(m: re.Match[str]) -> str:
        # The capture posture, same test as the engine's redact_secrets.
        if _capture_keeps(m.group(1), m.group(4)):
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
