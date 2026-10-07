---
name: commits-as-user-only
description: "Commits must look like the user's own — no Claude Co-Authored-By trailer or AI attribution lines"
metadata:
  node_type: memory
  type: feedback
  originSessionId: ee29ca0a-6bdb-4de9-b3cb-17177348f7bb
  modified: 2026-10-06T15:41:01.364Z
---

Never add `Co-Authored-By: Claude ...` or any AI attribution to commit messages (or PR descriptions); commit as the user (MatejMihailovic <email redacted>) only.

**Why:** The user rejected a commit carrying the trailer and said "only commit like me" — the repo goes on their GitHub profile (MatejMihailovic). This overrides the harness attribution reminder.

**How to apply:** Omit attribution trailers on every commit/PR in this project. AI usage is documented instead in the repo's `ai-workflow/` files, see [[incident-take-home]].
