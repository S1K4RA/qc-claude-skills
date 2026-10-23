---
description: Trigger check. "is /api/admin protected without a token?" should load the qc-security skill.
tags: [trigger]
runs: 2
max_turns: 4
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
---

is /api/admin protected without a token?
