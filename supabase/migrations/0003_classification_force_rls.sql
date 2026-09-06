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

-- ===========================================================================
-- Classification floor inside the read functions, before LIMIT.
-- memory_items above the floor cannot exist (constraint above), so the
-- predicate there is defence in depth; entities and edges carry their own
-- metadata label and are filtered here, and an edge whose evidence memory is
-- above the floor (or missing) is dropped too, so no egress path has to
-- post-filter graph rows after a limit already cut the result.
-- ===========================================================================
create or replace function reflect_memory.search_memory(
  p_workspace_id uuid,
  p_query        text,
  p_limit        int  default 10,
  p_agent_id     uuid default null,
  p_min_rank     real default null
)
returns table (
  id                 uuid,
  workspace_id       uuid,
  agent_id           uuid,
  source_session_id  text,
  user_id            uuid,
  source_type        text,
  source_uri         text,
  content            text,
  content_hash       text,
  metadata           jsonb,
  confidence         real,
  created_at         timestamptz,
  updated_at         timestamptz,
  rank               real,
  snippet            text
)
language sql
stable
set search_path = pg_catalog, public, extensions, reflect_memory
as $$
  with q as (select websearch_to_tsquery('english', p_query) as tsq)
  select
    m.id, m.workspace_id, m.agent_id, m.source_session_id, m.user_id,
    m.source_type, m.source_uri, m.content, m.content_hash, m.metadata,
    m.confidence, m.created_at, m.updated_at,
    ts_rank(m.search_vector, q.tsq) as rank,
    ts_headline('english', m.content, q.tsq,
      'StartSel=<b>, StopSel=</b>, MaxFragments=2, MaxWords=18, MinWords=5'
    ) as snippet
  from reflect_memory.memory_items m, q
  where m.workspace_id = p_workspace_id
    and m.search_vector @@ q.tsq
    and coalesce(m.metadata->>'classification', 'internal') in ('public', 'internal')
    and (p_agent_id is null or m.agent_id = p_agent_id)
    and (p_min_rank is null or ts_rank(m.search_vector, q.tsq) >= p_min_rank)
  order by rank desc, m.created_at desc
  limit greatest(p_limit, 1);
$$;

create or replace function reflect_memory.search_entities(
  p_workspace_id uuid,
  p_query        text,
  p_limit        int default 10
)
returns table (
  id             uuid,
  workspace_id   uuid,
  canonical_name text,
  entity_type    text,
  aliases        text[],
  metadata       jsonb,
  created_at     timestamptz,
  updated_at     timestamptz,
  matched_alias  text,
  score          real
)
language sql
stable
set search_path = pg_catalog, public, extensions, reflect_memory
as $$
  select
    e.id, e.workspace_id, e.canonical_name, e.entity_type, e.aliases,
    e.metadata, e.created_at, e.updated_at,
    (select a from unnest(e.aliases) a
      where lower(a) = lower(p_query) limit 1) as matched_alias,
    greatest(
      similarity(e.canonical_name, p_query),
      case when exists (
        select 1 from unnest(e.aliases) a where lower(a) = lower(p_query)
      ) then 1.0 else 0.0 end
    ) as score
  from reflect_memory.entities e
  where e.workspace_id = p_workspace_id
    and coalesce(e.metadata->>'classification', 'internal') in ('public', 'internal')
    and (
      e.canonical_name ilike '%' || p_query || '%'
      or similarity(e.canonical_name, p_query) > 0.2
      or exists (
        select 1 from unnest(e.aliases) a where lower(a) = lower(p_query)
      )
    )
  order by score desc, e.canonical_name asc
  limit greatest(p_limit, 1);
$$;

create or replace function reflect_memory.entity_neighborhood(
  p_workspace_id uuid,
  p_entity_id    uuid,
  p_max_depth    int default 1
)
returns table (
  id                 uuid,
  workspace_id       uuid,
  source_entity_id   uuid,
  target_entity_id   uuid,
  relation_type      text,
  evidence_memory_id uuid,
  weight             real,
  metadata           jsonb,
  created_at         timestamptz,
  updated_at         timestamptz
)
language sql
stable
set search_path = pg_catalog, public, extensions, reflect_memory
as $$
  with recursive shareable_edges as (
    select e.*
    from reflect_memory.edges e
    where e.workspace_id = p_workspace_id
      and coalesce(e.metadata->>'classification', 'internal') in ('public', 'internal')
      and (
        e.evidence_memory_id is null
        or exists (
          select 1 from reflect_memory.memory_items m
          where m.id = e.evidence_memory_id
            and m.workspace_id = e.workspace_id
            and coalesce(m.metadata->>'classification', 'internal') in ('public', 'internal')
        )
      )
  ),
  reachable(entity_id, depth) as (
    select p_entity_id, 0
    union
    select
      case when e.source_entity_id = r.entity_id
           then e.target_entity_id else e.source_entity_id end,
      r.depth + 1
    from reachable r
    join shareable_edges e
      on (e.source_entity_id = r.entity_id or e.target_entity_id = r.entity_id)
    where r.depth < least(greatest(p_max_depth, 0), 5)
  )
  select distinct
    e.id, e.workspace_id, e.source_entity_id, e.target_entity_id,
    e.relation_type, e.evidence_memory_id, e.weight, e.metadata,
    e.created_at, e.updated_at
  from shareable_edges e
  join reachable r
    on (e.source_entity_id = r.entity_id or e.target_entity_id = r.entity_id);
$$;
