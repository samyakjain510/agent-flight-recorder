# Lesson 0: the big picture

Written for Samyak. One of these gets added at each phase.

## The problem

More and more AI work runs as a team. One lead agent splits a job into pieces and hands each piece to a subagent, a separate copy of the model with its own instructions and its own memory. The subagents work in parallel and report back. Anthropic says its research system used about 15 times the tokens of a normal chat when it worked this way.

When a run like that goes wrong or costs too much, it is hard to see why. Questions you can't easily answer today:

- Which subagent was told what?
- Did two subagents do the same work?
- Did two of them disagree, and who won?
- Which subagent's work actually made it into the final answer, and which was thrown away?
- When a person approved an action, did they have time to look at it?

## What already exists

Several tools already draw the tree of agents and show how many tokens each one used. Langfuse, Arize Phoenix and Opik have Claude Code plugins; claude-devtools is a free local viewer. Claude Code itself can now report which subagent made each call. So drawing the tree is a solved problem.

Nobody I found answers the questions after the tree: what was duplicated, what was used, where agents disagreed, and how approvals happened. Nobody publishes measurements from real runs either. That gap is this project.

## What we're building

A local tool, `flightrec`, plus a study of my own runs.

```
transcripts on my Mac → reader → analyzers → viewer + replay → findings → public write-up
                        (Phase 1)  (Phase 2)    (Phase 3)       (Phase 4)   (Phase 5)
```

Like an airplane's flight recorder, it reads what was already recorded and reconstructs what happened. Nothing is uploaded anywhere.

## Words you'll see a lot

- **Transcript:** the file Claude Code writes for each session, one line per event. The format is called JSONL: every line is a small JSON record.
- **Lead agent / subagent:** the lead is the agent you talk to. A subagent is a helper the lead starts with the Agent tool. It gets written instructions and reports back.
- **Model call (request):** one round trip to the model: send the conversation so far, get a reply. A session is hundreds of these.
- **Tokens:** the units models read and write, roughly three quarters of a word each. Input tokens come in three kinds:
  - *fresh input*: new text, full price;
  - *cache write*: text stored so the next call can reuse it, a bit above full price;
  - *cache read*: text reused from that store, a small fraction of the price (one twentieth on Opus 5.5).
  Output tokens are what the model writes, including its thinking.
- **Tracing / span:** recording each step of a program as a timed block nested inside the step that started it. That nesting is what gives you a tree.
- **OpenTelemetry (OTel):** the open standard most tracing tools use to send and store spans.
- **Hooks:** small scripts Claude Code runs at moments like "a subagent started" or "permission was requested".
- **Lineage:** where a piece of information came from and where it went. Here: did a subagent's report end up in the final answer?

## Rules this project runs under

- $0. All model work goes through Claude Code on the Pro plan.
- My transcripts contain personal data. They stay on my Mac. Snapshots live in `data/`, which git never tracks.
- Any public demo uses a run made on purpose for the demo, then redacted, and I approve it first.
