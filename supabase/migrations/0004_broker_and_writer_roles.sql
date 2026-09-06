-- ===========================================================================
-- 0004: the two roles the documented processes connect as. Grants only.
--
--   reflect_broker  the Context Broker's DSN (REFLECT_BROKER_PG_DSN):
--                   USAGE on the schema, SELECT on every table, EXECUTE on
--                   the read functions. Not a superuser, no BYPASSRLS, never
--                   a table owner, so FORCE ROW LEVEL SECURITY (0003) applies
--                   to every read it makes. The broker refuses any other kind
--                   of role at startup.
--   reflect_writer  the Mode 2 writer's DSN (REFLECT_PG_DSN) on deployments
--                   that do not use a BYPASSRLS service role: SELECT, INSERT,
--                   UPDATE, DELETE on every table, USAGE on sequences, EXECUTE
--                   on every function. Bound per call via app.current_workspace
--                   (MemoryStore and the adapters do this), so RLS scopes it.
--
-- No secret lives in a migration: both roles are created NOLOGIN. The
-- provisioning step (scripts/provision_roles.py, or the ALTER ROLE ... LOGIN
-- PASSWORD documented in docs/setup.md) sets passwords from the environment
-- after the migrations, so `supabase db push` applies this file unchanged.
-- Re-runnable, and safe for a CREATEROLE-only migrator: an existing role is
-- altered only for an attribute that differs (NOSUPERUSER / NOBYPASSRLS
-- need superuser even for the negative value, so they are never re-issued
-- when already false).
-- ===========================================================================

do $$
declare
  r record;
  name text;
begin
  foreach name in array array['reflect_broker', 'reflect_writer'] loop
    select rolsuper, rolbypassrls, rolcreatedb, rolcreaterole into r from pg_roles where rolname = name;
    if not found then
      execute format('create role %I nologin nosuperuser nobypassrls nocreatedb nocreaterole', name);
    else
      if r.rolsuper then execute format('alter role %I nosuperuser', name); end if;
      if r.rolbypassrls then execute format('alter role %I nobypassrls', name); end if;
      if r.rolcreatedb then execute format('alter role %I nocreatedb', name); end if;
      if r.rolcreaterole then execute format('alter role %I nocreaterole', name); end if;
    end if;
  end loop;
end;
$$;

-- reflect_broker: read only, under RLS.
grant usage on schema reflect_memory to reflect_broker;
grant select on all tables in schema reflect_memory to reflect_broker;
grant execute on function
  reflect_memory.search_memory(uuid, text, int, uuid, real),
  reflect_memory.search_entities(uuid, text, int),
  reflect_memory.entity_neighborhood(uuid, uuid, int),
  reflect_memory.current_workspace_id()
  to reflect_broker;
revoke insert, update, delete, truncate, references, trigger on all tables in schema reflect_memory from reflect_broker;

-- reflect_writer: DML under RLS, never owner.
grant usage on schema reflect_memory to reflect_writer;
grant select, insert, update, delete on all tables in schema reflect_memory to reflect_writer;
grant usage, select on all sequences in schema reflect_memory to reflect_writer;
grant execute on all functions in schema reflect_memory to reflect_writer;

-- Tables created later by re-running 0001/0002 keep the same shape.
alter default privileges in schema reflect_memory grant select on tables to reflect_broker;
alter default privileges in schema reflect_memory grant select, insert, update, delete on tables to reflect_writer;
alter default privileges in schema reflect_memory grant usage, select on sequences to reflect_writer;
