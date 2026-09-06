-- ===========================================================================
-- 0004: the two LOGIN roles the documented processes connect as.
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
--                   on every function. It is bound per connection via
--                   app.current_workspace (the adapters and MemoryStore do
--                   this), so RLS scopes its writes too.
--
-- Passwords come from session settings so this file stays plain SQL:
--   psql "$DSN" -c "set reflect.broker_password = '...'" ...   (same session)
--   or PGOPTIONS="-c reflect.broker_password=... -c reflect.writer_password=..." psql -f 0004...
-- Re-runnable: an existing role gets its password and grants refreshed.
-- ===========================================================================

do $$
declare
  broker_pw text := nullif(current_setting('reflect.broker_password', true), '');
  writer_pw text := nullif(current_setting('reflect.writer_password', true), '');
begin
  if broker_pw is null or writer_pw is null then
    raise exception using
      message = 'migration 0004 needs reflect.broker_password and reflect.writer_password set in this session '
                '(set reflect.broker_password = ''...''; or PGOPTIONS="-c reflect.broker_password=...")',
      errcode = 'invalid_parameter_value';
  end if;

  if not exists (select 1 from pg_roles where rolname = 'reflect_broker') then
    execute format('create role reflect_broker login nosuperuser nobypassrls nocreatedb nocreaterole password %L', broker_pw);
  else
    execute format('alter role reflect_broker login nosuperuser nobypassrls password %L', broker_pw);
  end if;
  if not exists (select 1 from pg_roles where rolname = 'reflect_writer') then
    execute format('create role reflect_writer login nosuperuser nobypassrls nocreatedb nocreaterole password %L', writer_pw);
  else
    execute format('alter role reflect_writer login nosuperuser nobypassrls password %L', writer_pw);
  end if;
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
