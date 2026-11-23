# Lesson 1: reading the black box

Written for Samyak after Phase 1 (Nov 23, 2026).

## What's in a transcript

Every Claude Code session writes `~/.claude/projects/<folder>/<session-id>.jsonl`. Each subagent gets its own file next to it, in `<session-id>/subagents/agent-<id>.jsonl`, plus a small `.meta.json` that says what kind of agent it was and which tool call started it.

The lines that matter most:

| Line type | What it holds |
|---|---|
| `assistant` | part of one model reply: thinking, text, or a tool call, plus the token counts for that call |
| `user` | your messages, and the result of each tool call, including the approval decision |
| `attachment` | extra context, including reports that background subagents send back |
| `cost-state` | Claude Code's own running total of tokens and cost for the session |

My history (Aug 27 to Nov 23) spans 21 Claude Code versions. Anthropic's docs say the format is internal and can change on any release. So all format knowledge lives in one file, `src/flightrec/claude_code.py`, with the assumptions written at the top and a test for each.

## How the tree is rebuilt

The lead starts a subagent with an Agent tool call. That call has an id. The subagent's `.meta.json` stores the same id, and the call's result stores the subagent's id. Matching the two gives an exact parent-to-child link and the instructions the parent wrote. In my 8-subagent session, all 8 linked.

## Three traps, and what the reader does about each

1. **One call, several lines.** Claude Code writes a reply as one line per piece (thinking, text, each tool call), and every line repeats the token counts. Adding up lines counts the same tokens more than once. The reader groups lines by request id and counts each call once.
2. **Copied history.** When a session is forked or resumed, Claude Code copies earlier lines into a new file. As of Nov 23, 1,121 of 4,700 model calls in my history (24%) appear in more than one file. The reader marks those calls as shared, so copied history never counts as new spend.
3. **Calls the transcript never shows.** Claude Code makes some model calls that are never written to the transcript. See below.

## How we know the reader is right

Claude Code keeps its own running total per session. I compared the reader's totals with it for every session, model by model.

A fair comparison only works on "clean" sessions: no copied history, never compacted, not still running. The rule for those: the reader may count *less* than Claude Code (some calls aren't in the transcript), but it must never count *more*. Counting more would mean a double-counting bug.

Results across my 113 sessions (76 with Claude Code totals, 42 clean):

- **0 overcounts.** The reader never counted a token Claude Code didn't.
- **17 exact matches**, to the token, on every model.
- **25 sessions where the transcript is missing calls.** Each missing call looks the same: about 506 fresh input tokens, a read of the cached conversation, and a reply of about 20 tokens. There are 1 to 14 per session. Together they are 9.14M of 50.47M tokens in those sessions (18.1%), almost all of it cheap cache reads. I tested five explanations (the auto-mode safety check, the advisor tool, multi-step calls, session naming, idle recaps) and none fit. They are an open question. Claude Code's OpenTelemetry export labels each call with its purpose, so that's the next place to look.
- **Calls on models the transcript never shows:** 1.40M of 92.27M tokens (1.5%), all on Haiku, in 6 sessions. These are Claude Code's own side jobs.

## Tokens are not dollars

The hidden calls are 18% of the tokens in those sessions, but most of them are cache reads, which cost a twentieth of fresh input. A finding can look big in tokens and small in money, or the other way round. The write-up will always say which one it means.

## What the reader can't see (so far)

- Calls Claude Code makes without writing them down (above).
- The summarizing call behind each compaction.
- Approvals in sessions from older versions, which didn't record them.
- Subagents that left no transcript file. They appear in the tree as stubs, with their instructions but no token counts.
