-- ===========================================================================
-- 0005: RLS policies evaluate the tenant resolver once per statement.
--
-- 0001 and 0002 wrote `workspace_id = reflect_memory.current_workspace_id()`
-- in every policy. Now that FORCE (0003) puts the table owner under RLS too,
-- that call runs once per row on every scan the worker makes. Wrapping it
-- as `(select reflect_memory.current_workspace_id())` lets the planner
-- evaluate it once as an InitPlan (the function is STABLE) and compare rows
-- against a constant.
--
-- The resolver also loses its two dead exception blocks: current_setting()
-- with missing_ok = true never raises, so the handlers only cost a
-- subtransaction per call.
--
-- !! SECURITY REVIEW REQUIRED !!  RLS-touching migration: partner review
-- before merge, do not self-merge. Re-runnable.
-- ===========================================================================

create or replace function reflect_memory.current_workspace_id()
returns uuid
language plpgsql
stable
set search_path = pg_catalog
as $$
declare
  v_guc text;
  v_claims text;
  v_ws text;
begin
  -- 1. signed JWT claim is authoritative when a JWT is present
  v_claims := current_setting('request.jwt.claims', true);
  if v_claims is not null and v_claims <> '' then
    v_ws := (v_claims::jsonb) ->> 'workspace_id';
    if v_ws is not null and v_ws <> '' then
      return v_ws::uuid;
    end if;
    return null;  -- JWT present but no workspace_id => deny; do NOT use the GUC
  end if;

  -- 2. no JWT at all (trusted worker / tests) => the GUC
  v_guc := current_setting('app.current_workspace', true);
  if v_guc is not null and v_guc <> '' then
    return v_guc::uuid;
  end if;

  return null;
end;
$$;

do $$
declare
  t text;
begin
  foreach t in array array['memory_items', 'entities', 'edges',
                           'ng_kv', 'ng_graph_nodes', 'ng_graph_edges', 'ng_vectors']
  loop
    execute format('drop policy if exists %I on reflect_memory.%I', t || '_tenant_isolation', t);
    execute format(
      'create policy %I on reflect_memory.%I for all '
      'using (workspace_id = (select reflect_memory.current_workspace_id())) '
      'with check (workspace_id = (select reflect_memory.current_workspace_id()))',
      t || '_tenant_isolation', t);
  end loop;
end;
$$;
