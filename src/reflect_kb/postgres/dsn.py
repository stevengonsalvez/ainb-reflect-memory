"""Transport rules for a Postgres DSN, shared by the writer path and the broker.

Notes, vectors and graph cross the network on this DSN, so a DSN that reaches
another host must pin an encrypting ``sslmode`` (``require``, ``verify-ca``,
``verify-full``). A loopback or Unix-socket server is exempt: nothing leaves
the machine. ``REFLECT_PG_ALLOW_INSECURE=1`` is the single, explicit opt-out.
"""

from __future__ import annotations

import os
import urllib.parse
from collections.abc import Mapping

__all__ = ["InsecureDSNError", "assert_tls", "is_local_dsn", "requires_tls"]

TLS_MODES = ("require", "verify-ca", "verify-full")
ALLOW_INSECURE_VAR = "REFLECT_PG_ALLOW_INSECURE"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


class InsecureDSNError(RuntimeError):
    """A network DSN without TLS, and no explicit opt-out."""


def _conninfo(dsn: str) -> dict[str, str]:
    try:
        from psycopg.conninfo import conninfo_to_dict

        return {k: str(v) for k, v in conninfo_to_dict(dsn).items() if v is not None}
    except Exception:  # noqa: BLE001 - psycopg missing or unparseable: fall back to the URI form
        if "://" not in dsn:
            return {}
        parts = urllib.parse.urlsplit(dsn)
        info = {"host": parts.hostname or ""}
        for key, values in urllib.parse.parse_qs(parts.query).items():
            info[key] = values[-1]
        return info


def is_local_dsn(dsn: str, env: Mapping[str, str] | None = None) -> bool:
    """True when every host the DSN can reach is this machine."""
    env = os.environ if env is None else env
    info = _conninfo(dsn)
    hosts = [h for h in info.get("host", "").split(",") if h] or [env.get("PGHOST", "")]
    addrs = [a for a in info.get("hostaddr", "").split(",") if a] or [env.get("PGHOSTADDR", "")]
    return all(h.strip("[]").lower() in _LOCAL_HOSTS or h.startswith("/") for h in hosts + addrs)


def requires_tls(dsn: str) -> bool:
    """True when the DSN pins an encrypting sslmode."""
    return _conninfo(dsn).get("sslmode", "") in TLS_MODES


def assert_tls(dsn: str, *, what: str = "Postgres DSN", env: Mapping[str, str] | None = None) -> None:
    """Raise :class:`InsecureDSNError` for a network DSN without TLS, unless
    ``REFLECT_PG_ALLOW_INSECURE=1``. Loopback and socket servers pass."""
    env = os.environ if env is None else env
    if requires_tls(dsn) or is_local_dsn(dsn, env) or env.get(ALLOW_INSECURE_VAR, "").strip() == "1":
        return
    raise InsecureDSNError(
        f"{what} reaches another host without sslmode=require, verify-ca or verify-full; "
        f"set {ALLOW_INSECURE_VAR}=1 only if the network itself is trusted"
    )
