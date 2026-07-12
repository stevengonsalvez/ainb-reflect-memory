---
id: alpha-auth-jwt-fix
title: "Supabase JWT verification fails on clock skew"
confidence: high
learning_type: bug-fix
scope: cross-project
tags: [project-alpha, auth, supabase]
created: 2026-06-28
---

# Supabase JWT verification fails on clock skew

Tokens rejected when the edge node clock drifts. Allow a 30s leeway window.
