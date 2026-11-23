# What went wrong

A running log of mistakes, wrong guesses and surprises, and what changed because of each. Material for the write-up.

## Phase 1 (Nov 23, 2026)

**I assumed one transcript file was one run.** About a quarter of all model calls (1,121 of 4,700) turned out to live in more than one file, because forks and resumes copy history. Counting per file would have double-counted them. Change: an index of which files share each call, and a "copied history" line in every report.

**I assumed Claude Code's own totals would match the transcript.** The first cross-check flagged 25 of 42 clean sessions as mismatches. All of them failed the same way: Claude Code counted more than the transcript showed, never less. Change: the check now enforces a one-way rule (the reader may undercount, never overcount) and reports the size of the gap. Result: 0 overcounts.

**Five guesses about the hidden calls were wrong.** I tested the auto-mode safety check, the advisor tool, multi-step calls, session naming and idle recaps. None matched the pattern. Change: I stopped guessing, recorded the exact signature (about 506 fresh tokens in, about 20 out, cached conversation read), and listed it as an open question. Next step is Claude Code's OpenTelemetry export, which labels each call's purpose.

**"Approvals: 0 decisions" was misleading.** Older Claude Code versions didn't record approvals, so zero meant "unknown". Change: the report now says "none recorded" for those sessions.

**I started three research agents at once before checking with Samyak.** He asked, during that same turn, to be told before several agents run in parallel because they use his Pro limits. Change: I ask first now.
