-- ============================================================================
-- ainb-reflect-memory: Phase 3 hardening. Classification floor + FORCE RLS.
--
-- Two independent guards, both fail closed:
--
--   1. Classification floor. metadata->>'classification' on memory_items,
--      entities and edges is the data label (vocabulary: public, internal,
--      restricted, pii; missing means internal). The shared store is an
--      egress path, so restricted and pii rows may NOT exist here at all.
--      The Python inputs refuse them before SQL is built; these constraints
--      make the database refuse them regardless of client. The ng_* tables
--      carry no label column: their floor is engine-level (the graph engine
--      never hands a restricted document to nano-graphrag).
--
--   2. FORCE ROW LEVEL SECURITY on all seven tenant tables (0001 and 0002).
--      ENABLE exempted the table owner. On a deployment where the application connects as the
--      owner role, that exemption is a cross-workspace read. FORCE closes it:
--      the owner is subject to the same policies as everyone else. Superusers
--      and BYPASSRLS roles (Supabase service_role) are still exempt by design;
--      that is the trusted migration/worker path. Any other worker connection
--      must now set app.current_workspace (see docs/setup.md).
--
-- !! SECURITY REVIEW REQUIRED !!  RLS-touching migration: partner review
-- before merge, do not self-merge.
--
-- Re-runnable: DROP CONSTRAINT IF EXISTS + ADD; FORCE is idempotent.
-- ============================================================================

-- Pre-check first, with a readable error. A row already labelled restricted,
-- pii or an unknown value would make the constraint below fail with a bare
-- check violation; instead name the count and what to do, before anything in
-- this migration changes. The rows are not downgraded or deleted here: that
-- is an operator decision, made explicitly.
do $$
declare
  t record;
  v_any boolean;
  n_bad bigint;
begin
  -- exists(...) first, so the common case (no offending row) is one index
  -- probe, and count(*) only to name the number in the error.
  for t in
    select * from (values
      ('memory_items', 'metadata'), ('entities', 'metadata'), ('edges', 'metadata')
    ) as v(tbl, col)
  loop
    execute format(
      'select exists (select 1 from reflect_memory.%I where %I->>''classification'' is not null '
      'and %I->>''classification'' not in (''public'', ''internal''))', t.tbl, t.col, t.col)
      into strict v_any;
    if v_any then
      execute format(
        'select count(*) from reflect_memory.%I where %I->>''classification'' is not null '
        'and %I->>''classification'' not in (''public'', ''internal'')', t.tbl, t.col, t.col)
        into n_bad;
      raise exception using
        message = format('%s %s row(s) carry a classification above the floor '
                         '(restricted, pii or unknown). Delete them from the shared store, '
                         'or relabel them deliberately, then re-run 0003.', n_bad, t.tbl),
        hint = format('select * from reflect_memory.%I where %I->>''classification'' '
                      'not in (''public'', ''internal'');', t.tbl, t.col);
    end if;
  end loop;
end;
$$;

-- FORCE ROW LEVEL SECURITY on every table that carries tenant data: the
-- three Phase 1 tables and the four nano-graphrag tables from 0002. ENABLE
-- exempted the table owner on all seven.
alter table reflect_memory.memory_items   force row level security;
alter table reflect_memory.entities       force row level security;
alter table reflect_memory.edges          force row level security;
alter table reflect_memory.ng_kv          force row level security;
alter table reflect_memory.ng_graph_nodes force row level security;
alter table reflect_memory.ng_graph_edges force row level security;
alter table reflect_memory.ng_vectors     force row level security;

-- One check per table that carries a label column, same predicate each time.
-- NOT VALID first, then VALIDATE CONSTRAINT: the validation scan runs under
-- SHARE UPDATE EXCLUSIVE instead of ACCESS EXCLUSIVE, so readers and writers
-- are not blocked for the length of the scan. The four ng_* tables carry no
-- label of their own (nano-graphrag writes its own JSON shapes); their floor
-- is applied once, in the graph engine, before chunking, so a restricted
-- document is never handed to nano-graphrag at all.
do $$
declare
  t record;
begin
  for t in
    select * from (values
      ('memory_items', 'metadata'), ('entities', 'metadata'), ('edges', 'metadata')
    ) as v(tbl, col)
  loop
    execute format('alter table reflect_memory.%I drop constraint if exists %I',
                   t.tbl, t.tbl || '_classification_floor');
    execute format(
      'alter table reflect_memory.%I add constraint %I check ('
      '%I->>''classification'' is null or %I->>''classification'' in (''public'', ''internal'')) not valid',
      t.tbl, t.tbl || '_classification_floor', t.col, t.col);
    execute format('alter table reflect_memory.%I validate constraint %I',
                   t.tbl, t.tbl || '_classification_floor');
  end loop;
end;
$$;
