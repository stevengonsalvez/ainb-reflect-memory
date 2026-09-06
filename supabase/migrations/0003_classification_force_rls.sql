-- ============================================================================
-- ainb-reflect-memory: Phase 3 hardening. Classification floor + FORCE RLS.
--
-- Two independent guards, both fail closed:
--
--   1. Classification floor. memory_items.metadata->>'classification' is the
--      data label of a memory item (vocabulary: public, internal, restricted,
--      pii; missing means internal). The shared store is an egress path, so
--      restricted and pii rows may NOT exist here at all. The Python
--      InsertMemoryInput refuses them before SQL is built; this constraint
--      makes the database refuse them regardless of client.
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
  n_bad bigint;
begin
  select count(*) into n_bad
  from reflect_memory.memory_items
  where metadata->>'classification' is not null
    and metadata->>'classification' not in ('public', 'internal');
  if n_bad > 0 then
    raise exception using
      message = format('%s memory_items row(s) carry a classification above the floor '
                       '(restricted, pii or unknown). Delete them from the shared store, '
                       'or relabel them deliberately, then re-run 0003.', n_bad),
      hint = 'select id, metadata->>''classification'' from reflect_memory.memory_items '
             'where metadata->>''classification'' not in (''public'', ''internal'');';
  end if;
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

alter table reflect_memory.memory_items
  drop constraint if exists memory_items_classification_floor;
alter table reflect_memory.memory_items
  add constraint memory_items_classification_floor
  check (
    metadata->>'classification' is null
    or metadata->>'classification' in ('public', 'internal')
  );
