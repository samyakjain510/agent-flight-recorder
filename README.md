# flightrec

A local flight recorder for multi-agent Claude Code runs. It reads the transcripts Claude Code already writes on your machine and shows the tree of lead agent and subagents, the instructions each one got, and tokens and time per branch. Analysis of duplicated work, lineage and approvals is in progress.

Nothing leaves your machine. There are no network calls and no dependencies beyond Python 3.9+.

**Status:** Phase 1 of 5 (the reader). Private while in progress.

## Try it

```bash
git clone <this repo> && cd agent-flight-recorder
python3 -m pip install -e .        # or skip installing and use: PYTHONPATH=src python3 -m flightrec
flightrec list                     # sessions that used subagents
flightrec show <session-id-prefix> # the tree, with tokens and time per branch
flightrec check --all              # compare token counts with Claude Code's own totals
```

Add `--redact` before the command to hide titles, descriptions and instructions, for example when sharing a screenshot.

## How we know the numbers are right

`flightrec check` compares the reader's token counts with the running totals Claude Code keeps for each session. On my machine (Nov 23, 2026), across 42 sessions where a fair comparison is possible, the reader never counted more than Claude Code did, and matched it exactly in 17. The rest of the gap comes from calls Claude Code never writes to the transcript. See `docs/learn/01-reading-transcripts.md`.

## Tests

```bash
python3 -m unittest discover -s tests
```

## License

MIT
