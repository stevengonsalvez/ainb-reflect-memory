-- 0006: the read functions the Context Broker and the writer call, with the
-- classification floor applied before LIMIT through one predicate.
--
-- Why a new file: 0003 shipped in an earlier release and an incremental
-- upgrade (supabase db push, or any migration table) skips an applied file,
-- so a function redefined inside 0003 never reached an upgraded database.
-- Shipped migrations are never edited; every change lands in a new file.
--
-- What changes against the 0001 definitions:
--   * reflect_memory.is_shareable(jsonb) is the one floor predicate; every
--     query that returns memory items, entities or edges composes it
--     (search_memory, search_entities, entity_neighborhood, and the Python
--     side's entities_by_ids), so a restricted entity cannot ride out on an
--     edge that touches it.
--   * search_entities matches the query as a literal substring
--     (position(...)), never as a LIKE pattern: q=% used to return the first
--     p_limit entities of the workspace and _ was a single-character wildcard.
--   * entity_neighborhood walks reflect_memory.edges with one join per
--     direction (source side, target side) and the floor inline, so
--     edges_workspace_source_idx and edges_workspace_target_idx serve every
--     hop; the materialised CTE referenced twice, joined on an OR across
--     source and target with a correlated EXISTS per edge, scanned every
--     edge of the workspace on every call.

create or replace function reflect_memory.is_shareable(p_metadata jsonb)
returns boolean
language sql
immutable
parallel safe
as $$
  select coalesce(p_metadata->>'classification', 'internal') in ('public', 'internal');
$$;

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
    and reflect_memory.is_shareable(m.metadata)
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
    and reflect_memory.is_shareable(e.metadata)
    and length(btrim(p_query)) > 0
    and (
      -- a literal substring match: % and _ in the query are characters, never wildcards
      position(lower(btrim(p_query)) in lower(e.canonical_name)) > 0
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
  -- Every hop is two index-friendly joins (one per direction) instead of an
  -- OR across source and target that no index serves.
  with recursive reachable(entity_id, depth) as (
    select p_entity_id, 0
    union
    select h.next_id, r.depth + 1
    from reachable r
    cross join lateral (
      select e.target_entity_id as next_id
      from reflect_memory.edges e
      left join reflect_memory.memory_items m
        on m.workspace_id = e.workspace_id and m.id = e.evidence_memory_id
      where e.workspace_id = p_workspace_id and e.source_entity_id = r.entity_id
        and reflect_memory.is_shareable(e.metadata)
        and (e.evidence_memory_id is null or (m.id is not null and reflect_memory.is_shareable(m.metadata)))
      union all
      select e.source_entity_id
      from reflect_memory.edges e
      left join reflect_memory.memory_items m
        on m.workspace_id = e.workspace_id and m.id = e.evidence_memory_id
      where e.workspace_id = p_workspace_id and e.target_entity_id = r.entity_id
        and reflect_memory.is_shareable(e.metadata)
        and (e.evidence_memory_id is null or (m.id is not null and reflect_memory.is_shareable(m.metadata)))
    ) h
    where r.depth < least(greatest(p_max_depth, 0), 5)
  )
  select distinct
    e.id, e.workspace_id, e.source_entity_id, e.target_entity_id,
    e.relation_type, e.evidence_memory_id, e.weight, e.metadata,
    e.created_at, e.updated_at
  from reflect_memory.edges e
  left join reflect_memory.memory_items m
    on m.workspace_id = e.workspace_id and m.id = e.evidence_memory_id
  where e.workspace_id = p_workspace_id
    and (e.source_entity_id in (select entity_id from reachable)
         or e.target_entity_id in (select entity_id from reachable))
    and reflect_memory.is_shareable(e.metadata)
    and (e.evidence_memory_id is null or (m.id is not null and reflect_memory.is_shareable(m.metadata)));
$$;

grant execute on function reflect_memory.is_shareable(jsonb) to public;
