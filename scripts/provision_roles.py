#!/usr/bin/env python3
"""Give the roles migration 0004 created their LOGIN passwords.

Migrations carry no secrets: 0004 creates reflect_broker and reflect_writer
NOLOGIN with grants only. This step runs after the migrations, with the
passwords taken from the environment, and can be re-run at any time to
rotate them:

    export DATABASE_URL=postgresql://postgres:...@host/db    # a CREATEROLE or superuser connection
    export REFLECT_BROKER_PASSWORD=...  REFLECT_WRITER_PASSWORD=...
    python scripts/provision_roles.py [--only broker|writer]

Each role is altered only when its password is provided; the connection is
the only place a password travels (parameter-bound via psycopg's sql.Literal).
"""

from __future__ import annotations

import argparse
import os
import sys

ROLES = {"broker": ("reflect_broker", "REFLECT_BROKER_PASSWORD"),
         "writer": ("reflect_writer", "REFLECT_WRITER_PASSWORD")}


def provision(dsn: str, passwords: dict[str, str], *, connect=None) -> list[str]:
    """ALTER ROLE ... LOGIN PASSWORD for every role with a password given.
    Returns the role names provisioned. ``connect`` is psycopg.connect unless
    a test injects one."""
    if connect is None:
        import psycopg

        connect = psycopg.connect
    from psycopg import sql

    done: list[str] = []
    conn = connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            for role, password in passwords.items():
                cur.execute("select 1 from pg_roles where rolname = %s", (role,))
                if cur.fetchone() is None:
                    raise SystemExit(f"role {role} does not exist; apply supabase/migrations/0004 first")
                cur.execute(sql.SQL("alter role {} login password {}").format(sql.Identifier(role), sql.Literal(password)))
                done.append(role)
    finally:
        conn.close()
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", choices=sorted(ROLES), help="provision one role")
    args = ap.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("DATABASE_URL is not set (a CREATEROLE or superuser connection)", file=sys.stderr)
        return 2
    passwords = {}
    for key, (role, var) in ROLES.items():
        if args.only and key != args.only:
            continue
        value = os.environ.get(var, "")
        if value:
            passwords[role] = value
    if not passwords:
        print("nothing to do: set REFLECT_BROKER_PASSWORD and/or REFLECT_WRITER_PASSWORD", file=sys.stderr)
        return 2
    for role in provision(dsn, passwords):
        print(f"provisioned {role}: LOGIN with the password from the environment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
