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
--   2. FORCE ROW LEVEL SECURITY. Migration 0001 ENABLEd RLS, which exempts the
--      table owner. On a deployment where the application connects as the
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

alter table reflect_memory.memory_items
  drop constraint if exists memory_items_classification_floor;
alter table reflect_memory.memory_items
  add constraint memory_items_classification_floor
  check (
    metadata->>'classification' is null
    or metadata->>'classification' in ('public', 'internal')
  );

alter table reflect_memory.memory_items force row level security;
alter table reflect_memory.entities     force row level security;
alter table reflect_memory.edges        force row level security;
