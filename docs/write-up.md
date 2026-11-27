# What I learned recording my own multi-agent runs

I use Claude Code most days, and more of that work now runs as a team: a lead agent hands pieces of a job to subagents, which work in parallel and report back. I wanted to know where the tokens went in those runs, whether the subagents' work was used, and who was approving what. Tools that draw the agent tree already exist. I couldn't find one that answered those questions, or anyone publishing measurements from real runs. So I built flightrec, a local tool that reads the transcripts Claude Code already writes, and pointed it at my own history.

The data: 113 Claude Code sessions on my Mac as of November 23, 2026, of which 11 used subagents, between October 9 and November 22. One of the 11 is the session in which I built this tool, so its counts were still growing while I measured; the numbers here are from a single run of `flightrec study` on November 23. Those 11 sessions started 30 subagents, most of them research helpers. That's a small sample from one person, so read everything below as a description of my runs.

## Checking the tool before trusting it

Claude Code's transcript format is internal and changed 21 times in the three months of data I have, so I checked the reader before reading anything into its numbers.

Claude Code keeps its own running token totals. In 43 sessions where a fair comparison is possible, flightrec matched them exactly in 17 and never counted more. The gap in the rest is calls Claude Code makes without writing them to the transcript (more on that below).

I worked prices out from Claude Code's own cost records instead of copying a price list. List prices reproduce the recorded cost to the cent in 37 of 38 records.

Tool output in my runs packs about 2 characters per token. The usual rule of thumb is 4, which would undercount tool output by about 40%.

Whether subagent work was used I checked two ways, described in finding 6.

## What I measured

All costs are API-equivalent: what the tokens would cost at list prices. I pay a flat subscription, so I use them to compare kinds of work.

### 1. Subagents were a small part of the spend

In sessions that used them, subagents accounted for 15% of the cost. The lead's own long conversation was the rest.

### 2. Starting a subagent has a fixed cost

Every subagent begins with a base context (system prompt, tool list, memory, its instructions) before it does anything: a median of 48k tokens in my runs. Carrying that through every call was 23% of all subagent cost. Small jobs paid the most for it: subagents that made 1 to 5 model calls spent a median of 56% of their cost on it.

### 3. Delegating was still cheaper than doing the work in the lead

This is the one estimate in the study. For each subagent I priced the other path: the lead takes the same steps itself, then carries what it read. When each subagent started, the lead's context was already a median of 135k tokens, bigger than any subagent's starting context, and every call re-reads the whole context. Delegating cost 52% to 67% of the estimate, depending on whether the lead would have kept the material in context or dropped it. It was cheaper in 27 of 30 cases either way. Some of that comes from subagents running on cheaper models; pricing them at the lead's model, delegating still won in 16 to 19 of 30. It lost when the lead's session was young (92k tokens of context) and had only 5 to 7 calls left.

Read with finding 2, a small subagent spends most of its money getting started and is still cheaper than a long lead session doing the same job.

### 4. Parallel helpers saved time

Five times a lead started several helpers at once, in groups of 8, 3, 3, 3 and 2. Those groups did 57 minutes of helper work in 27 minutes.

### 5. Parallel helpers overlapped

For each helper's report I took its distinctive items (names, file paths, links, numbers it wasn't given in its instructions) and checked how many also appeared in a sibling's report. The median was 17%, and up to 63%. For reports from unrelated sessions the same measure is 0%, so shared vocabulary doesn't explain it. Repeated file reads show it too: 13 of 16 were a helper reading a file a sibling had already read. Those reads cost little, about 0.4% of spend. The overlap in what the helpers concluded may matter more, and I can't price it.

### 6. Nearly all subagent work was used

I checked whether each subagent's work reached the lead in two ways. flightrec looks for the subagent's distinctive items in what the lead wrote afterwards, and for the lead reading or editing files the subagent wrote. A blind judge (Claude Haiku, shown the report and what the lead did next, without flightrec's verdict) gave a second opinion. By cost, 96% of subagent spend passed flightrec's check and 100% passed the judge's.

Where flightrec said "used", the judge agreed 15 times out of 15. To test for false alarms I also showed it 12 reports next to a lead from a different session, where the answer has to be "not used". The judge got all 12 right. flightrec gave a weak match on 3, which is why I call its weak-match verdict "unclear". Where flightrec found nothing and the judge said "used", the judge was mostly crediting things the lead already knew. Twice it caught something text matching can't see: the lead following the subagent's recommendation in its own words.

### 7. No conflicts

No two agents wrote the same file. My helpers were researchers, so this tells me nothing about how safe parallel editing is.

### 8. Settings made the approval decisions

In the multi-agent sessions, settings made all 1,766 permission decisions. Across all 113 sessions I made 4 of 5,176 myself, 2 of them rejections, and answered 16 questions an agent asked me. I steered through conversation: 159 messages during multi-agent runs. In practice my permission settings decided what the agents could run.

## What the tool can't see

In the 43 sessions I could check, 10% of tokens were in model calls Claude Code counted but never wrote down. Each looks like about 500 new input tokens plus the cached conversation, with about 20 tokens out. I tested five guesses about what they are, and none fit.

When Claude Code compacts a conversation, the summarizing call isn't in the transcript either. My history had 17 compactions.

Transcripts keep what each tool returned, but flightrec keeps only the size. Images and documents (1,108 in my history) can't be sized in tokens this way.

flightrec shows whether a subagent's text or files reappeared. It can't tell whether they changed the lead's mind.

The format is internal to Claude Code and changes without notice. flightrec keeps all knowledge of it in one file and reports record types it doesn't recognize. While I prepared the demo below, a new Claude Code version changed how background subagents deliver their reports, and flightrec needed a fix to read them.

## What went wrong along the way

The study exposed a bug in its own reader. The first full run said two subagents never reported back and that 24% of subagent spend was wasted. Their transcripts ended with a hand-back call, so I looked in the lead's file and found the reports. flightrec had been skipping every background subagent's report, because Claude Code marks those records as not typed by a person, and the reader dropped such records before checking what they were. My test used an older record shape, so it passed. With the fix, the share of subagent spend that reached the lead went from about 70% to 96%. Had I trusted the first run, I would have reported that a quarter of subagent spend was wasted.

Other mistakes, and what changed because of each, are in [what-went-wrong.md](what-went-wrong.md).

## Try it

flightrec runs on Python 3.9 or newer with no other dependencies and makes no network calls. Nothing leaves your machine.

```bash
pipx install git+https://github.com/samyakjain510/agent-flight-recorder
flightrec view --all --open
```

The [demo page](https://samyakjain510.github.io/agent-flight-recorder/demo/) shows a run I made on purpose for this write-up: a lead and three Haiku helpers reviewing flightrec's own code. `flightrec study` prints every number in this write-up for your own transcripts.
