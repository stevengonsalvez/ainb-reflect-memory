---
title: "Deploy script leaked the registry token into the shell"
category: debugging-sessions
tags:
  - deploy
  - secrets
key_insight: "Read the registry token from the keychain, never export it in the deploy script"
created: "2026-08-30"
confidence: medium
---

## Problem

The deploy script exported `REGISTRY_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789` into
the shell before pushing, and the value ended up in the session transcript.

## Solution

Move the token into the keychain and read it at push time. Rotate the leaked one.
