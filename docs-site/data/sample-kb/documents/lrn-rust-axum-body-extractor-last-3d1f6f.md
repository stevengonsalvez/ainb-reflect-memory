---
type: learning
id: lrn-rust-axum-body-extractor-last-3d1f6f
created: '2026-05-03'
updated: '2026-05-03'
scope: project-acme-api
confidence: medium
confidence_num: 0.62
learning_type: gotcha
discovery_tokens: 44400
title: "axum: body-consuming extractor must be the last argument"
tags: [rust, axum, compile-error]
symptoms:
- "the trait bound `fn(Json<T>, State<S>): Handler` is not satisfied"
key_insight: Json, Form and Bytes consume the request body, so axum only allows one and only in last position.
problem: Handler refuses to compile with an unhelpful Handler trait error.
root_cause: FromRequest (body) extractors are ordered after FromRequestParts extractors.
fix: 'Move Json<T> to the final parameter; add #[axum::debug_handler] to get a readable error.'
rule: Put State, Path, Query first; Json or Form last.
category: build-errors
entities: [axum, Rust, tokio, acme-api]
causal_relations: []
links: []
source_episodes: [ep-ef52f551]
superseded_by: null
forget_after: null
agent: codex
provenance:
  source_tool: codex
  source_path: sessions/acme-api/2026-05-03-rust-axum-body-extractor-las.jsonl
  content_hash: 9b4f9e31d7d85abc
  detected_at: '2026-05-03T10:30:00'
  source_memory_ids: [ep-ef52f551]
  proof_count: 1
---

## Problem

```text
error[E0277]: the trait bound `fn(Json<NewUser>, State<AppState>) -> ... : Handler<_, _>` is not satisfied
```

## Solution

```rust
#[axum::debug_handler]
async fn create_user(
    State(app): State<AppState>,
    Json(body): Json<NewUser>,   // body extractor LAST
) -> Result<StatusCode, ApiError> { /* ... */ }
```

`debug_handler` turns the trait soup into "Json<_> must be the last argument".
