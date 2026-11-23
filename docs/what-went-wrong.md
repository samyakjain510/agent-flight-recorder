# What went wrong

A running log of mistakes, wrong guesses and surprises, and what changed because of each. Material for the write-up.

## Phase 1 (Nov 23, 2026)

**I assumed one transcript file was one run.** About a quarter of all model calls (1,121 of 4,700) turned out to live in more than one file, because forks and resumes copy history. Counting per file would have double-counted them. Change: an index of which files share each call, and a "copied history" line in every report.

**I assumed Claude Code's own totals would match the transcript.** The first cross-check flagged 25 of 42 clean sessions as mismatches. All of them failed the same way: Claude Code counted more than the transcript showed, never less. Change: the check now enforces a one-way rule (the reader may undercount, never overcount) and reports the size of the gap. Result: 0 overcounts.

**Five guesses about the hidden calls were wrong.** I tested the auto-mode safety check, the advisor tool, multi-step calls, session naming and idle recaps. None matched the pattern. Change: I stopped guessing, recorded the exact signature (about 506 fresh tokens in, about 20 out, cached conversation read), and listed it as an open question. Next step is Claude Code's OpenTelemetry export, which labels each call's purpose.

**"Approvals: 0 decisions" was misleading.** Older Claude Code versions didn't record approvals, so zero meant "unknown". Change: the report now says "none recorded" for those sessions.

**I started three research agents at once before checking with Samyak.** He asked, during that same turn, to be told before several agents run in parallel because they use his Pro limits. Change: I ask first now.

## Phase 2 (Nov 23, 2026)

**Solving for prices freely gave a negative price.** Fitting Opus 5.5 prices to Claude Code's cost records with no constraints produced nonsense, because older records don't split cache writes into 5-minute and 1-hour kinds. Change: I tested the published list prices directly. They reproduce the recorded cost to the cent in 37 of 38 records that do have the split.

**Lineage only looked at the report text.** In one session, 6 of 8 subagents showed "no trace found". Their real work came back as files: 17 of 33 subagents write files, and many send only a short "done" report. Change: lineage also tracks what happens to each file a subagent writes.

**Text matching missed paraphrase.** On the planning session for this project, where I know all 3 research reports were used, the first version found 1. Change: name-shaped words (CamelCase, acronyms, hyphenated names) count as evidence, since paraphrase usually keeps names. Now 3 of 3, but that is a small test; a model-based check is proposed.

**Image reads looked like 42 repeats.** The fingerprint only covered text, so every image or PDF read had the same empty fingerprint. I first guessed these were Claude Code's own "file unchanged" stubs; checking the fingerprints showed they were all empty text. Change: fingerprint the whole result, flag results with images, and skip empty ones. Result: zero repeats inside any agent.

**A URL was counted twice.** Part of a URL also matched the file-path pattern. A unit test caught it. Change: URLs are removed before looking for paths and names.

**The brief's example finding turned out small.** The brief suggested measuring "how much of the tokens went to re-reading the same files". For my runs that is at most 0.5% of cost. The bigger fixed cost is starting a subagent: a median of 48k tokens of context before any work. He asked, during that same turn, to be told before several agents run in parallel because they use his Pro limits. Change: I ask first now.
