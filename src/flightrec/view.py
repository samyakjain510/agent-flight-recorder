"""Turn a run into one self-contained HTML page.

The page is a template (viewer.html, next to this file) plus the run's data as
JSON. It needs no server and loads nothing from the internet, so it can be
opened from disk and kept or shared as a single file.

Two ideas shape the data:

The clock. Real runs are mostly idle: one of the author's runs spans 12 days
but was active for about 5 hours. Every gap longer than `gap` seconds between
consecutive moments in the run is squeezed to a short break of fixed width.
All times in the page are "display seconds" on this squeezed clock; the real
time of each moment is kept for labels.

Redaction. With redact=True every piece of text that came from the transcript
(titles, instructions, reports, commands, paths, URLs, what agents said) is
replaced by its length. Tool names, agent types, numbers and times stay.
"""

from __future__ import annotations

import bisect
import html
import json
import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from . import analyze as az
from . import fmt
from .model import Agent, Run, ToolCall
from .pricing import request_cost

PAYLOAD_VERSION = 1
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer.html")
SAID_LIMIT = 600     # characters kept from each message an agent wrote, in the replay panel
TARGET_LIMIT = 160   # characters kept from a tool's target (path, command, URL)
TEXT_LIMIT = 20_000  # characters kept from instructions and reports


def _t(ts: Optional[datetime]) -> Optional[float]:
    return ts.timestamp() if ts else None


class Clock:
    """Maps real time to display seconds, squeezing idle gaps.

    times: every moment the page will draw. Gaps between consecutive moments
    longer than `gap` become breaks of `break_len` display seconds. By default
    all breaks together take about a tenth of the active time, each between
    0.4% and 2% of it (and between 5 and 20 s for very short runs), so they
    stay visible without crowding out the work.
    """

    def __init__(self, times: Iterable[float], gap: float = 600.0, break_len: Optional[float] = None):
        self.real: List[float] = sorted(set(times))
        self.gap = gap
        gaps = [b - a for a, b in zip(self.real, self.real[1:])]
        active = sum(g for g in gaps if g <= gap)
        n_breaks = sum(1 for g in gaps if g > gap)
        if break_len is None:
            hi, lo = max(20.0, active * 0.02), max(5.0, active * 0.004)
            break_len = min(hi, max(lo, active * 0.10 / max(n_breaks, 1)))
        self.break_len = break_len
        self.disp: List[float] = []
        self.breaks: List[dict] = []  # {"d": display start, "len": display width, "real": seconds squeezed out}
        d = 0.0
        for i, t in enumerate(self.real):
            if i:
                g = t - self.real[i - 1]
                if g > gap:
                    self.breaks.append({"d": round(d, 3), "len": round(self.break_len, 3), "real": round(g)})
                    d += self.break_len
                else:
                    d += g
            self.disp.append(d)
        self.active = active
        self.total = d

    def __call__(self, t: Optional[float]) -> Optional[float]:
        if t is None or not self.real:
            return None
        i = bisect.bisect_left(self.real, t)
        if i < len(self.real) and self.real[i] == t:
            return round(self.disp[i], 3)
        if i == 0:
            return 0.0
        if i == len(self.real):
            return round(self.total, 3)
        a, b = self.real[i - 1], self.real[i]
        if b - a > self.gap:  # inside a squeezed gap: place it proportionally inside the break
            return round(self.disp[i - 1] + (t - a) / (b - a) * self.break_len, 3)
        return round(self.disp[i - 1] + (t - a), 3)


def _moments(run: Run) -> List[float]:
    out = []
    for a in run.agents.values():
        out += [_t(x) for x in (a.started, a.ended, a.report_at)]
        for r in a.requests:
            out += [_t(r.started), _t(r.ended)]
        for c in a.tool_calls:
            out += [_t(c.started), _t(c.finished)]
        out += [_t(ts) for ts, _ in a.said]
    out += [_t(x) for x in run.human_messages + run.compactions]
    return [x for x in out if x is not None]


def _text(value: str, redact: bool, limit: int) -> str:
    if not value:
        return ""
    if redact:
        return f"<{len(value)} chars>"
    return value if len(value) <= limit else value[:limit] + f"… <{len(value) - limit} more chars>"


def target_of(call: ToolCall) -> str:
    """What a tool call acted on, in one line: a path, the start of a command, a URL, a query."""
    inp = call.input or {}
    for key in ("file_path", "notebook_path", "url", "query", "pattern", "description", "skill"):
        v = inp.get(key)
        if isinstance(v, str) and v:
            if key == "pattern" and isinstance(inp.get("path"), str):
                return f"{v}  in {inp['path']}"
            return v
    cmd = inp.get("command")
    if isinstance(cmd, str) and cmd:
        return cmd.strip().splitlines()[0] if cmd.strip() else ""
    if inp:
        return json.dumps(inp, sort_keys=True, default=str)
    return ""


def agent_label(agent: Agent, index: int, redact: bool) -> str:
    if agent.id == "main":
        return "lead"
    if redact or not agent.description:
        return f"{agent.kind} #{index}"
    return f"{agent.kind} · {' '.join(agent.description.split())}"


def build(run: Run, scale: az.TokenScale, redact: bool = False, gap: float = 600.0) -> dict:
    """Everything the page draws, as plain JSON-ready data."""
    clock = Clock(_moments(run), gap=gap)
    summary = az.summarize(run, scale)
    order = [a for a, _ in run.tree()]
    depth = {a.id: d for a, d in run.tree()}
    # agents that exist but are not reachable from the lead (should not happen; keep them visible)
    order += [a for a in run.agents.values() if a not in order]
    idx = {a.id: i for i, a in enumerate(order)}

    startup = {s.agent.id: s for s in summary["startup"]}
    lineage = {x.agent.id: x for x in summary["lineage"]}

    agents = []
    for i, a in enumerate(order):
        u = a.usage()
        cost = sum(request_cost(r) or 0.0 for r in a.requests)
        s = startup.get(a.id)
        ln = lineage.get(a.id)
        agents.append({
            "id": a.id,
            "parent": idx.get(a.parent_id) if a.parent_id else None,
            "depth": depth.get(a.id, 1),
            "kind": a.kind,
            "label": agent_label(a, i, redact),
            "model": (a.models() or [a.model_requested or ""])[-1],
            "background": a.background,
            "missing": a.transcript is None and a.id != "main",
            "start": clock(_t(a.started)),
            "end": clock(_t(a.ended)),
            "report": clock(_t(a.report_at)),
            "report_real": _t(a.report_at),
            "instructions": _text(a.instructions, redact, TEXT_LIMIT),
            "report_text": _text(a.report_text, redact, TEXT_LIMIT),
            "report_chars": a.report_chars or len(a.report_text or ""),
            "cost": round(cost, 4),
            "tokens": {"in": u.input, "cache_write": u.cache_write, "cache_read": u.cache_read, "out": u.output},
            "calls": len(a.requests),
            "tools": len(a.tool_calls),
            "base_context": s.first_input if s else None,
            "base_share": round(s.cost_share, 3) if s and a.id != "main" else None,
            "lineage": None if ln is None else {
                "status": ln.status, "report": ln.report_status,
                "items": ln.items_total, "reused": ln.items_reused, "overlap": round(ln.phrase_overlap, 3),
                "files_written": ln.files_written, "picked_up": ln.files_picked_up,
                "revised": ln.files_revised, "replaced": ln.files_replaced,
            },
        })

    models = sorted({r.model for r in run.requests()})
    calls = []
    for a in order:
        for r in a.requests:
            c = request_cost(r)
            calls.append({
                "a": idx[a.id], "s": clock(_t(r.started)), "e": clock(_t(r.ended or r.started)),
                "real": _t(r.started), "in": r.usage.total_input, "fresh": r.usage.input,
                "cw": r.usage.cache_write, "cr": r.usage.cache_read, "out": r.usage.output,
                "cost": None if c is None else round(c, 5), "m": models.index(r.model),
                "copied": not r.owned,
            })
    calls.sort(key=lambda x: (x["s"] is None, x["s"] or 0))

    tools = []
    tool_index: Dict[str, int] = {}
    for a in order:
        for c in a.tool_calls:
            ap = c.approval
            tool_index[c.id] = len(tools)
            tools.append({
                "a": idx[a.id], "s": clock(_t(c.started)), "e": clock(_t(c.finished or c.started)),
                "real": _t(c.started), "name": c.name,
                "target": _text(target_of(c), redact, TARGET_LIMIT),
                "chars": c.result_chars, "media": c.result_media, "error": c.is_error,
                "approval": None if ap is None else {"decision": ap.decision, "source": ap.source,
                                                    "by_you": ap.by_human},
                "spawned": idx.get(c.spawned_agent_id) if c.spawned_agent_id else None,
            })

    said = []
    for a in order:
        for ts, text in a.said:
            said.append({"a": idx[a.id], "d": clock(_t(ts)), "real": _t(ts),
                         "text": _text(text.strip(), redact, SAID_LIMIT)})
    said.sort(key=lambda x: x["d"] or 0)

    findings = _findings(run, summary, clock, idx, tool_index, redact)

    started, ended = run.started, run.ended
    return {
        "version": PAYLOAD_VERSION,
        "session": run.session_id,
        "title": _text(run.title or "", redact, 200),
        "project": _text(run.project or "", redact, 200),
        "redacted": redact,
        "started": _t(started), "ended": _t(ended),
        "wall": round((ended - started).total_seconds()) if started and ended else 0,
        "active": round(clock.active), "display": round(clock.total, 3),
        "gap": gap, "breaks": clock.breaks,
        "totals": {
            "cost": round(summary["cost"], 4), "cost_unknown": summary["cost_unknown"],
            "lead_cost": round(summary["lead_cost"], 4), "sub_cost": round(summary["sub_cost"], 4),
            "tokens": run.usage().total, "calls": len(calls), "tools": len(tools),
            "copied": sum(1 for c in calls if c["copied"]), "peak": summary["peak"],
        },
        "models": models,
        "agents": agents, "calls": calls, "tools": tools, "said": said,
        "humans": [clock(_t(t)) for t in run.human_messages],
        "compactions": [clock(_t(t)) for t in run.compactions],
        "findings": findings,
        "versions": sorted(run.versions),
    }


def _findings(run: Run, summary: dict, clock: Clock, idx: Dict[str, int],
              tool_index: Dict[str, int], redact: bool) -> List[dict]:
    out = []
    for s in summary["startup"]:
        if s.agent.id == "main" or not s.agent.requests:
            continue
        first = s.agent.requests[0]
        out.append({
            "kind": "startup", "a": idx[s.agent.id], "d": clock(_t(first.started)),
            "title": f"Started with {fmt.tokens(s.first_input)} tokens of base context",
            "detail": f"About {s.cost_share * 100:.0f}% of this agent's cost went to carrying the context it began with"
                      f" (system prompt, tools, memory, its instructions) across {s.calls} model calls.",
        })
    for x in summary["repeats"]:
        where = "same agent" if x.within_agent else f"{agent_label(run.agents[x.first.agent_id], idx[x.first.agent_id], redact)}"
        out.append({
            "kind": "repeat", "a": idx[x.call.agent_id], "d": clock(_t(x.call.started)),
            "tool": tool_index.get(x.call.id), "first_tool": tool_index.get(x.first.id),
            "title": f"{x.call.name} repeated an identical earlier result",
            "detail": f"{_text(target_of(x.call), redact, TARGET_LIMIT)} — first done by {where}."
                      f" About {fmt.tokens(x.tokens)} tokens added again; up to ${x.cost:.3f} carried.",
        })
    for ln in summary["lineage"]:
        a = ln.agent
        at = a.report_at or a.ended
        parts = []
        if ln.report_status not in ("no report",):
            parts.append(f"report: {ln.report_status} ({ln.items_reused} of {ln.items_total} distinctive items"
                         f" reappeared, {ln.phrase_overlap * 100:.0f}% of phrases)")
        if ln.files_written:
            parts.append(f"files: {ln.files_written} written, {ln.files_picked_up} picked up, "
                         f"{ln.files_revised} revised, {ln.files_replaced} replaced")
        out.append({
            "kind": "lineage", "status": ln.status, "a": idx[a.id], "d": clock(_t(at)),
            "title": f"Work {ln.status}" if ln.status in ("used", "unclear") else ln.status.capitalize(),
            "detail": "; ".join(parts) or "No text report reached the parent.",
        })
    for c in summary["conflicts"]:
        writes = [t for t in run.tool_calls() if (t.input or {}).get("file_path") == c.path
                  and t.name in az.WRITE_TOOLS and not t.is_error]
        last = max((t.started for t in writes if t.started), default=None)
        out.append({
            "kind": "conflict", "a": idx.get(c.agents[-1], 0), "d": clock(_t(last)),
            "title": "Stale write" if c.kind == "stale write" else "File written by several agents",
            "detail": f"{_text(c.path, redact, TARGET_LIMIT)}: "
                      + ", ".join(agent_label(run.agents[x], idx[x], redact) for x in c.agents if x in run.agents),
        })
    ap_rep = summary["approvals"]
    for call, secs in ap_rep.human + [(c, None) for c in ap_rep.answers]:
        ap = call.approval
        verb = "approved" if ap.decision == "accept" else "rejected"
        # Answering a question the agent asked is recorded as a permission decision too.
        title = ("You answered a question the agent asked" if call.name in az.QUESTION_TOOLS
                 else f"You {verb} {call.name}")
        out.append({
            "kind": "approval", "a": idx[call.agent_id], "d": clock(_t(call.started)),
            "tool": tool_index.get(call.id),
            "title": title,
            "detail": f"{_text(target_of(call), redact, TARGET_LIMIT)}"
                      + (f" — {secs:.0f}s from the call to its result" if secs is not None else ""),
        })
    out.sort(key=lambda f: (f["d"] is None, f["d"] or 0))
    return out


def render(payload: dict) -> str:
    """The page: the template with the payload dropped in."""
    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # Inside <script>, a literal "</" could end the script early; "<!--" can confuse old parsers.
    data = data.replace("</", "<\\/").replace("<!--", "<\\!--").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    title = html.escape(f"flightrec · {payload['session'][:8]}")
    return page.replace("__TITLE__", title).replace("/*__DATA__*/null", data)


def index_page(rows: List[dict]) -> str:
    """A plain list of runs linking to their pages. rows: payload summaries."""
    head = ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>flightrec runs</title><style>"
            ":root{color-scheme:light dark;--fg:#1f1f1d;--mut:#6b6a65;--line:#e4e2da;--bg:#faf9f5;--card:#fff;--acc:#185FA5}"
            "@media (prefers-color-scheme:dark){:root{--fg:#ecebe6;--mut:#a3a29c;--line:#3a3a37;--bg:#1d1d1b;--card:#262624;--acc:#85B7EB}}"
            "body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}"
            "main{max-width:1100px;margin:0 auto}h1{font-size:20px;font-weight:500;margin:0 0 4px}p{color:var(--mut);margin:0 0 16px}"
            ".wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:8px}"
            "th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}"
            "th{font-weight:500;color:var(--mut);font-size:12px}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}"
            "a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}</style>")
    body = ["<main><h1>flightrec runs</h1><p>Multi-agent Claude Code sessions on this machine. Costs are API-equivalent dollars."
            " Each page is a single file that works offline.</p><div class=wrap><table><tr><th>Session</th><th>Title</th>"
            "<th>Started</th><th class=n>Agents</th><th class=n>Cost</th><th class=n>Active</th><th class=n>Wall</th>"
            "<th>Subagent work</th></tr>"]
    for r in rows:
        started = datetime.fromtimestamp(r["started"]).strftime("%Y-%m-%d %H:%M") if r.get("started") else "?"
        body.append(
            f"<tr><td><a href='{html.escape(r['file'])}'>{html.escape(r['session'][:8])}</a></td>"
            f"<td>{html.escape(r['title'][:70])}</td><td>{started}</td><td class=n>{r['agents']}</td>"
            f"<td class=n>${r['cost']:.2f}</td><td class=n>{html.escape(r['active'])}</td>"
            f"<td class=n>{html.escape(r['wall'])}</td><td>{html.escape(r['lineage'])}</td></tr>")
    body.append("</table></div></main>")
    return head + "".join(body)


def index_row(payload: dict, file: str) -> dict:
    lin = Counter(a["lineage"]["status"] for a in payload["agents"] if a["lineage"])
    return {
        "file": file, "session": payload["session"], "title": payload["title"], "started": payload["started"],
        "agents": len(payload["agents"]), "cost": payload["totals"]["cost"],
        "active": fmt.span(timedelta(seconds=payload["active"])), "wall": fmt.span(timedelta(seconds=payload["wall"])),
        "lineage": ", ".join(f"{k} {v}" for k, v in lin.most_common()),
    }
