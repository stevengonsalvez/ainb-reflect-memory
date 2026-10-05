---
type: learning
id: lrn-test-pytest-fixture-scope-db-da4eda
created: '2026-07-21'
updated: '2026-07-21'
scope: project-quillbot
confidence: medium
confidence_num: 0.65
learning_type: best-practice
discovery_tokens: 16600
title: "pytest: create the database once per session, roll back per test"
tags: [testing, pytest, python, postgres]
symptoms: [test suite takes 4 minutes because each test migrates the schema]
key_insight: Session-scoped engine plus a per-test transaction that is rolled back gives isolation at a fraction of the cost.
problem: Creating a fresh schema per test made 600 tests crawl.
root_cause: Function-scoped fixture ran migrations every time.
fix: scope='session' for the engine, function-scoped connection with an outer transaction and rollback.
rule: Isolation by rollback, not by recreating the world.
category: testing
entities: [pytest, Python, PostgreSQL, quillbot]
causal_relations: []
links: []
source_episodes: [ep-facbba8f]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/quillbot/2026-07-21-test-pytest-fixture-scope-db.jsonl
  content_hash: 823c466cf4449223
  detected_at: '2026-07-21T15:20:00'
  source_memory_ids: [ep-facbba8f]
  proof_count: 2
---

```python
@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_URL)
    run_migrations(eng)          # once
    yield eng

@pytest.fixture
def db(engine):
    with engine.connect() as conn, conn.begin() as tx:
        yield conn
        tx.rollback()
```

Suite time: 4 min 10 s down to 38 s.
