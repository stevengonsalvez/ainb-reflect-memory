# Setup — ainb-reflect-memory

Operational guide: secrets, database setup, migration, seed, and tests. For the
architecture and API, see the [README](../README.md).

---

## 1. Secrets (Bitwarden)

**No secrets live in this repo.** All credentials come from Bitwarden and land
in a local `.env` that git ignores. Copy the template and fill it:

```bash
cp .env.example .env   # .env is git-ignored; never commit it
```

### Required values (placeholders in `.env.example`)

| Env var                     | What it is                                   | Needed for                          | Exposure rule                                   |
| --------------------------- | -------------------------------------------- | ----------------------------------- | ----------------------------------------------- |
| `DATABASE_URL`              | Postgres connection string                   | migration, seed, integration tests  | server/worker only                              |
| `SUPABASE_URL`              | `https://<ref>.supabase.co`                  | direct client path (Phase 2+)       | safe to expose                                  |
| `SUPABASE_ANON_KEY`         | public anon key                              | direct browser/PostgREST reads      | safe to expose (RLS still applies)              |
| `SUPABASE_SERVICE_ROLE_KEY` | service-role key (**bypasses RLS**)          | migrations / trusted workers only   | **NEVER** to browser/client; NEVER commit       |
| `REFLECT_PG_DSN`            | DSN that switches reflect to the shared backend | enabling the backend (write path: `service_role` or `reflect_writer`) | server/worker only |
| `REFLECT_BROKER_PG_DSN`     | the Context Broker's own DSN (`reflect_broker` role) | running the broker | broker host only |
| `REFLECT_WORKSPACE_ID`      | tenant UUID (hard isolation boundary)        | enabling the backend                | n/a (not a secret)                              |

### Bitwarden item

Supabase credentials live in Bitwarden item metadata:

- **Item:** `WOLOLO-SUPABASE`
- **Known keys:** `DATABASE_PASSWORD`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SECRET_KEY`

Do **not** print secret values into logs, Discord, PRs, or committed files. Load values into the shell session only, then map them to the local env names below. `SUPABASE_SECRET_KEY` is the service-role/secret key and should be treated like `SUPABASE_SERVICE_ROLE_KEY`.

Pull safely with the Bitwarden CLI (values go only into ignored `.env`):

```bash
bw get item "WOLOLO-SUPABASE" \
  | jq -r '.notes | split("\n")[] | select(test("^export ")) | sub("^export "; "")' \
  >> .env
```

If the connection string is not stored as `DATABASE_URL`, build it from the Supabase connection string plus `DATABASE_PASSWORD` in the local shell only.

Unit tests need **none** of these — they run with no database and no
credentials (see §5).

---

## 2. Create the Supabase project

1. Create a project in the Supabase dashboard.
2. Copy **Project Settings → API** → `URL`, `anon` key, `service_role` key into
   the matching Bitwarden fields.
3. Copy **Project Settings → Database → Connection string** into `DATABASE_URL`.
   Use the **direct** connection string for migrations; the pooler/session
   string is fine for app traffic.

The migrations need `pgcrypto`, `pg_trgm` and `vector` (pgvector); all three
are available on Supabase by default and are enabled by the files themselves.
Locally, install the `pgvector` package for your Postgres before applying
0002 (for example `brew install pgvector`; it lands in PostgreSQL 17's
extension dir).

---

## 3. Apply the migrations

This is the one ordered list; the README and the Phase 2 section below point
here instead of repeating it. Each file is plain SQL and re-runnable
(`IF NOT EXISTS` / `CREATE OR REPLACE` / `DROP … IF EXISTS`).

| Order | File | Needs | Adds |
|---|---|---|---|
| 1 | `0001_reflect_memory_phase1.sql` | `pgcrypto`, `pg_trgm` (enabled by the file) | schema, memory tables, RLS, grants |
| 2 | `0002_nanographrag_pgvector.sql` | `pgvector` (enabled by the file; install the package locally) | `ng_*` tables for the shared nano-graphrag store |
| 3 | `0003_classification_force_rls.sql` | 1 and 2 applied | legacy-row pre-check, FORCE RLS on all seven tables, classification floor on every label column, read functions filter before `limit` |
| 4 | `0004_broker_and_writer_roles.sql` | 1 to 3 applied; no session settings, no secrets | the `reflect_broker` and `reflect_writer` roles, NOLOGIN, grants only (see "Roles" below); their passwords come from the provisioning step |
| 5 | `0005_rls_policies_initplan.sql` | 1 to 3 applied | every policy evaluates the tenant resolver once per statement (an InitPlan) instead of once per row under FORCE RLS |
| 6 | `0006_read_functions_shareable_floor.sql` | 1 to 5 applied | the read functions: `is_shareable(jsonb)` composed into every entity-returning query, literal substring entity search (`%` and `_` are characters), an index-friendly neighbourhood walk |

**Option A, psql (works anywhere).** One invocation, the files in order;
`ON_ERROR_STOP` makes psql exit non-zero at the first failing statement and
skip the files after it:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f supabase/migrations/0001_reflect_memory_phase1.sql \
  -f supabase/migrations/0002_nanographrag_pgvector.sql \
  -f supabase/migrations/0003_classification_force_rls.sql \
  -f supabase/migrations/0004_broker_and_writer_roles.sql \
  -f supabase/migrations/0005_rls_policies_initplan.sql \
  -f supabase/migrations/0006_read_functions_shareable_floor.sql
```

The table and the block above are checked against `ls supabase/migrations`
by `tests/test_docs_contracts.py`, so a new file cannot land without a row
here. Shipped files are never edited in place; a change is a new file.

**Then provision the role passwords (both options).** No migration carries a
secret: 0004 creates the roles NOLOGIN, and this step, run after the
migrations with a CREATEROLE or superuser connection, sets a LOGIN password
on each role whose variable is set. Re-run it to rotate a password.

```bash
export DATABASE_URL=postgresql://postgres:…@host/db
export REFLECT_BROKER_PASSWORD=…  REFLECT_WRITER_PASSWORD=…
python scripts/provision_roles.py            # or --only broker | --only writer
```

Migration 0003 runs in this order: first it checks for rows already labelled
above the floor and stops with a message naming the count if any exist (delete
or relabel them deliberately, then re-run), so nothing is locked down while
the data is still wrong; then it switches all seven tables (`memory_items`,
`entities`, `edges`, `ng_kv`, `ng_graph_nodes`, `ng_graph_edges`,
`ng_vectors`) to **FORCE ROW LEVEL SECURITY**; then it adds the
classification floor as a check constraint refusing `restricted` and `pii`
rows, and replaces the read functions so the classification predicate is
applied in SQL, before `limit`. With FORCE, the table owner is subject to the
policies too. Consequence for the worker DSN: connect as a BYPASSRLS role
(Supabase `service_role`) or as a role that sets `app.current_workspace` per
connection; an owner role without either now sees nothing, by design.

### Roles (created by 0004, the one place they are defined)

| Role | Used by | Variable | Grants | RLS |
|---|---|---|---|---|
| `reflect_writer` | the Mode 2 writer (ingest, reindex) on deployments without a BYPASSRLS service role | `REFLECT_PG_DSN` | SELECT, INSERT, UPDATE, DELETE on every table; sequences; every function | applies; bound per statement via `SET LOCAL app.current_workspace` |
| `reflect_broker` | the Context Broker | `REFLECT_BROKER_PG_DSN` | SELECT on every table; EXECUTE on the search functions only | applies; the broker refuses a superuser, BYPASSRLS or owner role at startup |
| `service_role` (Supabase, not created here) | migrations, and the writer where you accept BYPASSRLS | `REFLECT_PG_DSN` | everything | bypassed |

`reflect_writer` and `reflect_broker` are created NOLOGIN by 0004 and cannot
connect until `scripts/provision_roles.py` sets their passwords; re-running
0004 alters an existing role only for an attribute that differs, so it
applies under a CREATEROLE-only migrator (hosted Supabase) as well as locally.

One variable per process: the writer never reads `REFLECT_BROKER_PG_DSN`
and the broker never reads `REFLECT_PG_DSN`. Neither role owns a table.

**Option B, Supabase CLI** (if you use it for this project):

```bash
supabase db push          # applies supabase/migrations/*.sql, no session settings needed
python scripts/provision_roles.py   # then the provisioning step above, same variables
```

> ⚠️ **These migrations define Row-Level Security policies.** Per the issue's
> security requirements, RLS/security-sensitive migrations require **partner
> review before merge — do not self-merge.**

---

## 4. Seed (smoke-test a live database)

```bash
export DATABASE_URL=...                       # from .env / Bitwarden
python scripts/seed.py      # default demo workspace
python scripts/seed.py <WORKSPACE_UUID>   # specific tenant
```

It inserts a couple of memory items, two entities, and an edge, then prints an
evidence pack. It is idempotent — re-running creates no duplicates.

---

## 5. Tests

### Unit tests — no database, no credentials (the default CI path)

```bash
PYTHONPATH=src pytest -m "not integration" tests/postgres
```

These cover normalization/dedupe hashing, model validation, and the SQL-builder
security invariants (tenant scoping, no value interpolation).

### Integration tests — require a Postgres (auto-skip otherwise)

Set `DATABASE_URL` (or `REFLECT_TEST_DATABASE_URL`) to a **throwaway** database,
then:

```bash
pytest -m integration   # or just `pytest` to run both tiers
```

If neither var is set, or the database is unreachable, every integration test
**skips** cleanly — they never fail for lack of credentials.

The fixtures apply every migration in the section 3 table. On macOS the default Unix socket
directory can exceed the 104-byte socket path limit; either run the test
Postgres with `-c unix_socket_directories=''` and connect over
`127.0.0.1`, or export a short `TMPDIR` (for example `/tmp/pg`) before
`pg_ctl start`.

#### Spin a throwaway local Postgres

**Docker:**

```bash
docker run -d --name reflect-pg -e POSTGRES_PASSWORD=reflect_test \
  -e POSTGRES_DB=reflect_test -p 55432:5432 pgvector/pgvector:pg16   # postgres:16-alpine has no pgvector; 0002 needs it
export DATABASE_URL='postgresql://postgres:reflect_test@localhost:55432/reflect_test'
# ... run tests ...
docker rm -f reflect-pg
```

**Homebrew Postgres (no Docker daemon):**

```bash
export PGDATA=/tmp/reflect-pgdata
initdb -D "$PGDATA" -U postgres --auth=trust
pg_ctl -D "$PGDATA" -o "-p 55432 -k /tmp -c listen_addresses=''" -l /tmp/pg.log start
psql -h /tmp -p 55432 -U postgres -d postgres -c 'CREATE DATABASE reflect_test;'
export DATABASE_URL='postgresql://postgres@/reflect_test?host=/tmp&port=55432'
# ... run tests ...  then:  pg_ctl -D "$PGDATA" stop && rm -rf "$PGDATA"
```

The integration fixtures apply the migration automatically before the first
test and truncate tables between tests.

---

## 6. What's covered (Phase 1 acceptance)

- migration applies cleanly on a fresh database, and is re-runnable
- seed inserts memory/entity/edge rows (idempotent)
- FTS search returns ranked rows with highlighted snippets
- graph neighborhood returns only same-tenant edges; cross-tenant edges are
  physically impossible (composite FK)
- duplicate ingestion is idempotent (per-tenant content hash)
- tenant isolation on the trusted path; RLS fail-closed on the direct path
- RLS FORCEd on all seven tables: a non-superuser table owner cannot read across workspaces
- classification floor: `restricted` / `pii` rows are refused by the check constraint
- Context Broker (`tests/broker/`): OIDC 401/403, tenant from the claim only,
  every returned hit pinned and resolved, refusals counted; the live test
  serves it with uvicorn against this database

See [README → Two ways to run: local or shared](../README.md#two-ways-to-run-local-or-shared).

---

## 7. Shared nano-graphrag backend (Phase 2/3)

Makes reflect's nano-graphrag use this Postgres as its shared vector + graph +
community store, so the same memory is queryable from every machine. See
[README → Two ways to run: local or shared](../README.md#two-ways-to-run-local-or-shared).

### Migration

Apply the ordered list in [section 3](#3-apply-the-migrations); nothing is
repeated here. The one Mode 2 fact that is not in that table: the embedding
column is `vector(768)` to match all-mpnet-base-v2.

### Enable the backend in reflect-kb

`LearningsGraphEngine` switches to the shared backend when BOTH are set
(otherwise it keeps local-file behavior). The trigger is `REFLECT_PG_DSN`
**only** — the generic `DATABASE_URL` is deliberately NOT a trigger (it usually
points at an unrelated DB):

```bash
export REFLECT_PG_DSN="postgresql://USER:PASS@HOST:5432/DBNAME"
export REFLECT_WORKSPACE_ID="<workspace-uuid>"
```

**Tenant binding.** Which role each DSN carries is the "Roles" table in
section 3; this paragraph is only the mechanics. `MemoryStore` and the
nano-graphrag adapter bind `app.current_workspace` with `SET LOCAL` inside
the transaction of every statement, so the owner or `reflect_writer` writes
under FORCE RLS and a transaction-mode pooler cannot lose the binding. The
tenant resolver treats a signed JWT claim as authoritative over that GUC, so
PostgREST clients (`authenticated`: read-only SELECT plus EXECUTE on the
search functions) are scoped by their token. The Context Broker binds the
same GUC per request on `REFLECT_BROKER_PG_DSN`. Both DSNs are judged on the
open connection, not on the string: TLS, or a loopback or Unix-socket
server, else refused unless `REFLECT_PG_ALLOW_INSECURE=1`.

The `reflect` client needs nano-graphrag + its embedding stack (the `[graph]`
extra); the Postgres adapters add the `[postgres]` extra (psycopg). Install both:
`pip install '.[graph,postgres]'`.

### Cross-machine demo + tests

```bash
# end-to-end: machine A writes, machine B reads — from Postgres, no shared files
PYTHONPATH=src DATABASE_URL=... python scripts/demo_cross_machine.py

# adapter conformance + cross-machine + RLS + full-pipeline tests
#   (need nano-graphrag + networkx + numpy + psycopg + a pgvector Postgres)
PYTHONPATH=src DATABASE_URL=... pytest -m integration tests/postgres

# the always-on "server stays dumb" scan needs no DB and no nano-graphrag:
PYTHONPATH=src pytest -m "not integration" tests/postgres/test_server_is_dumb.py
```
