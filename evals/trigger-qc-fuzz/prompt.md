---
description: Trigger check. "fuzz the invoice parser" should load the qc-fuzz skill.
tags: [trigger]
runs: 2
max_turns: 4
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
---

fuzz the invoice parser
