"""Adapter for Claude Code transcripts.

This is the only file that knows Claude Code's on-disk format. Claude Code's
docs say the format is internal and can change on any release, so every
assumption is listed here and checked by the tests.

Assumptions (checked against Claude Code 2.1.168 to 2.1.330, Aug-Nov 2026):

Layout
  <root>/<project>/<session-id>.jsonl                          the lead agent
  <root>/<project>/<session-id>/subagents/agent-<id>.jsonl     one subagent
  <root>/<project>/<session-id>/subagents/agent-<id>.meta.json its type, description,
      the id of the tool call that started it (toolUseId), and spawnDepth

Model calls
  One model call is written as several "assistant" lines, one per content
  block (thinking, text, tool use), sharing one requestId. Each line repeats
  the call's token usage; only output_tokens can grow from line to line. So we
  group by requestId and take the field-wise maximum.
  Lines with model "<synthetic>" are placeholders Claude Code writes itself.
  No model call happened, so we skip them.

Tool calls and approvals
  A tool call is a "tool_use" block in an assistant line. Its result is a
  "tool_result" block in a later "user" line. That user line may carry
  permissionDecision = {decision, source}; source "config" means settings or
  the permission mode allowed it, "user_*" means a person clicked.

Subagents
  The parent starts a subagent with an Agent (formerly Task) tool call.
  Its result carries toolUseResult.agentId. Synchronous subagents
  (status "completed") return their report in that result. Background ones
  (status "async_launched") deliver it later in a line whose origin has
  kind "peer" and from = the subagent's id.

Messages from the person
  User lines with origin.kind "human". Older versions have no origin, so a
  plain user line that is not a tool result counts as a human message.

Other records
  cost-state      Claude Code's own running token and cost totals for the session
  derived-from    which session this one was forked, resumed or cleared from
  system/compact_boundary   a compaction (the conversation was summarized)
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .model import Agent, Approval, Event, Request, Run, ToolCall, Usage

# Tool-input fields that carry text the agent wrote (used to trace where reports went).
WRITTEN_FIELDS = ("content", "new_string", "command", "prompt", "query", "url", "description")

SPAWN_TOOLS = ("Agent", "Task")
SYNTHETIC_MODEL = "<synthetic>"

# Record types we read, or have looked at and deliberately ignore.
# Anything else is counted in Run.unknown_record_types so format drift shows up.
KNOWN_TYPES = {
    "assistant", "user", "attachment", "system", "cost-state", "derived-from",
    "custom-title", "ai-title", "summary", "queue-operation", "last-prompt",
    "atis-latch", "agent-name", "file-history-snapshot", "file-history-delta",
    "api-request", "api-request-blob", "api-request-shape", "data-flow-records",
    "data-flow-incomplete", "mode", "permission-mode", "artifact-autoreact-ledger",
    "frame-link", "relocated", "bridge-session", "tools-used",
    "artifact-comment-monitor", "pr-link",
}


# ---------------------------------------------------------------- files

def default_root() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def find_sessions(root: Optional[str] = None) -> List[str]:
    """Every lead-agent transcript under root."""
    root = root or default_root()
    out = []
    for project in sorted(os.listdir(root)):
        pdir = os.path.join(root, project)
        if not os.path.isdir(pdir):
            continue
        for name in sorted(os.listdir(pdir)):
            if name.endswith(".jsonl"):
                out.append(os.path.join(pdir, name))
    return out


def subagent_files(session_path: str) -> List[str]:
    sub = os.path.join(session_path[: -len(".jsonl")], "subagents")
    if not os.path.isdir(sub):
        return []
    return [
        os.path.join(sub, n)
        for n in sorted(os.listdir(sub))
        if n.startswith("agent-") and n.endswith(".jsonl")
    ]


def session_id_of(path: str) -> str:
    return os.path.basename(path)[: -len(".jsonl")]


def read_jsonl(path: str) -> Tuple[List[dict], int]:
    """All records in a JSONL file, plus how many lines could not be read."""
    records, bad = [], 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(obj, dict):
                records.append(obj)
            else:
                bad += 1
    return records, bad


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------- small helpers

def parse_ts(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def usage_of(message: dict) -> Usage:
    u = message.get("usage") or {}
    server = u.get("server_tool_use") or {}
    details = u.get("output_tokens_details") or {}
    creation = u.get("cache_creation") or {}
    return Usage(
        input=u.get("input_tokens") or 0,
        cache_write=u.get("cache_creation_input_tokens") or 0,
        cache_read=u.get("cache_read_input_tokens") or 0,
        output=u.get("output_tokens") or 0,
        thinking=details.get("thinking_tokens") or 0,
        web_searches=server.get("web_search_requests") or 0,
        web_fetches=server.get("web_fetch_requests") or 0,
        cache_write_1h=creation.get("ephemeral_1h_input_tokens") or 0,
    )


def text_of(content) -> str:
    """The readable text in a message or tool-result content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text") or "")
                elif "content" in b:
                    parts.append(text_of(b.get("content")))
        return "\n".join(p for p in parts if p)
    return ""


def text_len(content) -> int:
    """Characters of readable text in a message or tool-result content."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    n += len(b.get("text") or "")
                elif "content" in b:
                    n += text_len(b.get("content"))
        return n
    return 0


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]


class _Links:
    """Facts gathered while reading, joined up after every file is read."""

    def __init__(self) -> None:
        self.spawn_results: Dict[str, Tuple[ToolCall, dict, Optional[datetime], str]] = {}
        self.deliveries: List[Tuple[dict, Optional[datetime]]] = []


# ---------------------------------------------------------------- reading one agent

def _read_agent(records: List[dict], agent: Agent, run: Run, links: _Links) -> None:
    is_lead = agent.id == "main"
    groups: Dict[str, List[Tuple[dict, Optional[datetime]]]] = {}
    order: List[str] = []
    calls: Dict[str, ToolCall] = {}

    for r in records:
        rtype = r.get("type")
        ts = parse_ts(r.get("timestamp"))
        if rtype not in KNOWN_TYPES:
            run.unknown_record_types[str(rtype)] += 1
        if r.get("version"):
            run.versions.add(r["version"])

        if rtype == "assistant":
            msg = r.get("message") or {}
            if msg.get("model") == SYNTHETIC_MODEL:
                continue
            agent.seen(ts)
            rid = r.get("requestId") or msg.get("id") or r.get("uuid") or f"line-{len(order)}"
            if rid not in groups:
                groups[rid] = []
                order.append(rid)
                agent.events.append(Event("call", ts, rid))
            groups[rid].append((r, ts))
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text"):
                    agent.outputs.append((ts, b["text"]))
                if b.get("type") == "tool_use" and b.get("id") not in calls:
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    calls[b["id"]] = ToolCall(
                        id=b["id"],
                        agent_id=agent.id,
                        request_id=rid,
                        name=b.get("name") or "?",
                        input=inp,
                        started=ts,
                    )
                    written = "\n".join(str(inp[k]) for k in WRITTEN_FIELDS if isinstance(inp.get(k), str))
                    if written:
                        agent.outputs.append((ts, written))

        elif rtype == "user":
            agent.seen(ts)
            _read_user_line(r, ts, agent, run, calls, links)

        elif rtype == "attachment":
            origin = (r.get("attachment") or {}).get("origin")
            size = -1
            if isinstance(origin, dict) and is_lead:
                if origin.get("kind") == "peer":
                    links.deliveries.append((origin, ts))
                    size = len(str(origin.get("handbackReportText") or origin.get("body") or ""))
                elif origin.get("kind") == "human":
                    run.human_messages.append(ts)
            agent.events.append(Event("context", ts, None, size))

        elif rtype == "system" and is_lead and r.get("subtype") == "compact_boundary":
            run.compactions.append(ts)
        elif rtype == "cost-state" and is_lead:
            run.reported_totals = r
        elif rtype == "derived-from" and is_lead:
            run.derived_from.extend(s for s in (r.get("sessions") or []) if isinstance(s, dict))
        elif rtype == "custom-title" and is_lead and r.get("customTitle"):
            run.title = r["customTitle"]

    for rid in order:
        lines = groups[rid]
        usage = Usage()
        tool_ids: List[str] = []
        for r, _ in lines:
            msg = r.get("message") or {}
            usage = usage.max_with(usage_of(msg))
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                    tool_ids.append(b["id"])
        last = lines[-1][0].get("message") or {}
        times = [t for _, t in lines if t]
        agent.requests.append(
            Request(
                id=rid,
                agent_id=agent.id,
                model=last.get("model") or "?",
                started=min(times) if times else None,
                ended=max(times) if times else None,
                usage=usage,
                stop_reason=last.get("stop_reason"),
                tool_use_ids=tool_ids,
                speed=(last.get("usage") or {}).get("speed"),
            )
        )
    agent.tool_calls = list(calls.values())


def _read_user_line(r: dict, ts, agent: Agent, run: Run, calls: Dict[str, ToolCall], links: _Links) -> None:
    msg = r.get("message") or {}
    content = msg.get("content")
    results = []
    if isinstance(content, list):
        results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]

    if results:
        decision = r.get("permissionDecision")
        result_meta = r.get("toolUseResult")
        for b in results:
            call = calls.get(b.get("tool_use_id"))
            body = text_of(b.get("content"))
            agent.events.append(Event("result", ts, b.get("tool_use_id"), len(body)))
            if call is None:
                continue  # a result whose call we never saw, e.g. history copied without it
            call.finished = ts
            call.is_error = bool(b.get("is_error"))
            call.result_chars = len(body)
            raw = b.get("content")
            call.result_media = isinstance(raw, list) and any(
                isinstance(x, dict) and x.get("type") in ("image", "document") for x in raw)
            if call.result_media:
                call.result_hash = fingerprint(json.dumps(raw, sort_keys=True, default=str))
            elif body:
                call.result_hash = fingerprint(body)
            else:
                call.result_hash = None  # empty result: nothing to compare
            if isinstance(decision, dict) and decision.get("decision"):
                call.approval = Approval(
                    tool_use_id=call.id,
                    decision=str(decision.get("decision")),
                    source=str(decision.get("source") or "unknown"),
                    reason=decision.get("reasonType") or decision.get("reasonCode"),
                    at=ts,
                )
            if call.name in SPAWN_TOOLS and isinstance(result_meta, dict) and result_meta.get("agentId"):
                links.spawn_results[str(result_meta["agentId"])] = (call, result_meta, ts, body)
        return

    agent.events.append(Event("context", ts, None, text_len(content)))
    if agent.id != "main" or r.get("isMeta"):
        return
    origin = r.get("origin")
    if isinstance(origin, dict):
        if origin.get("kind") == "human":
            run.human_messages.append(ts)
        elif origin.get("kind") == "peer":
            links.deliveries.append((origin, ts))
    elif isinstance(content, (str, list)):
        run.human_messages.append(ts)  # older versions: no origin field


# ---------------------------------------------------------------- reading a whole run

def parse_session(path: str) -> Run:
    """Read a lead transcript and all its subagents into one Run."""
    run = Run(session_id=session_id_of(path), path=path, project=os.path.basename(os.path.dirname(path)))
    links = _Links()

    lead = Agent(id="main", parent_id=None, kind="lead", transcript=path)
    run.agents["main"] = lead
    records, bad = read_jsonl(path)
    run.bad_lines += bad
    _read_agent(records, lead, run, links)

    for sub_path in subagent_files(path):
        agent_id = os.path.basename(sub_path)[len("agent-"): -len(".jsonl")]
        meta = _read_json(sub_path[: -len(".jsonl")] + ".meta.json")
        agent = Agent(
            id=agent_id,
            parent_id=None,
            kind=str(meta.get("agentType") or "unknown"),
            description=str(meta.get("description") or ""),
            model_requested=meta.get("model"),
            spawn_tool_use_id=meta.get("toolUseId"),
            transcript=sub_path,
        )
        run.agents[agent_id] = agent
        records, bad = read_jsonl(sub_path)
        run.bad_lines += bad
        _read_agent(records, agent, run, links)

    _link(run, links)
    return run


def _link(run: Run, links: _Links) -> None:
    owner = {c.id: a.id for a in run.agents.values() for c in a.tool_calls}
    by_id = {c.id: c for a in run.agents.values() for c in a.tool_calls}

    # 1. Subagents that left a transcript: find the call that started them.
    for agent in run.subagents:
        call = by_id.get(agent.spawn_tool_use_id or "")
        if call is None:
            agent.parent_id = "main"
            run.notes.append(f"subagent {agent.id}: the call that started it is not in this run; shown under the lead")
            continue
        agent.parent_id = owner[call.id]
        call.spawned_agent_id = agent.id
        agent.instructions = str(call.input.get("prompt") or "")
        agent.description = agent.description or str(call.input.get("description") or "")
        if "run_in_background" in call.input:
            agent.background = bool(call.input.get("run_in_background"))

    # 2. Spawn results: background vs synchronous, reports, and subagents with no transcript.
    for agent_id, (call, meta, ts, body) in links.spawn_results.items():
        agent = run.agents.get(agent_id)
        if agent is None:
            agent = Agent(
                id=agent_id,
                parent_id=call.agent_id,
                kind=str(meta.get("agentType") or call.input.get("subagent_type") or "unknown"),
                description=str(call.input.get("description") or meta.get("description") or ""),
                instructions=str(call.input.get("prompt") or meta.get("prompt") or ""),
                spawn_tool_use_id=call.id,
                transcript=None,
            )
            agent.seen(call.started)
            run.agents[agent_id] = agent
            run.missing_transcripts.append(agent_id)
        call.spawned_agent_id = agent_id
        status = meta.get("status")
        if status == "async_launched" or meta.get("isAsync"):
            agent.background = True
        elif status == "completed":
            if agent.background is None:
                agent.background = False
            if agent.report_at is None:
                agent.report_at = ts
                agent.report_chars = len(body)
                agent.report_text = body

    # 3. Background reports delivered later as "peer" messages.
    for origin, ts in links.deliveries:
        sender = str(origin.get("from") or "")
        for agent in run.subagents:
            if sender and (sender == agent.id or agent.id in sender):
                if agent.report_at is None or (ts is not None and ts < agent.report_at):
                    agent.report_text = str(origin.get("handbackReportText") or origin.get("body") or "")
                    agent.report_at = ts
                    agent.report_chars = len(agent.report_text)
                break
