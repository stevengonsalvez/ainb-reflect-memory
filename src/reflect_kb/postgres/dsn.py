"""Transport rules for a Postgres DSN, shared by the writer path, the graph
adapter and the broker.

Notes, vectors and graph cross the network on this DSN, so a connection that
reaches another host must be encrypted. The judgement is made on the open
connection, the way libpq resolved it (``conn.info.host``, ``hostaddr`` and
``ssl_in_use`` after service files, environment and multi-host resolution),
never on the DSN string: a keyword-form or unparseable string used to be
judged local and skipped the check. A loopback or Unix-socket server is
exempt: nothing leaves the machine. ``REFLECT_PG_ALLOW_INSECURE=1`` is the
single, explicit opt-out.
"""

from __future__ import annotations

import os
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Any

__all__ = [
    "InsecureDSNError",
    "assert_tls",
    "connect_secure",
    "is_local_connection",
    "is_local_dsn",
    "requires_tls",
]

TLS_MODES = ("require", "verify-ca", "verify-full")
ALLOW_INSECURE_VAR = "REFLECT_PG_ALLOW_INSECURE"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


class InsecureDSNError(RuntimeError):
    """A network connection without TLS, and no explicit opt-out."""


def _local_host(host: str) -> bool:
    return host.strip("[]").lower() in _LOCAL_HOSTS or host.startswith("/")


def is_local_connection(info: Any) -> bool:
    """True when the server libpq connected to is this machine: every host
    and hostaddr it resolved is loopback or a Unix socket directory."""
    hosts = [h for h in str(getattr(info, "host", "") or "").split(",")]
    addrs = [a for a in str(getattr(info, "hostaddr", "") or "").split(",") if a]
    return all(_local_host(h) for h in hosts + addrs)


def _connection_is_secure(info: Any, env: Mapping[str, str]) -> bool:
    if env.get(ALLOW_INSECURE_VAR, "").strip() == "1":
        return True
    if bool(getattr(info, "ssl_in_use", False)):
        return True
    return is_local_connection(info)


def connect_secure(
    dsn: str,
    *,
    what: str = "Postgres DSN",
    env: Mapping[str, str] | None = None,
    connect: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Open the connection and return it, or close it and raise
    :class:`InsecureDSNError` when it reached another host without TLS and
    ``REFLECT_PG_ALLOW_INSECURE`` is not ``1``. ``connect`` is psycopg.connect
    unless a test injects one; ``kwargs`` go to it."""
    env = os.environ if env is None else env
    if connect is None:
        import psycopg

        connect = psycopg.connect
    conn = connect(dsn, **kwargs)
    info = getattr(conn, "info", None)
    if info is None:
        return conn  # a fake connection in a unit test carries no transport
    if _connection_is_secure(info, env):
        return conn
    try:
        conn.close()
    finally:
        pass
    raise InsecureDSNError(
        f"{what} reached {getattr(info, 'host', '') or getattr(info, 'hostaddr', '')!r} without TLS; "
        f"pin sslmode=require, verify-ca or verify-full, or set {ALLOW_INSECURE_VAR}=1 only if the "
        "network itself is trusted"
    )


def assert_tls(
    dsn: str,
    *,
    what: str = "Postgres DSN",
    env: Mapping[str, str] | None = None,
    connect: Callable[..., Any] | None = None,
) -> None:
    """Probe the DSN once (connect, judge, close). Raises
    :class:`InsecureDSNError` the way :func:`connect_secure` does."""
    conn = connect_secure(dsn, what=what, env=env, connect=connect)
    close = getattr(conn, "close", None)
    if callable(close):
        close()


# --------------------------------------------------------------------------- #
# String pre-filters. They cannot see service files or every libpq key and
# are never the gate; they exist for messages and tests that reason about a
# DSN before any connection is made.
# --------------------------------------------------------------------------- #


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
    """String pre-filter: True when every host the DSN names is this machine
    (DSN host and hostaddr, then PGHOST and PGHOSTADDR). A ``service=`` name
    (or PGSERVICE) is never local: pg_service.conf is not read here."""
    env = os.environ if env is None else env
    info = _conninfo(dsn)
    if info.get("service") or env.get("PGSERVICE"):
        return False
    hosts = [h for h in info.get("host", "").split(",") if h] or [env.get("PGHOST", "")]
    addrs = [a for a in info.get("hostaddr", "").split(",") if a] or [env.get("PGHOSTADDR", "")]
    return all(_local_host(h) for h in hosts + addrs)


def requires_tls(dsn: str, env: Mapping[str, str] | None = None) -> bool:
    """String pre-filter: True when the DSN, or PGSSLMODE when the DSN says
    nothing, pins an encrypting sslmode."""
    env = os.environ if env is None else env
    mode = _conninfo(dsn).get("sslmode", "") or env.get("PGSSLMODE", "")
    return mode in TLS_MODES
