"""The run model: a clean description of one multi-agent run.

Nothing here knows about Claude Code's file format. The adapter in
claude_code.py fills these objects in, and everything else only reads them.
That way a format change in Claude Code means fixing one file.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Set, Tuple


@dataclass
class Usage:
    """Token counts for one or more model calls.

    input        fresh input, billed at full price
    cache_write  input stored in the prompt cache on this call (billed a bit above full price)
    cache_read   input served from the prompt cache (billed at a small fraction)
    output       tokens the model wrote, thinking included
    thinking     the thinking part of output, when Claude Code reports it
    """

    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0
    thinking: int = 0
    web_searches: int = 0
    web_fetches: int = 0
    cache_write_1h: int = 0  # the part of cache_write kept for 1 hour (priced higher than 5-minute writes)

    def add(self, other: "Usage") -> "Usage":
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))
        return self

    def max_with(self, other: "Usage") -> "Usage":
        """Field-wise maximum. Used when one call is written as several lines."""
        return Usage(**{f.name: max(getattr(self, f.name), getattr(other, f.name)) for f in fields(self)})

    @property
    def total_input(self) -> int:
        return self.input + self.cache_write + self.cache_read

    @property
    def total(self) -> int:
        return self.total_input + self.output

    def core(self) -> Tuple[int, int, int, int]:
        """The four counts Claude Code also keeps in its own totals."""
        return (self.input, self.cache_write, self.cache_read, self.output)


@dataclass
class Approval:
    """A permission decision about one tool call."""

    tool_use_id: str
    decision: str  # "accept" or "reject"
    source: str  # "config" = allowed by settings or mode; "user_*" = a person clicked
    reason: Optional[str] = None
    at: Optional[datetime] = None

    @property
    def by_human(self) -> bool:
        return self.source.startswith("user")


@dataclass
class ToolCall:
    """One tool call an agent made (read a file, run a command, start a subagent...)."""

    id: str
    agent_id: str
    request_id: Optional[str]
    name: str
    input: dict
    started: Optional[datetime] = None
    finished: Optional[datetime] = None
    is_error: bool = False
    result_chars: int = 0
    result_hash: Optional[str] = None  # fingerprint of the whole result: equal hashes = identical results
    result_media: bool = False  # the result holds images or documents, whose token size we can't measure
    approval: Optional[Approval] = None
    spawned_agent_id: Optional[str] = None


@dataclass
class Request:
    """One call to the model, however many lines it took in the transcript."""

    id: str
    agent_id: str
    model: str
    started: Optional[datetime]
    ended: Optional[datetime]
    usage: Usage
    stop_reason: Optional[str] = None
    tool_use_ids: List[str] = field(default_factory=list)
    shared_with: Set[str] = field(default_factory=set)  # other session files holding the same call
    owned: bool = True  # False when another file holds the original of this (copied) call
    speed: Optional[str] = None  # "fast" when fast mode was used (priced higher)


@dataclass
class Event:
    """One step in an agent's timeline, in file order.

    kind: "call"    a model call (ref = request id)
          "result"  a tool result came back (ref = tool call id, chars = its size)
          "context" anything else added to the conversation: a message, a
                    report from a subagent, a reminder (chars = size, -1 if unknown)
    """

    kind: str
    at: Optional[datetime]
    ref: Optional[str] = None
    chars: int = 0


@dataclass
class Agent:
    """The lead agent ("main") or one subagent."""

    id: str
    parent_id: Optional[str]
    kind: str  # "lead" for the main agent, else the subagent type
    description: str = ""
    instructions: str = ""  # the prompt its parent wrote for it
    model_requested: Optional[str] = None
    spawn_tool_use_id: Optional[str] = None
    background: Optional[bool] = None
    transcript: Optional[str] = None  # None when the subagent left no transcript file
    requests: List[Request] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    started: Optional[datetime] = None
    ended: Optional[datetime] = None
    report_at: Optional[datetime] = None  # when its final report reached the parent
    report_chars: int = 0
    report_text: str = ""  # kept in memory for lineage; never printed unless asked
    outputs: List[Tuple[Optional[datetime], str]] = field(default_factory=list)  # what this agent wrote
    events: List[Event] = field(default_factory=list)

    def usage(self) -> Usage:
        total = Usage()
        for r in self.requests:
            total.add(r.usage)
        return total

    def models(self) -> List[str]:
        return sorted({r.model for r in self.requests})

    def seen(self, ts: Optional[datetime]) -> None:
        if ts is None:
            return
        if self.started is None or ts < self.started:
            self.started = ts
        if self.ended is None or ts > self.ended:
            self.ended = ts


@dataclass
class Run:
    """One session: the lead agent plus every subagent it started."""

    session_id: str
    path: str
    project: str
    title: Optional[str] = None
    agents: Dict[str, Agent] = field(default_factory=dict)
    human_messages: List[datetime] = field(default_factory=list)
    compactions: List[datetime] = field(default_factory=list)
    reported_totals: Optional[dict] = None  # Claude Code's own running totals, as recorded
    derived_from: List[dict] = field(default_factory=list)
    versions: Set[str] = field(default_factory=set)
    unknown_record_types: Counter = field(default_factory=Counter)
    missing_transcripts: List[str] = field(default_factory=list)
    bad_lines: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def lead(self) -> Agent:
        return self.agents["main"]

    @property
    def subagents(self) -> List[Agent]:
        return [a for a in self.agents.values() if a.id != "main"]

    @property
    def started(self) -> Optional[datetime]:
        times = [a.started for a in self.agents.values() if a.started]
        return min(times) if times else None

    @property
    def ended(self) -> Optional[datetime]:
        times = [a.ended for a in self.agents.values() if a.ended]
        return max(times) if times else None

    def children(self, agent_id: str) -> List[Agent]:
        kids = [a for a in self.agents.values() if a.parent_id == agent_id]
        return sorted(kids, key=lambda a: (a.started is None, a.started or datetime.min))

    def tree(self, agent_id: str = "main", depth: int = 0) -> Iterator[Tuple[Agent, int]]:
        """Walk the agent tree, parents before children."""
        yield self.agents[agent_id], depth
        for child in self.children(agent_id):
            yield from self.tree(child.id, depth + 1)

    def branch_usage(self, agent_id: str) -> Usage:
        """Tokens for an agent plus everything below it."""
        total = Usage()
        for agent, _ in self.tree(agent_id):
            total.add(agent.usage())
        return total

    def requests(self) -> List[Request]:
        return [r for a in self.agents.values() for r in a.requests]

    def tool_calls(self) -> List[ToolCall]:
        return [c for a in self.agents.values() for c in a.tool_calls]

    def usage(self) -> Usage:
        total = Usage()
        for r in self.requests():
            total.add(r.usage)
        return total

    def usage_by_model(self) -> Dict[str, Usage]:
        out: Dict[str, Usage] = {}
        for r in self.requests():
            out.setdefault(r.model, Usage()).add(r.usage)
        return out

    def approvals(self) -> List[Approval]:
        return [c.approval for c in self.tool_calls() if c.approval is not None]
