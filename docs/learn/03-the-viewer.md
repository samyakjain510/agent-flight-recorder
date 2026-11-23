# Lesson 3: the viewer

Written for Samyak after Phase 3 (Nov 23, 2026).

## What it is

`flightrec view <session>` writes one HTML file for a run. Open it in any browser. The file holds everything: the run's data, the drawing code and the styles. It needs no server, loads nothing from the internet, and keeps working if you move or email it. `flightrec view --all` writes a page per multi-agent run plus an index page that lists them.

## How it's built

Python reads the transcripts and runs the analyzers, as in Phases 1 and 2. A new module, `view.py`, turns the results into a **payload**: one bundle of plain data (agents, model calls, tool calls, findings, times) for the page to draw. The payload is pasted into a template page, `viewer.html`. When the page opens, a few hundred lines of plain JavaScript draw the timeline as **SVG**, the browser's built-in format for shapes and lines. There are no libraries, so there is nothing to download and nothing that can change under it.

All transcript text is inserted into the page as plain text, never as HTML. If a transcript contains something that looks like code (a report quoting `<script>`, say), it shows up as text and can't run. A unit test checks this.

## Reading a page

- **One lane per agent.** The lead is on top; each subagent sits under the agent that started it. Purple bars above the line are model calls; taller means more context was sent. Green bars below the line are tool calls; orange ones started a subagent; red ones failed.
- **The gray first bar** on a subagent lane is its first model call. That call carries the base context every subagent starts with (system prompt, tool list, memory, instructions), which Lesson 2 found is about a quarter of subagent cost.
- **Dashed orange lines** show a subagent being started. **Arrows** show its report coming back, colored by lineage: green used, amber unclear, gray no trace.
- **Gray bands** are idle time. Any gap over 10 minutes is squeezed into a narrow band labeled with its real length. One of my runs spans 13 days but was active for 5 hours 17 minutes; without squeezing, the work would be a few slivers on an empty screen.
- **Replay.** Play moves a playhead through the run; what hasn't happened yet is faded. The counters show cost, tokens and agents running as of the playhead. The arrow keys step from one event to the next. "At the playhead" lists the latest events: tool calls with their file, command or URL, model calls with their size, and what each agent wrote.
- **Findings** sit on the lanes as small triangles and in a list you can filter: startup cost, repeated work (selecting one draws a link back to the first time), subagent lineage, file conflicts, and your approvals. Clicking one jumps the playhead there.
- **Zoom.** Parallel subagents often all run inside one minute of a long session. "Zoom to its time" on a selected agent fits its work to the screen.

## What it can't show

Tool results are not stored, only their size, so the replay shows what an agent did and how big the answer was, not the answer. Calls Claude Code never writes to the transcript (Lesson 1) are missing from the lanes. Costs are API-equivalent.

## Words

- **Self-contained page:** a single file that carries everything it needs to display.
- **Payload:** the data handed to the page, here as JSON (a plain text format for lists and labelled values).
- **SVG:** a way to describe drawings (rectangles, lines, text) that browsers render directly.
- **Playhead:** the marker for "now" in a replay.
