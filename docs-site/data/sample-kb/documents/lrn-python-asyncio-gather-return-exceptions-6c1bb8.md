---
type: learning
id: lrn-python-asyncio-gather-return-exceptions-6c1bb8
created: '2026-08-24'
updated: '2026-08-24'
scope: project-quillbot
confidence: medium
confidence_num: 0.57
learning_type: gotcha
discovery_tokens: 61000
title: asyncio.gather swallows sibling failures unless return_exceptions is set
tags: [python, asyncio, concurrency]
symptoms: [one failed download leaves other tasks running, Task exception was never retrieved]
key_insight: By default gather raises the first exception and leaves the rest running; use TaskGroup for structured cancellation.
problem: A failed fetch left orphaned tasks writing partial files.
root_cause: gather does not cancel siblings on error.
fix: Use asyncio.TaskGroup (3.11+) or gather(return_exceptions=True) and inspect results.
rule: Prefer TaskGroup; gather only when you handle every result.
category: debugging-sessions
entities: [asyncio, Python, quillbot, uv]
causal_relations: []
links: []
source_episodes: [ep-98857cbc]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/quillbot/2026-08-24-python-asyncio-gather-return.jsonl
  content_hash: 0d84ba3e8779100b
  detected_at: '2026-08-24T16:00:00'
  source_memory_ids: [ep-98857cbc]
  proof_count: 1
---

```python
async with asyncio.TaskGroup() as tg:
    tasks = [tg.create_task(fetch(u)) for u in urls]
# any failure cancels the others and raises ExceptionGroup
results = [t.result() for t in tasks]
```
