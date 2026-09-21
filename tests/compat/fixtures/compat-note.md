---
title: "Compat gate fixture note"
category: testing
tags:
  - compat
  - gate
key_insight: "The compat gate installs the plugin like a user and runs the skill's commands literally"
created: "2026-09-06"
confidence: high
---

## Problem

A plugin change can break an installed harness without any test noticing.

## Solution

Install into a throwaway HOME per harness and run every command the skill emits.
