# Lesson 4: the study

Written for Samyak after Phase 4 (Nov 23–24, 2026). The numbers are in `docs/write-up.md`; this lesson is about how three of them were made, because they take more than counting.

## Counting once

Forks and resumes copy history into new files. Every total in the study counts a copied model call, subagent, approval or message once, in the file created first. Without that rule, 4 of my 11 multi-agent sessions (one session and its forks) would have counted the same work up to four times.

## An estimate: was delegating cheaper?

You can't measure the run that didn't happen. So for each subagent I built the other path from measured parts:

- **What the lead already carried.** The lead's context size at the moment it started the subagent (median 135k tokens in my runs).
- **What the subagent's work added.** How much the subagent's context grew from its first call to its last: everything it read and wrote.
- **How long that would have stayed.** The number of calls the lead made afterwards.

Then: the lead takes the same steps itself, at its own prices, re-reading its bigger context on every step, and afterwards carries the extra material. Against that, the real cost: what the subagent cost, plus the lead carrying the report.

The weak point is "afterwards carries the extra material". Claude Code can compact or clear old context. So the study gives two answers: one where the material stays for all the lead's later calls, and one where it's dropped at once. A conclusion that holds at both ends doesn't depend on that assumption. Delegating was cheaper in 27 of 30 cases at both ends.

A second check removes a different explanation. Some subagents ran on cheaper models, so they would look cheaper even with no context effect. Pricing every subagent at the lead's model shrinks the count (to 16–19 of 30) but doesn't erase it.

## A baseline: is the overlap real?

Parallel helpers' reports shared a median 17% of their distinctive items (names, paths, links, numbers) with a sibling's report. Shared words could just mean similar topics. So the same measure runs against reports from unrelated sessions: 0%. The difference is the evidence. This is the same idea as the controls in the Haiku check: measure what "nothing going on" looks like before calling something a finding.

## Two methods for one question

"Was this subagent's work used?" got two answers:

- **Text and files** (the tool's method): did distinctive items from the report, or files the subagent wrote, show up in the lead's later work? 96% of subagent spend passes.
- **A blind Haiku judge**: shown the report and what the lead did next, without the tool's verdict. 100% passes.

Where they disagree, neither is simply right. Haiku credited things the lead already knew (5 cases), and it caught a kind of use text can't see: the lead following a recommendation in its own words (2 cases). Weighting by cost shows the disagreement is about $1 of $26, so the headline survives either method. When two imperfect methods agree, the claim is safer than either one alone; where they disagree, I report both.

## Words

- **Counterfactual:** the path that didn't happen, here "the lead does the work itself".
- **Bound:** an end of a range you know the answer lies within.
- **Baseline:** what a measure reads when nothing is going on.
- **Cost-weighted:** counting each case by what it cost, so a $3 subagent matters more than a $0.01 one.
