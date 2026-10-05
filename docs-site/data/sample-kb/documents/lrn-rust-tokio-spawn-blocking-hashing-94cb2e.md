---
type: learning
id: lrn-rust-tokio-spawn-blocking-hashing-94cb2e
created: '2026-04-22'
updated: '2026-04-22'
scope: project-acme-api
confidence: high
confidence_num: 0.88
learning_type: performance
discovery_tokens: 19400
title: Run argon2 hashing inside spawn_blocking
tags: [rust, tokio, auth, perf]
symptoms: [p99 latency spikes on /login, health check times out under login load]
key_insight: CPU-bound work on a tokio worker thread starves every other task on that thread.
problem: Login bursts froze unrelated endpoints, including /healthz.
root_cause: argon2 verify takes ~80ms of pure CPU and was awaited directly on the async worker.
fix: Wrap hash and verify in tokio::task::spawn_blocking.
rule: Anything over ~1ms of CPU goes to spawn_blocking or a rayon pool.
category: debugging-sessions
entities: [tokio, Rust, argon2, axum, acme-api]
causal_relations:
- {source: argon2 on async worker, target: latency spikes, type: causes}
links: []
source_episodes: [ep-5c9c6412]
superseded_by: null
forget_after: null
agent: claude-code
provenance:
  source_tool: claude
  source_path: sessions/acme-api/2026-04-22-rust-tokio-spawn-blocking-ha.jsonl
  content_hash: 9813ae972a684f85
  detected_at: '2026-04-22T13:05:00'
  source_memory_ids: [ep-5c9c6412]
  proof_count: 2
---

## Problem

`/login` bursts caused `/healthz` to time out. Flamegraph showed the worker threads parked inside `argon2::verify`.

## Solution

```rust
let ok = tokio::task::spawn_blocking(move || {
    argon2.verify_password(pw.as_bytes(), &parsed_hash).is_ok()
})
.await?;
```

Size the blocking pool with `Builder::max_blocking_threads` if login volume is high.

## Anti-Pattern

Calling the sync verify inside an `async fn` and trusting `.await` elsewhere to yield. It never yields mid-hash.
