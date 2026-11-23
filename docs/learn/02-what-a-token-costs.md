# Lesson 2: what a token costs, and where the work went

Written for Samyak after Phase 2 (Nov 23, 2026). Numbers are from my own transcripts on that date; they move a little as new sessions are written.

## Turning tokens into dollars

Tokens of different kinds cost very different amounts, so token counts alone can mislead. To compare fairly I needed each model's price per token type. I didn't copy a price list. Claude Code saves its own dollar cost next to its token counts, so I worked backwards from those records.

The published list prices fit. For Opus 5.5, per million tokens: $4 fresh input, $5 for a 5-minute cache write, $8 for a 1-hour cache write, $0.20 for a cache read, $20 for output. They reproduce Claude Code's recorded cost to the cent in 37 of 38 Opus records that split cache writes by duration. The one miss costs 1.8 times the prediction, which looks like fast mode (twice the price). Priced call by call, the reader's totals match Claude Code's to the cent in all 12 clean sessions where every model has a known price.

I pay a flat Pro fee, so these dollars are "what the API would charge". They are a fair way to weigh a cheap cache read against an expensive output token, not a bill.

## How big is a tool result, in tokens?

Transcripts store tool results as text. Between two calls of the same agent, the input grows by exactly what was added. When exactly one tool result arrived in between, that growth is the result's size in tokens. Across 835 such steps in my history:

- command output (Bash): about 2.05 characters per token
- file reads: about 2.44
- all tools together: about 2.02

The usual rule of thumb is 4 characters per token. That holds for plain English. Code, file listings and line-numbered output pack fewer characters per token. Estimating with 4 would understate tool output by about 40%. Screenshots carry no text, so their size can't be measured this way; the tool says so.

## Carried cost: why early reads cost more

Every model call in an agent loop resends the whole conversation. Anything added to an agent's context is written to the cache once and then read back on every later call. So a tool result's real cost is its size times (one cache write plus one cache read per later call). A large file read on step 2 of a 60-step agent is paid for 59 more times. This assumes the content stays in context; Claude Code can clear old results, so carried numbers are an upper bound.

## What the analyzers found across my 11 multi-agent sessions

These are first results, not the study.

- **Total:** about $162 API-equivalent. Subagents were 16% of it.
- **Startup cost:** a subagent starts with a median of 48k tokens of context before it does anything: system prompt, tool list, memory files, its instructions. Across all subagents that base context is 23% of their cost. For small jobs it dominates. In one session, each of 8 subagents started with about 42k tokens and ran only 4 or 5 calls, so the base context was 68 to 83% of each one's cost.
- **Repeated work:** zero repeats inside a single agent. Sixteen times one agent read a file another had already read, all of them file reads, at most $0.77 (0.5% of cost). For my runs, re-reading files is a small cost.
- **Where subagent work went** (33 subagents): 20 used, 4 with some trace, 3 left a file nobody else touched, 2 with no trace found, 4 never sent a report.
- **File conflicts:** none. No two agents wrote the same file.
- **Approvals:** a person decided 1 of 2,029 permission decisions in these sessions. Settings decided the rest.

## How "used" is decided, and how much to trust it

Work comes back two ways, so lineage checks both:

1. **The report.** Does text from the subagent's final report show up in what its parent wrote afterwards? It looks for distinctive items: URLs, file paths, numbers, and name-shaped words like `SubagentStop` or `claude-devtools`. It ignores anything the parent already had.
2. **Files.** If the subagent wrote a file, did another agent read it, use it in a command, edit it, or write over it?

I tested it on the one run where I know the answer: the planning session for this project. Three research helpers reported back and I used all three, in my own words. The first version, which matched URLs, paths, numbers and phrases, called only 1 of the 3 "used". Adding names fixed it: 3 of 3.

Three known answers is a thin test, and names make false alarms easier. The proper check is to ask a model, for each subagent, whether the parent's later work relied on its report, and compare. That is about 33 short Haiku calls through `claude -p`. I'll run it only with Samyak's OK.

## Words

- **Least squares:** a way to find the numbers that best fit many equations at once. Here, the prices that best explain many recorded costs.
- **Fingerprint (hash):** a short code computed from content. Identical content always gives the same code, and different content almost never does.
- **Carried cost:** what a piece of context costs over the rest of an agent's life, because it is re-read on every later call.
- **Lineage:** where a piece of work went after it was produced.
