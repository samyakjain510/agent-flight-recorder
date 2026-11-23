# Plan

| Phase | What | Status |
|---|---|---|
| 1. Reader | Turn transcripts into one clean run model. Cross-check token totals against Claude Code's own. | Done Nov 23 |
| 2. Analyzers | Prices checked against Claude Code's records; token size of tool results measured; repeated work, startup cost, lineage (report + files), file conflicts, approvals, parallelism. | Done Nov 23, including a model check of the lineage verdicts |
| 3. Viewer | One local HTML file per run: tree, a lane per agent on a timeline you can step through, findings pinned in place. | Done Nov 23 |
| 4. Study | Run on my own sessions. Private report first; I decide what can go public (aggregate numbers only). | `flightrec study` built; private report drafted Nov 23; lineage check complete (30 reports, 12 controls). Waiting on Samyak: which numbers go public, and what it changed for him |
| 5. Ship | Demo run made on purpose on a public repo, redacted. README, one-command install, write-up, demo page. Nothing public without my approval. | |

Open questions
- What are the small same-model calls Claude Code makes without writing them to the transcript? (Lesson 1.) Try the OpenTelemetry export, which labels each call's purpose.
- Lineage checked against Haiku in two rounds (30 reports, 12 controls): "used" agreed 15 of 15; "unclear" fired on 3 of 12 controls; Haiku said used for all 30, crediting content the lead already had (5) and recommendations followed in other words (2). By cost: 96% (text + files) vs 100% (Haiku). Details in lesson 2.
- Startup cost answered: median 48k tokens of base context per subagent, 23% of subagent cost overall.
- Install: the macOS system Python's pip (21.2) can't read pyproject-only projects (it built an empty "UNKNOWN" package) and can't do editable installs. Before shipping, test the one-command install (likely `pipx install git+…` or `pip install .` with a current pip) on a clean setup.
