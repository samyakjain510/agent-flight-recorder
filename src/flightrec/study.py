"""The study: aggregate findings across every multi-agent run on this machine.

`flightrec study` prints these numbers; `--json` saves them. Everything is
measured from the transcripts except one section, the delegation estimate,
which is a model of what would have happened if the lead had done a
subagent's work itself. Its assumptions are written next to the code and
printed with the result.

Counting rules (same as `analyze --all`):
- A model call copied into a fork or resume counts once, in the file created first.
- A subagent copied into a fork counts once, in the file created first.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from . import analyze as az
from .check import check_run
from .claude_code import find_sessions, parse_session, subagent_files
from .corpus import Corpus
from .model import Agent, Run
from .pricing import PRICES, request_cost, usage_cost

FANOUT_WINDOW = 120.0  # siblings started within this many seconds of the first form one fan-out


def _t(ts: Optional[datetime]) -> float:
    return ts.timestamp() if ts else 0.0


def _median(xs: List[float]) -> Optional[float]:
    return statistics.median(xs) if xs else None


def _pct(part: float, whole: float) -> Optional[float]:
    return round(100.0 * part / whole, 1) if whole else None


def _end(a: Agent) -> Optional[datetime]:
    return a.report_at or a.ended


@dataclass
class Dataset:
    runs: List[Run]                      # multi-agent runs, oldest file first
    subagents: List[Tuple[Run, Agent]]   # each subagent once, with the run that owns it
    all_light: List[dict]                # one small summary per session (all sessions)
    scale: az.TokenScale
    root: Optional[str] = None


def load(root: Optional[str] = None) -> Dataset:
    corpus = Corpus(root)
    paths = find_sessions(root)
    scale = az.calibrate(parse_session(p) for p in paths)
    runs, light = [], []
    for p in paths:
        run = parse_session(p)
        corpus.mark(run)
        owned = [r for r in run.requests() if r.owned]
        ap = az.approvals(run, {r.id for r in owned})
        chk = check_run(run)
        missing = sum(m.missing().total for m in chk.models) if chk.clean else None
        light.append({
            "session": run.session_id, "multi": bool(subagent_files(p)),
            "started": _t(run.started) or None,
            "cost": sum(request_cost(r) or 0.0 for r in owned), "tokens": sum(r.usage.total for r in owned),
            "approvals": ap.total, "by_settings": ap.by_source.get("config", 0), "by_person": len(ap.human),
            "rejected_by_person": sum(1 for c, _ in ap.human if c.approval and c.approval.decision != "accept"),
            "answers": len(ap.answers), "clean": chk.clean, "verdict": chk.verdict,
            "transcript_tokens": run.usage().total, "missing_tokens": missing,
            "compactions": len(run.compactions), "versions": sorted(run.versions),
            "media_results": sum(1 for c in run.tool_calls() if c.result_media),
        })
        if subagent_files(p):
            runs.append(run)
    runs.sort(key=lambda r: corpus.created.get(r.session_id, (0.0, "", r.session_id)))
    seen, subs = set(), []
    for run in runs:
        for a in run.subagents:
            if a.id not in seen:
                seen.add(a.id)
                subs.append((run, a))
    return Dataset(runs, subs, light, scale, root)


# ---------------------------------------------------------------- sections

def corpus_section(ds: Dataset) -> dict:
    multi = [x for x in ds.all_light if x["multi"]]
    starts = [x["started"] for x in multi if x["started"]]
    versions = {v for x in ds.all_light for v in x["versions"]}
    return {
        "sessions": len(ds.all_light), "multi_agent_sessions": len(multi),
        "subagents": len(ds.subagents),
        "subagents_with_transcript": sum(1 for _, a in ds.subagents if a.transcript),
        "first": min(starts) if starts else None, "last": max(starts) if starts else None,
        "claude_code_versions": len(versions),
        "cost_all": round(sum(x["cost"] for x in ds.all_light), 2),
        "cost_multi": round(sum(x["cost"] for x in multi), 2),
        "tokens_all": sum(x["tokens"] for x in ds.all_light),
        "tokens_multi": sum(x["tokens"] for x in multi),
    }


def spend_section(ds: Dataset) -> dict:
    lead = sub = 0.0
    for run in ds.runs:
        for r in run.requests():
            if r.owned:
                c = request_cost(r) or 0.0
                if r.agent_id == "main":
                    lead += c
                else:
                    sub += c
    by_type: Dict[str, dict] = defaultdict(lambda: {"count": 0, "cost": 0.0, "calls": []})
    costs = []
    for run, a in ds.subagents:
        c = sum(request_cost(r) or 0.0 for r in a.requests if r.owned)
        t = by_type[a.kind]
        t["count"] += 1
        t["cost"] += c
        t["calls"].append(len(a.requests))
        if a.requests:
            costs.append(c)
    models = Counter(az.main_model(a) or "no transcript" for _, a in ds.subagents)
    return {
        "lead_cost": round(lead, 2), "subagent_cost": round(sub, 2), "subagent_share_pct": _pct(sub, lead + sub),
        "per_subagent_cost_median": round(_median(costs) or 0, 3), "per_subagent_cost_max": round(max(costs or [0]), 2),
        "by_type": {k: {"count": v["count"], "cost": round(v["cost"], 2), "median_calls": _median(v["calls"])}
                    for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]["cost"])},
        "subagent_models": dict(models.most_common()),
    }


def startup_section(ds: Dataset) -> dict:
    """Base context: what a subagent carries from its first call (system prompt, tools,
    memory, instructions), times every call it makes. Share = that carried cost / its cost."""
    rows = []
    by_run = {run.session_id: {s.agent.id: s for s in az.startup_costs(run)} for run in ds.runs}
    for run, a in ds.subagents:
        s = by_run[run.session_id].get(a.id)
        if s and s.total_cost:
            rows.append({"calls": s.calls, "base": s.first_input, "share": s.cost_share,
                         "carried": s.carried_cost, "cost": s.total_cost})
    buckets = []
    for lo, hi, label in ((1, 5, "1-5 calls"), (6, 15, "6-15 calls"), (16, 10**9, "16+ calls")):
        g = [r for r in rows if lo <= r["calls"] <= hi]
        if g:
            buckets.append({"size": label, "subagents": len(g),
                            "median_share_pct": round(100 * _median([r["share"] for r in g]), 1),
                            "carried_share_pct": _pct(sum(r["carried"] for r in g), sum(r["cost"] for r in g)),
                            "cost": round(sum(r["cost"] for r in g), 2)})
    return {
        "subagents": len(rows),
        "base_tokens_min": min((r["base"] for r in rows), default=None),
        "base_tokens_median": _median([r["base"] for r in rows]),
        "base_tokens_max": max((r["base"] for r in rows), default=None),
        "carried_share_pct": _pct(sum(r["carried"] for r in rows), sum(r["cost"] for r in rows)),
        "by_size": buckets,
    }


def delegation_section(ds: Dataset) -> dict:
    """Estimate: was handing the job to a subagent cheaper than the lead doing it itself?

    Delegated (actual): the subagent's cost, plus the lead carrying the report for
    the rest of its calls.
    Inline (estimate): the lead makes the same calls itself, at its own prices, on
    top of the context it already had when it started the subagent; the subagent's
    context growth (everything it read and wrote after its first call) then stays
    in the lead's context for the rest of the lead's calls.

    Assumptions: the lead would take the same steps; report size uses the measured
    characters-per-token ratio of tool results, which overstates prose a little and so
    favours the inline side.

    Two bounds, because Claude Code can compact or clear old context:
      keep  the material stays in the lead's context for all its later calls (upper bound
            on what inline costs);
      drop  it is dropped right after the work (the only saving left is doing the work
            in a smaller context, and on a cheaper model).
    """
    rows = []
    for run, a in ds.subagents:
        parent = run.agents.get(a.parent_id or "main")
        reqs = sorted(a.requests, key=lambda r: _t(r.started))
        if not reqs or parent is None or not parent.requests or not a.started:
            continue
        model = az.main_model(parent)
        p = PRICES.get(model or "")
        if not p:
            continue
        w, r_, o = az.write_price(parent) / 1e6, p[3] / 1e6, p[4] / 1e6
        before = [q for q in parent.requests if _t(q.started) <= _t(a.started)]
        if not before:
            continue
        lead_ctx = max(before, key=lambda q: _t(q.started)).usage.total_input
        end = _end(a)
        n_after = sum(1 for q in parent.requests if end and _t(q.started) > _t(end))
        base = reqs[0].usage.total_input
        inline_work = 0.0
        for i, q in enumerate(reqs):
            growth = max(0, q.usage.total_input - base)
            new = 0 if i == 0 else q.usage.input + q.usage.cache_write
            inline_work += (lead_ctx + growth - new) * r_ + new * w + q.usage.output * o
        growth_total = max(0, reqs[-1].usage.total_input + reqs[-1].usage.output - base)
        inline = inline_work + growth_total * r_ * n_after
        sub_cost = sum(request_cost(q) or 0.0 for q in reqs)
        sub_cost_lead_prices = sum(usage_cost(q.usage, model) or 0.0 for q in reqs)
        report_tokens = (a.report_chars or len(a.report_text or "")) / ds.scale.overall
        carry_report = report_tokens * (w + r_ * n_after)
        write_report = report_tokens * w
        rows.append({
            "session": run.session_id[:8], "agent": a.id[:8], "calls": len(reqs), "lead_calls_after": n_after,
            "growth_tokens": growth_total, "lead_context": lead_ctx, "same_model": az.main_model(a) == model,
            "inline": inline, "delegated": sub_cost + carry_report,
            "delegated_at_lead_prices": sub_cost_lead_prices + carry_report,
            "inline_drop": inline_work, "delegated_drop": sub_cost + write_report,
            "delegated_drop_at_lead_prices": sub_cost_lead_prices + write_report,
        })
    cheaper = [x for x in rows if x["delegated"] < x["inline"]]
    cheaper_same = [x for x in rows if x["delegated_at_lead_prices"] < x["inline"]]
    small = [x for x in rows if x["calls"] <= 5]
    big = [x for x in rows if x["calls"] > 5]
    return {
        "subagents": len(rows),
        "delegation_cheaper": len(cheaper),
        "delegation_cheaper_at_lead_prices": len(cheaper_same),
        "inline_total": round(sum(x["inline"] for x in rows), 2),
        "delegated_total": round(sum(x["delegated"] for x in rows), 2),
        "delegated_total_at_lead_prices": round(sum(x["delegated_at_lead_prices"] for x in rows), 2),
        "drop": {"cheaper": sum(1 for x in rows if x["delegated_drop"] < x["inline_drop"]),
                 "cheaper_at_lead_prices": sum(1 for x in rows if x["delegated_drop_at_lead_prices"] < x["inline_drop"]),
                 "inline_total": round(sum(x["inline_drop"] for x in rows), 2),
                 "delegated_total": round(sum(x["delegated_drop"] for x in rows), 2)},
        "small_jobs": {"subagents": len(small), "cheaper": sum(1 for x in small if x["delegated"] < x["inline"]),
                       "median_ratio": _median([x["delegated"] / x["inline"] for x in small if x["inline"]])},
        "bigger_jobs": {"subagents": len(big), "cheaper": sum(1 for x in big if x["delegated"] < x["inline"]),
                        "median_ratio": _median([x["delegated"] / x["inline"] for x in big if x["inline"]])},
        "rows": rows,
    }


def fanouts(ds: Dataset) -> List[List[Agent]]:
    """Groups of subagents with the same parent started within FANOUT_WINDOW seconds of the group's first."""
    groups = []
    by_parent: Dict[Tuple[str, str], List[Agent]] = defaultdict(list)
    for run, a in ds.subagents:
        if a.started:
            by_parent[(run.session_id, a.parent_id or "main")].append(a)
    for kids in by_parent.values():
        kids.sort(key=lambda a: _t(a.started))
        cur: List[Agent] = []
        for a in kids:
            if cur and _t(a.started) - _t(cur[0].started) > FANOUT_WINDOW:
                groups.append(cur)
                cur = []
            cur.append(a)
        if cur:
            groups.append(cur)
    return groups


def parallel_section(ds: Dataset) -> dict:
    """Time: a fan-out's wall time (first start to last report) against the sum of its
    subagents' own durations, which is roughly what running them one after another would take."""
    groups = fanouts(ds)
    multi = [g for g in groups if len(g) >= 2]
    serial = wall = 0.0
    for g in multi:
        ends = [_t(_end(a)) for a in g if _end(a)]
        if not ends:
            continue
        wall += max(ends) - min(_t(a.started) for a in g)
        serial += sum(_t(_end(a)) - _t(a.started) for a in g if _end(a))
    bg = Counter("background" if a.background else "waited for" if a.background is False else "unknown"
                 for _, a in ds.subagents)
    return {
        "fanouts": len(groups), "single": sum(1 for g in groups if len(g) == 1),
        "parallel_fanouts": len(multi), "sizes": sorted((len(g) for g in multi), reverse=True),
        "parallel_wall_minutes": round(wall / 60, 1), "parallel_serial_minutes": round(serial / 60, 1),
        "minutes_saved": round((serial - wall) / 60, 1),
        "lead_waited_vs_background": dict(bg),
    }


def _items(a: Agent) -> set:
    return az.signals(a.report_text or "") - az.signals(a.instructions or "")


def overlap_section(ds: Dataset) -> dict:
    """Redundancy among parallel helpers: of each report's distinctive items (names, paths,
    URLs, numbers not in its own instructions), how many also appear in a sibling's report?
    Baseline: the same measure against reports from other sessions, which can't share work."""
    groups = [g for g in fanouts(ds) if sum(1 for a in g if a.report_text) >= 2]
    sib, base = [], []
    reports = [(run.session_id, a) for run, a in ds.subagents if a.report_text]
    for g in groups:
        session = next(run.session_id for run, a in ds.subagents if a is g[0])
        for a in g:
            mine = _items(a)
            if len(mine) < 5:
                continue
            others = set().union(*(_items(b) for b in g if b is not a and b.report_text))
            sib.append(len(mine & others) / len(mine))
            strangers = [b for s, b in reports if s != session][: len(g) - 1 or 1]
            if strangers:
                base.append(len(mine & set().union(*(_items(b) for b in strangers))) / len(mine))
    return {
        "fanouts_with_reports": len(groups), "reports_measured": len(sib),
        "sibling_overlap_median_pct": round(100 * _median(sib), 1) if sib else None,
        "sibling_overlap_max_pct": round(100 * max(sib), 1) if sib else None,
        "baseline_overlap_median_pct": round(100 * _median(base), 1) if base else None,
    }


def waste_section(ds: Dataset) -> dict:
    """Repeats (identical results fetched again), lineage weighted by cost, and file conflicts."""
    reps = []
    for run in ds.runs:
        owned = {r.id for r in run.requests() if r.owned}
        reps += [x for x in az.find_repeats(run, ds.scale) if x.call.request_id in owned]
    total = sum(x["cost"] for x in ds.all_light if x["multi"])
    keep = {(run.session_id, a.id) for run, a in ds.subagents}
    lin_cost: Dict[str, float] = defaultdict(float)
    lin_n: Counter = Counter()
    conflicts = set()
    for run in ds.runs:
        for ln in az.lineage(run):
            if (run.session_id, ln.agent.id) in keep:
                lin_n[ln.status] += 1
                lin_cost[ln.status] += sum(request_cost(r) or 0.0 for r in ln.agent.requests)
        for c in az.file_conflicts(run):
            conflicts.add((c.path, c.kind, tuple(c.agents)))
    sub_cost = sum(lin_cost.values())
    parallel_siblings = 0
    for x in reps:
        if not x.within_agent:
            a, b = x.first.agent_id, x.call.agent_id
            parallel_siblings += a != "main" and b != "main"
    return {
        "repeats_within_agent": sum(1 for x in reps if x.within_agent),
        "repeats_across_agents": sum(1 for x in reps if not x.within_agent),
        "repeats_between_subagents": parallel_siblings,
        "repeat_tools": dict(Counter(x.call.name for x in reps).most_common()),
        "repeat_tokens": sum(x.tokens for x in reps),
        "repeat_cost_upper": round(sum(x.cost for x in reps), 3),
        "repeat_share_pct": _pct(sum(x.cost for x in reps), total),
        "lineage_count": dict(lin_n.most_common()),
        "lineage_cost": {k: round(v, 2) for k, v in sorted(lin_cost.items(), key=lambda kv: -kv[1])},
        "lineage_cost_pct": {k: _pct(v, sub_cost) for k, v in sorted(lin_cost.items(), key=lambda kv: -kv[1])},
        "conflicts_several_writers": sum(1 for c in conflicts if c[1] == "several writers"),
        "conflicts_stale_writes": sum(1 for c in conflicts if c[1] == "stale write"),
    }


def approvals_section(ds: Dataset) -> dict:
    def agg(rows):
        return {"decisions": sum(x["approvals"] for x in rows), "by_settings": sum(x["by_settings"] for x in rows),
                "by_person": sum(x["by_person"] for x in rows),
                "rejected_by_person": sum(x["rejected_by_person"] for x in rows),
                "answers_to_questions": sum(x["answers"] for x in rows)}
    return {"multi_agent_sessions": agg([x for x in ds.all_light if x["multi"]]), "all_sessions": agg(ds.all_light),
            # a fork copies earlier messages, so count each moment once
            "messages_from_you_in_multi_agent_runs": len({t for r in ds.runs for t in r.human_messages})}


def blind_spots_section(ds: Dataset) -> dict:
    clean = [x for x in ds.all_light if x["clean"] and x["missing_tokens"] is not None]
    seen = sum(x["transcript_tokens"] for x in clean)
    missing = sum(x["missing_tokens"] for x in clean)
    return {
        "clean_sessions_checked": len(clean),
        "clean_sessions_exact": sum(1 for x in clean if x["verdict"] == "match"),
        "overcounts": sum(1 for x in clean if x["verdict"] == "OVERCOUNT"),
        "hidden_token_share_pct": _pct(missing, seen + missing),
        "compactions": sum(x["compactions"] for x in ds.all_light),
        "image_or_document_results": sum(x["media_results"] for x in ds.all_light),
        "claude_code_versions": len({v for x in ds.all_light for v in x["versions"]}),
    }


def study(root: Optional[str] = None) -> dict:
    ds = load(root)
    out = {
        "corpus": corpus_section(ds),
        "spend": spend_section(ds),
        "startup": startup_section(ds),
        "delegation": delegation_section(ds),
        "parallel": parallel_section(ds),
        "overlap": overlap_section(ds),
        "waste": waste_section(ds),
        "approvals": approvals_section(ds),
        "blind_spots": blind_spots_section(ds),
        "chars_per_token": round(ds.scale.overall, 2),
    }
    out["per_session"] = per_session(ds)
    return out


def per_session(ds: Dataset) -> List[dict]:
    rows = []
    keep = {(run.session_id, a.id) for run, a in ds.subagents}
    for run in ds.runs:
        owned = [r for r in run.requests() if r.owned]
        lin = Counter(ln.status for ln in az.lineage(run) if (run.session_id, ln.agent.id) in keep)
        rows.append({
            "session": run.session_id[:8], "started": _t(run.started) or None,
            "subagents": sum(1 for s, a in ds.subagents if s is run),
            "cost": round(sum(request_cost(r) or 0.0 for r in owned), 2),
            "subagent_cost": round(sum(request_cost(r) or 0.0 for r in owned if r.agent_id != "main"), 2),
            "copied_calls": sum(1 for r in run.requests() if not r.owned),
            "lineage": dict(lin), "peak_parallel": az.peak_parallel(run),
        })
    return rows
