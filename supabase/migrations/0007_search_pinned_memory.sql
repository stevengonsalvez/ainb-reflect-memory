-- 0007: a lexical search that returns only pinned rows, filtered before LIMIT.
--
-- The Context Broker serves only hits whose source_uri is a
-- repo@sha:path pin. It used to call search_memory and drop unpinned rows in
-- Python after the LIMIT, so a workspace whose top p_limit matches were all
-- unpinned got nothing back even when pinned matches ranked just below.
--
-- Why a new function instead of a filter inside search_memory: search_memory
-- is the general store search, and the writer, the mirror and the store's
-- own callers read unpinned notes through it. Only the broker's contract is
-- "pinned or nothing", so only the broker's read filters.
--
-- reflect_memory.is_pinned_source_uri(text) mirrors
-- reflect_kb.pinning.parse_source_uri. It may accept a little more than the
-- Python parser (a malformed line range, an inverted one), never less: a row
-- the parser accepts must never be filtered here, and the broker still parses
-- every row it gets. So the line-range suffix is matched as "# and anything"
-- (Python's \d also matches non-ASCII digits), and the path as "anything but
-- #": whitespace is not excluded here, because what [:space:] matches depends
-- on the server's locale and Python's \s does not. No locale-dependent class
-- is used, which also keeps immutable honest. No search_path is set so the
-- planner can inline the function; the operators resolve through pg_catalog.

create or replace function reflect_memory.is_pinned_source_uri(p_source_uri text)
returns boolean
language sql
immutable
parallel safe
as $$
  select coalesce(
    p_source_uri ~ '^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*@[0-9a-f]{7,64}:[^#]+(#.*)?$'
    -- the path is clean and relative: no leading slash, no empty, . or .. segment
    and ('/' || substring(p_source_uri from '^[^@]*@[0-9a-f]{7,64}:([^#]+)') || '/') !~ '/\.{0,2}/',
    false
  );
$$;

create or replace function reflect_memory.search_pinned_memory(
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
    and reflect_memory.is_pinned_source_uri(m.source_uri)
    and (p_agent_id is null or m.agent_id = p_agent_id)
    and (p_min_rank is null or ts_rank(m.search_vector, q.tsq) >= p_min_rank)
  order by rank desc, m.created_at desc
  limit greatest(p_limit, 1);
$$;

-- New functions get PUBLIC EXECUTE by default; 0001 made that explicit-deny.
revoke all on function reflect_memory.search_pinned_memory(uuid, text, int, uuid, real) from public;
grant execute on function reflect_memory.is_pinned_source_uri(text) to public;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'reflect_broker') then
    grant execute on function reflect_memory.search_pinned_memory(uuid, text, int, uuid, real) to reflect_broker;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    grant execute on function reflect_memory.search_pinned_memory(uuid, text, int, uuid, real) to authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function reflect_memory.search_pinned_memory(uuid, text, int, uuid, real) to service_role;
  end if;
end;
$$;
