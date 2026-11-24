# Demo run

A multi-agent Claude Code run made on purpose for flightrec's public demo, on public code, so that nothing private is in it.

- **Task:** a lead agent (Claude Sonnet 5.5) starts three helper subagents (Claude Haiku 5.5) in parallel to review a copy of this repository, then writes `REVIEW.md`. The exact prompt is in [prompt.txt](prompt.txt).
- **Made with** Claude Code 2.1.330 on November 24, 2026:

  ```bash
  claude -p --model sonnet --allowedTools "Read" "Glob" "Grep" "Agent" "Write" \
      --setting-sources project --strict-mcp-config --disable-slash-commands < prompt.txt
  ```

  The helpers tried to run shell commands, which this command doesn't allow; those three refusals show up as permission rejections.

- **Scrubbed** with [`scripts/scrub_transcripts.py`](../../scripts/scrub_transcripts.py): only the records flightrec reads are kept; account ids, the email address Claude Code adds as context, machine details and system-prompt snapshots are removed; paths and names are replaced (`/home/demo`).

Open it:

```bash
flightrec --root examples/demo-run/projects view --all --open
```

The published demo page, `docs/demo/index.html`, was built with:

```bash
flightrec --root examples/demo-run/projects view f00f22b5 --out docs/demo --file index.html \
    --title "Demo: a lead and three Haiku helpers review flightrec's own code"
```
