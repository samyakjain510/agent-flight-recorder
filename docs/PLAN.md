# Plan

| Phase | What | Status |
|---|---|---|
| 1. Reader | Turn transcripts into one clean run model. Cross-check token totals against Claude Code's own. | Done Nov 23 |
| 2. Analyzers | Duplicated work, subagent startup cost, lineage (which reports reached the final answer), file conflicts, approvals. Plain code first; `claude -p` on Haiku only where text matching can't decide, in small batches agreed first. | Next |
| 3. Viewer | One local HTML file per run: tree, a lane per agent on a timeline you can step through, findings pinned in place. | |
| 4. Study | Run on my own sessions. Private report first; I decide what can go public (aggregate numbers only). | |
| 5. Ship | Demo run made on purpose on a public repo, redacted. README, one-command install, write-up, demo page. Nothing public without my approval. | |

Open questions
- What are the small same-model calls Claude Code makes without writing them to the transcript? (Lesson 1.) Try the OpenTelemetry export, which labels each call's purpose.
- How big is the fixed cost of starting a subagent? In one session, each of 8 subagents used about 190k tokens for 4 or 5 calls.
