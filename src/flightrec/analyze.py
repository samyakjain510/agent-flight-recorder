"""Analyzers: turn a Run into findings.

Every number here comes from the transcript. Where we estimate (token size of
a tool result, carried cost), the method is written next to the code and in
docs/learn/02-what-a-token-costs.md.

Carried cost: in an agent loop every model call resends the whole
conversation. Anything added to an agent's context is written to the prompt
cache once and then read back on every later call of that agent. So a tool
result's real cost is

    size x (cache-write price  +  later calls x cache-read price)

This assumes the content stays in context until the agent stops. Claude Code
can clear old tool results or compact the conversation, so treat carried
numbers as an upper bound.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

from .model import Agent, Run, ToolCall, Usage
from .pricing import PRICES, request_cost, usage_cost

DEFAULT_CHARS_PER_TOKEN = 2.0  # median measured on the author's transcripts (Nov 2026); calibrate() replaces it


def _ts(t: Optional[datetime]) -> float:
    return t.timestamp() if t else 0.0


def main_model(agent: Agent) -> Optional[str]:
    models = Counter(r.model for r in agent.requests)
    return models.most_common(1)[0][0] if models else None


def write_price(agent: Agent) -> float:
    """Average cache-write price for this agent, weighted by its 5-minute / 1-hour mix."""
    p = PRICES.get(main_model(agent) or "")
    if not p:
        return 0.0
    u = agent.usage()
    share_1h = (u.cache_write_1h / u.cache_write) if u.cache_write else 0.0
    return p[1] * (1 - share_1h) + p[2] * share_1h


def read_price(agent: Agent) -> float:
    p = PRICES.get(main_model(agent) or "")
    return p[3] if p else 0.0


# ---------------------------------------------------------------- token size of tool results

@dataclass
class TokenScale:
    """Characters per token, measured per tool from the transcripts themselves."""

    by_tool: Dict[str, float]
    overall: float
    samples: int

    def ratio(self, tool: str) -> float:
        return self.by_tool.get(tool, self.overall)

    def tokens(self, call: ToolCall) -> int:
        return int(round(call.result_chars / self.ratio(call.name)))


def one_result_steps(agent: Agent) -> Iterator[Tuple[str, int, int]]:
    """(tool name, result chars, tokens added) for every step where exactly one
    tool result, and nothing else, arrived between two calls of the same model."""
    reqs = {r.id: r for r in agent.requests}
    calls = {c.id: c for c in agent.tool_calls}
    prev, between = None, []
    for ev in agent.events:
        if ev.kind != "call":
            between.append(ev)
            continue
        if prev and len(between) == 1 and between[0].kind == "result" and between[0].chars >= 200:
            a, b, c = reqs.get(prev), reqs.get(ev.ref or ""), calls.get(between[0].ref or "")
            if a and b and c and a.model == b.model:
                added = b.usage.total_input - a.usage.total_input - a.usage.output
                if added > 0:
                    yield c.name, between[0].chars, added
        prev, between = ev.ref, []


def calibrate(runs: Iterable[Run], min_samples: int = 10) -> TokenScale:
    per_tool: Dict[str, List[float]] = defaultdict(list)
    for run in runs:
        for agent in run.agents.values():
            for name, chars, tokens in one_result_steps(agent):
                per_tool[name].append(chars / tokens)
    every = [x for v in per_tool.values() for x in v]
    overall = statistics.median(every) if every else DEFAULT_CHARS_PER_TOKEN
    by_tool = {k: statistics.median(v) for k, v in per_tool.items() if len(v) >= min_samples}
    return TokenScale(by_tool, overall, len(every))


def later_calls(agent: Agent) -> Dict[str, int]:
    """For each tool result: how many model calls this agent made after it arrived."""
    remaining = sum(1 for e in agent.events if e.kind == "call")
    out: Dict[str, int] = {}
    for e in agent.events:
        if e.kind == "call":
            remaining -= 1
        elif e.kind == "result" and e.ref and e.ref not in out:
            out[e.ref] = remaining
    return out


# ---------------------------------------------------------------- 1. repeated work

def target(call: ToolCall) -> Optional[str]:
    """What a read-like tool call looked at. None for tools that change things."""
    i = call.input
    if call.name == "Read":
        return f"file {i.get('file_path')} {i.get('offset', '')}:{i.get('limit', '')}"
    if call.name == "WebFetch":
        return f"page {i.get('url')}"
    if call.name == "WebSearch":
        return f"search {str(i.get('query', '')).strip().lower()}"
    if call.name == "Bash":
        return f"command {str(i.get('command', '')).strip()}"
    if call.name in ("Grep", "Glob", "LS", "NotebookRead"):
        return f"{call.name.lower()} {json.dumps(i, sort_keys=True)}"
    return None


@dataclass
class Repeat:
    call: ToolCall
    first: ToolCall
    within_agent: bool  # True: this agent already had the identical result
    tokens: int
    later_calls: int
    cost: float  # API-equivalent: one cache write plus a cache read per later call

    @property
    def carried_tokens(self) -> int:
        return self.tokens * (1 + self.later_calls)


def find_repeats(run: Run, scale: TokenScale) -> List[Repeat]:
    later = {a.id: later_calls(a) for a in run.agents.values()}
    holders: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    first_of: Dict[Tuple[str, str], ToolCall] = {}
    out = []
    calls = [c for c in run.tool_calls() if c.result_hash and not c.is_error and target(c)]
    calls.sort(key=lambda c: _ts(c.finished or c.started))
    for c in calls:
        key = (target(c) or "", c.result_hash or "")
        if key not in first_of:
            first_of[key] = c
            holders[key].add(c.agent_id)
            continue
        agent = run.agents[c.agent_id]
        n_later = later[c.agent_id].get(c.id, 0)
        tokens = 0 if c.result_media else scale.tokens(c)  # image/document size can't be measured from text
        cost = tokens * (write_price(agent) + n_later * read_price(agent)) / 1e6
        out.append(Repeat(c, first_of[key], c.agent_id in holders[key], tokens, n_later, cost))
        holders[key].add(c.agent_id)
    return out


# ---------------------------------------------------------------- 2. startup cost

@dataclass
class Startup:
    agent: Agent
    first_input: int  # tokens in the first call's input: system prompt, tools, memory, instructions
    calls: int
    total_input: int
    carried_cost: float  # cost of that base context across all of the agent's calls
    total_cost: float

    @property
    def token_share(self) -> float:
        return min(1.0, self.first_input * self.calls / self.total_input) if self.total_input else 0.0

    @property
    def cost_share(self) -> float:
        return min(1.0, self.carried_cost / self.total_cost) if self.total_cost else 0.0


def startup_costs(run: Run) -> List[Startup]:
    out = []
    for agent, _ in run.tree():
        reqs = sorted(agent.requests, key=lambda r: _ts(r.started))
        if not reqs:
            continue
        first = reqs[0]
        first_in = first.usage.total_input
        input_only = Usage(input=first.usage.input, cache_write=first.usage.cache_write,
                           cache_read=first.usage.cache_read, cache_write_1h=first.usage.cache_write_1h)
        first_cost = usage_cost(input_only, first.model, first.speed) or 0.0
        carried = first_cost + (len(reqs) - 1) * first_in * read_price(agent) / 1e6
        total = sum(usage_cost(r.usage, r.model, r.speed) or 0.0 for r in reqs)
        out.append(Startup(agent, first_in, len(reqs), sum(r.usage.total_input for r in reqs), carried, total))
    return out


# ---------------------------------------------------------------- 3. lineage

URL_RE = re.compile(r"https?://[^\s)\]>\"'`|,]+")
PATH_RE = re.compile(r"(?:~|/)[\w.\-/]*\w\.\w{1,6}\b")
NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?(?![\w])")
WORD_RE = re.compile(r"[a-z0-9]+")
# Name-shaped words survive paraphrase: CamelCase (SubagentStop), acronyms (MAST),
# and hyphen/underscore compounds (claude-devtools, parent_agent_id).
NAME_RE = re.compile(
    r"(?<![\w/.-])(?:[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+|[A-Z]{3,}s?)(?![\w/-])"
)
SHINGLE = 6


def signals(text: str) -> Set[str]:
    """Distinctive items: URLs, file paths, names, and numbers unlikely to repeat by chance."""
    items = {u.rstrip(".") for u in URL_RE.findall(text)}
    text = URL_RE.sub(" ", text)  # so pieces of a URL aren't counted again as paths or names
    items |= set(PATH_RE.findall(text))
    items |= set(NAME_RE.findall(text))
    for n in NUM_RE.findall(text):
        digits = re.sub(r"\D", "", n)
        if len(digits) >= 3 and not re.fullmatch(r"(19|20)\d\d", n):
            items.add(n)
    return items


def shingles(text: str) -> Set[Tuple[str, ...]]:
    words = WORD_RE.findall(text.lower())
    return {tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


@dataclass
class Lineage:
    """Where a subagent's work went. Two channels:

    report  does text from its final report (URLs, paths, numbers, exact
            phrases the parent didn't already have) show up in what the parent
            wrote afterwards?
    files   what happened to files it wrote: another agent read them or used
            them in a command (picked up), edited them (revised), or wrote over
            them (replaced)?
    """

    agent: Agent
    status: str
    report_status: str = "no report"  # used / unclear / no trace found / no report / arrived after parent stopped
    items_total: int = 0
    items_reused: int = 0
    phrase_overlap: float = 0.0
    first_use_after: Optional[timedelta] = None
    files_written: int = 0
    files_picked_up: int = 0
    files_revised: int = 0
    files_replaced: int = 0


def _report_channel(agent: Agent, parent: Agent) -> Tuple[str, int, int, float, Optional[timedelta]]:
    if not agent.report_text:
        return "no report", 0, 0, 0.0, None
    at = _ts(agent.report_at)
    before = [t for ts, t in parent.outputs if _ts(ts) < at]
    after = [(ts, t) for ts, t in parent.outputs if _ts(ts) >= at]
    if not after:
        return "arrived after parent stopped", 0, 0, 0.0, None
    known_text = agent.instructions + "\n" + "\n".join(before)
    items = signals(agent.report_text) - signals(known_text)
    after_text = "\n".join(t for _, t in after)
    reused = {x for x in items if x in after_text}
    rep_sh = shingles(agent.report_text) - shingles(agent.instructions)
    overlap = len(rep_sh & shingles(after_text)) / len(rep_sh) if rep_sh else 0.0
    first_use = None
    for ts, t in after:
        if any(x in t for x in reused) or (rep_sh and rep_sh & shingles(t)):
            if ts and agent.report_at:
                first_use = ts - agent.report_at
            break
    if len(reused) >= 2 or overlap >= 0.05:
        status = "used"
    elif reused or overlap >= 0.01:
        # One reused item or a little phrase overlap. A model-judged check (scripts/judge_lineage.py)
        # found this also happens by chance: 2 of 6 reports shown next to a different session's lead got it.
        status = "unclear"
    else:
        status = "no trace found"
    return status, len(items), len(reused), overlap, first_use


def _file_channel(run: Run, agent: Agent) -> Tuple[int, int, int, int]:
    written, picked, revised, replaced = 0, 0, 0, 0
    for w in agent.tool_calls:
        path = w.input.get("file_path")
        if w.name not in WRITE_TOOLS or w.is_error or not isinstance(path, str):
            continue
        written += 1
        base = path.rsplit("/", 1)[-1]
        t = _ts(w.started)
        later = [c for a in run.agents.values() if a.id != agent.id for c in a.tool_calls if _ts(c.started) > t]
        touched = {c.name for c in later if c.input.get("file_path") == path}
        in_command = any(c.name == "Bash" and (path in str(c.input.get("command", "")) or
                                                (len(base) >= 5 and base in str(c.input.get("command", ""))))
                         for c in later)
        picked += bool("Read" in touched or in_command)
        revised += bool(touched & {"Edit", "MultiEdit", "NotebookEdit"})
        replaced += bool("Write" in touched)
    return written, picked, revised, replaced


def lineage(run: Run) -> List[Lineage]:
    out = []
    for agent in run.subagents:
        parent = run.agents.get(agent.parent_id or "main")
        if parent is None:
            continue
        rstatus, n_items, n_reused, overlap, first_use = _report_channel(agent, parent)
        written, picked, revised, replaced = _file_channel(run, agent)
        if rstatus == "used" or picked or revised:
            status = "used"
        elif written and replaced == written:
            status = "replaced by another agent"
        elif rstatus == "unclear":
            status = "unclear"
        elif written:
            status = "file left as written"
        elif rstatus in ("no report", "arrived after parent stopped"):
            status = rstatus
        else:
            status = "no trace found"
        out.append(Lineage(agent, status, rstatus, n_items, n_reused, overlap, first_use,
                           written, picked, revised, replaced))
    return out


# ---------------------------------------------------------------- 4. file conflicts

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


@dataclass
class FileConflict:
    path: str
    kind: str  # "several writers" or "stale write"
    agents: List[str]


def file_conflicts(run: Run) -> List[FileConflict]:
    writes: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
    reads: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
    for c in run.tool_calls():
        path = c.input.get("file_path") or c.input.get("notebook_path")
        if not isinstance(path, str):
            continue
        if c.name in WRITE_TOOLS and not c.is_error:
            writes[path].append((_ts(c.started), c.agent_id))
        elif c.name == "Read":
            reads[path].append((_ts(c.started), c.agent_id))
    out = []
    for path, ws in writes.items():
        writers = sorted({a for _, a in ws})
        if len(writers) < 2:
            continue
        out.append(FileConflict(path, "several writers", writers))
        ws = sorted(ws)
        for i, (t_other, other) in enumerate(ws):
            for t_mine, me in ws[i + 1:]:
                if me == other:
                    continue
                my_reads = [t for t, a in reads[path] if a == me]
                read_before = any(t < t_other for t in my_reads)
                reread = any(t_other <= t <= t_mine for t in my_reads)
                if read_before and not reread:
                    out.append(FileConflict(path, "stale write", [other, me]))
    return out


# ---------------------------------------------------------------- 5. approvals

RISKY_RE = re.compile(
    r"\brm\s+-\w*[rf]|\bgit\s+(push|reset|clean|checkout\s+--)|\bsudo\b|\bcurl\b|\bwget\b|"
    r"\bnpm\s+publish\b|\bpip3?\s+install\b|\bchmod\b|\bdd\s+if=|\bgh\s+(repo|pr|release)\s+(create|delete|merge)"
)


# Tools whose "permission decision" is really a person answering the agent, not allowing an action.
QUESTION_TOOLS = {"AskUserQuestion"}


@dataclass
class ApprovalReport:
    total: int = 0
    by_source: Counter = field(default_factory=Counter)
    rejected: int = 0
    human: List[Tuple[ToolCall, Optional[float]]] = field(default_factory=list)  # (call, seconds call→result)
    answers: List[ToolCall] = field(default_factory=list)  # a person answered a question the agent asked
    settings_median: Dict[str, float] = field(default_factory=dict)  # tool → median seconds when settings allowed it

    def quick_risky(self, under_seconds: float = 3.0) -> List[ToolCall]:
        """Risky commands a person allowed faster than under_seconds after the call (execution time included)."""
        return [c for c, s in self.human if s is not None and s < under_seconds and c.approval
                and c.approval.decision == "accept" and RISKY_RE.search(str(c.input.get("command", "")))]


def approvals(run: Run, only: Optional[Set[str]] = None) -> ApprovalReport:
    """Permission decisions in a run. `only`: count just the calls made in these model requests
    (used to skip history a fork or resume copied from another file)."""
    rep = ApprovalReport()
    settings_waits: Dict[str, List[float]] = defaultdict(list)
    for c in run.tool_calls():
        a = c.approval
        if a is None or (only is not None and c.request_id not in only):
            continue
        rep.total += 1
        rep.by_source[a.source] += 1
        rep.rejected += a.decision != "accept"
        secs = (c.finished - c.started).total_seconds() if c.finished and c.started else None
        if a.by_human and c.name in QUESTION_TOOLS:
            rep.answers.append(c)
        elif a.by_human:
            rep.human.append((c, secs))
        elif secs is not None:
            settings_waits[c.name].append(secs)
    rep.settings_median = {k: statistics.median(v) for k, v in settings_waits.items()}
    return rep


# ---------------------------------------------------------------- 6. parallelism

def peak_parallel(run: Run) -> int:
    """Most subagents running at the same moment."""
    edges = []
    for a in run.subagents:
        start, end = a.started, a.report_at or a.ended
        if start and end and end >= start:
            edges += [(_ts(start), 1), (_ts(end), -1)]
    peak = cur = 0
    for _, d in sorted(edges, key=lambda e: (e[0], e[1])):
        cur += d
        peak = max(peak, cur)
    return peak


# ---------------------------------------------------------------- everything for one run

def summarize(run: Run, scale: TokenScale, only_owned: bool = False) -> dict:
    """All findings for one run. only_owned: leave out history copied from an older file,
    so that summing over many runs counts each call once."""
    owned_ids = {r.id for r in run.requests() if r.owned or not only_owned}
    reqs = [r for r in run.requests() if r.id in owned_ids]
    costs = [request_cost(r) for r in reqs]
    by_agent_cost: Counter = Counter()
    for r, c in zip(reqs, costs):
        by_agent_cost["lead" if r.agent_id == "main" else "sub"] += c or 0.0
    return {
        "run": run,
        "cost": sum(c or 0.0 for c in costs),
        "cost_unknown": sum(1 for c in costs if c is None),
        "lead_cost": by_agent_cost["lead"],
        "sub_cost": by_agent_cost["sub"],
        "startup": [s for s in startup_costs(run)
                    if not only_owned or all(r.id in owned_ids for r in s.agent.requests)],
        "repeats": [x for x in find_repeats(run, scale) if x.call.request_id in owned_ids],
        "lineage": lineage(run),
        "conflicts": file_conflicts(run),
        "approvals": approvals(run, owned_ids if only_owned else None),
        "peak": peak_parallel(run),
    }
