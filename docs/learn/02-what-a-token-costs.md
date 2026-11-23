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
- **Where subagent work went** (30 subagents): 22 used, 4 unclear, 2 left a file nobody else touched, 2 with no trace found. (A first count said 33; forks carry copies of their source's subagents, so 3 were counted twice. Until Phase 4 the reader also dropped reports from background subagents, which made 2 look like they never reported; see what-went-wrong.)
- **File conflicts:** none. No two agents wrote the same file.
- **Approvals:** in the calls these sessions made themselves, a person decided none of about 1,660 permission decisions. Settings decided all of them. (A first count said 1 of 2,029; that one human decision was a copy from an older file.)

## How "used" is decided, and how much to trust it

Work comes back two ways, so lineage checks both:

1. **The report.** Does text from the subagent's final report show up in what its parent wrote afterwards? It looks for distinctive items: URLs, file paths, numbers, and name-shaped words like `SubagentStop` or `claude-devtools`. It ignores anything the parent already had.
2. **Files.** If the subagent wrote a file, did another agent read it, use it in a command, edit it, or write over it?

I tested it on the one run where I know the answer: the planning session for this project. Three research helpers reported back and I used all three, in my own words. The first version, which matched URLs, paths, numbers and phrases, called only 1 of the 3 "used". Adding names fixed it: 3 of 3.

Three known answers is a thin test, so I checked it against a model.

### Checking the checker

`scripts/judge_lineage.py` asks Haiku, once per subagent report, whether the lead's later work relied on it. Haiku sees the instructions, the report, a little of what the lead wrote before, and the next 60,000 characters of what the lead did after. It never sees my verdict. Mixed in are 6 controls: a report shown next to a lead from a different session, where the right answer has to be "not used".

18 reports qualified at the time. (In Phase 4 I found the reader had been dropping reports from background subagents; with that fixed, 30 of 30 subagents have a report, so 12 more await the same check.) The run took 24 calls, about 3 minutes, and 680k Haiku tokens, about $0.12 at API prices, on my Pro plan.

| My verdict | Haiku said | Reading |
|---|---|---|
| used (9) | relied 8, partly 1 | Agree in all 9. |
| some trace (4) | relied 3, partly 1 | Haiku says used. One case shows real use my matcher underrated. |
| no trace found (5) | relied 3, partly 2 | Disagree in all 5. See below. |
| controls (6) | not used 6 | Haiku right in all 6. My matcher said "some trace" on 2 of them. |

Neither side is ground truth, so I checked Haiku's evidence without reading it. For each case, the script takes the names, numbers and longer words Haiku cites, keeps the ones found in both the report and the lead's later work, and asks whether the lead already had them before the report arrived. When both methods say "used", 1 to 7 cited items are new. In 4 of the 5 "no trace found" cases, every item Haiku cites was already in the lead's earlier work. Haiku was crediting the report with things the lead already knew, even though the prompt told it not to. Four of those five are from one session where 8 helpers did similar jobs in parallel, so the lead had already seen the same content from a sibling.

Haiku's confidence lines up with this. All 6 of its confidence-5 verdicts have at least one new item. Its confidence-3 verdicts have at most one.

What changed in the tool:

- **"used" stays.** A model agreed in 9 of 9.
- **"some trace" is now "unclear".** A single matching item or a little shared phrasing also happened by chance in 2 of 6 mismatched pairs, so it can't tell use from coincidence.
- **"no trace found" stays, with a clearer meaning:** nothing new from the report reached the later work. The lead may still have read it and found nothing it didn't already have, which is its own kind of waste when helpers run in parallel on overlapping jobs.

Limits: 18 real cases, 8 from one session. A model judge is not ground truth either; it over-credits shared topics. Text matching misses use that leaves no names or numbers behind, such as a recommendation followed in different words.

## Words

- **Least squares:** a way to find the numbers that best fit many equations at once. Here, the prices that best explain many recorded costs.
- **Fingerprint (hash):** a short code computed from content. Identical content always gives the same code, and different content almost never does.
- **Carried cost:** what a piece of context costs over the rest of an agent's life, because it is re-read on every later call.
- **Lineage:** where a piece of work went after it was produced.
- **Control:** a case where the right answer is known in advance (here, "not used"), mixed in to catch a method that says yes too easily.
- **Blind:** the judge doesn't see the answer it is being compared with.
