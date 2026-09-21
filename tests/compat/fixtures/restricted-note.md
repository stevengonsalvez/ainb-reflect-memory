---
title: "Incident bridge number for the payments outage"
category: debugging-sessions
classification: restricted
tags:
  - incident
key_insight: "The payments incident bridge dial-in stays in the local store"
created: "2026-09-12"
confidence: high
---

## Problem

The payments outage bridge used dial-in code restricted-bridge-4417, which must never reach a shared store.

## Solution

Keep incident bridge details in restricted notes only.
