# Plan

| Phase | What | Status |
|---|---|---|
| 1. Reader | Turn transcripts into one clean run model. Cross-check token totals against Claude Code's own. | Done Nov 23 |
| 2. Analyzers | Prices checked against Claude Code's records; token size of tool results measured; repeated work, startup cost, lineage (report + files), file conflicts, approvals, parallelism. | Done Nov 23, except the model-based lineage check (waiting for OK) |
| 3. Viewer | One local HTML file per run: tree, a lane per agent on a timeline you can step through, findings pinned in place. | Next |
| 4. Study | Run on my own sessions. Private report first; I decide what can go public (aggregate numbers only). | |
| 5. Ship | Demo run made on purpose on a public repo, redacted. README, one-command install, write-up, demo page. Nothing public without my approval. | |

Open questions
- What are the small same-model calls Claude Code makes without writing them to the transcript? (Lesson 1.) Try the OpenTelemetry export, which labels each call's purpose.
- Lineage by text matching scored 3 of 3 on the one run with a known answer. Validate it properly: ask Haiku (via `claude -p`, about 33 calls) whether each parent relied on each report, and compare.
- Startup cost answered: median 48k tokens of base context per subagent, 23% of subagent cost overall.
