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

**The brief's example finding turned out small.** The brief suggested measuring "how much of the tokens went to re-reading the same files". For my runs that is at most 0.5% of cost. The bigger fixed cost is starting a subagent: a median of 48k tokens of context before any work.

**Forks counted the same subagents twice.** A forked session carries copies of its source's subagents. The cross-session summary counted 33 subagents; 30 are distinct. Approvals had the same problem: the "1 human approval out of 2,029" was a copy of a decision from an older file. Change: the summary counts each subagent, approval and conflict once, in the file created first, the same rule already used for model calls. Corrected: 30 subagents, 0 of 1,645 approval decisions made by a person.

**I said the model check would take about 33 calls.** It took 18 real cases plus 6 controls. Three subagents were double-counted (above), and 12 sent no text report: their work came back as files, which the file channel already records as fact.

**My weak-match verdict fired by chance.** In a blind check, I showed reports next to a lead from a different session, where the right answer has to be "not used". My matcher said "some trace" on 2 of 6. Change: renamed it "unclear".

**The model judge over-credited.** Haiku said all 18 real reports were used at least partly. In 4 of the 5 cases where my matcher found nothing, everything Haiku cited was already in the lead's work before the report arrived, despite a prompt telling it that doesn't count. Change: I kept text matching as the tool's method and the model as a cross-check, and checked the model's evidence mechanically instead of trusting its verdict.

## Phase 3 (Nov 23, 2026)

**Idle bands crowded out the work.** My longest run has 33 idle gaps. At a fixed width per gap, the squeezed bands took 40% of the screen. Change: all breaks together get about a tenth of the active time, each between 0.4% and 2% of it.

**Selecting an agent hid its own work.** Clicking an agent moved the playhead to its start, so everything it did was in the faded "not yet happened" part. Change: the playhead goes to when its report came back.

**Parallel helpers were unreadable at full width.** Eight subagents that all ran inside one minute of a 75-minute session showed up as a sliver. Change: a "Zoom to its time" button fits one agent's work to the screen.

**An answer looked like an approval.** Claude Code records you answering a question the agent asked (`AskUserQuestion`) as a permission decision, so the page said "You approved AskUserQuestion". Change: the page now says you answered a question. The approval counts in Phase 2 include these too; the study needs to separate them.

**The app's browser won't run scripts in local files outside the project folder.** To check pages I served the redacted folder from a small local web server (localhost only). Only redacted pages were ever opened in a browser I can see.

## Phase 4 (Nov 23, 2026)

**The reader dropped every report from a background subagent.** It skipped records Claude Code marks `isMeta` (not typed by a person) before checking whether they carried a subagent's report, and all 24 background reports in my history carry that mark. My test used an older delivery shape, so it passed. I found it because the first study run said 24% of subagent spend went to two subagents that "never reported"; their transcripts ended with a hand-back call, so I checked the lead's file. Change: reports are read before meta records are skipped, with a test using the real shape (it fails on the old reader). Effects: every subagent reported; lineage is now 22 used, 4 unclear, 2 file left, 2 no trace (was 19 / 4 / 3 / 2 plus 2 "no report"); 12 reports that the Haiku check never saw now exist.

**The controls in the Haiku check were named by position.** "control-2" meant the second pair drawn from the list of real cases. When the fix above added cases, the same name pointed at a different pair. Change: each control is named after its pair; the first round's control results are kept but set aside.

**Messages from me were counted twice in forks.** A fork copies earlier messages. Change: each message is counted once by its time (170 became 158).

**The delegation estimate leaned on one assumption.** Most of the estimated saving came from the lead carrying the subagent's reading for its remaining calls, which assumes nothing is compacted or cleared. Change: it is reported as a range, from "kept" to "dropped right away".

**I read too much into Haiku's confidence.** After round 1, Haiku's confidence-5 verdicts always came with new items, and I wrote that confidence lined up with evidence. Round 2 had three confidence-5 verdicts with no new items, all cases of the lead following a recommendation in its own words. Change: the lesson now treats confidence as a hint, and the report names the kind of use text matching can't see.

## Phase 5 (Nov 24, 2026)

**Claude Code changed how background reports arrive, again.** The demo run used Claude Code 2.1.330, where a finished background subagent's report comes as a "task-notification" message with the report inside tags. flightrec showed all three demo helpers as never reporting, the same symptom as the Phase 4 bug in a new shape. Change: the reader parses task notifications, with a test that also checks a shell task's notification isn't mistaken for a report. My own 11 study sessions don't use this shape, so the study's numbers didn't change.

**Transcripts carry more personal data than paths.** The first scrub of the demo replaced my home folder and user name and still failed its own check: Claude Code writes my email address into every transcript as context for the model, and records also hold account and organization ids, machine details and snapshots of its system prompt. Change: the scrub keeps only the records and attachments flightrec reads, removes account ids, replaces every email address, and refuses to finish if anything on its watch list remains.

**The write-up's numbers drifted while I wrote it.** The session I built this in is one of the 11 I measured, so its counts keep growing. My first draft took three numbers from an earlier run than the saved study. Change: the write-up is dated, and a script checks every number in it against the saved study output.
