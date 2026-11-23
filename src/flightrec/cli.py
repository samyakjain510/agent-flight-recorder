"""The flightrec command. Everything it reads and writes stays on this machine."""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from typing import List, Optional

from . import __version__, fmt
from . import analyze as az
from .check import FIELDS, check_run
from .claude_code import default_root, find_sessions, parse_session, session_id_of, subagent_files
from .corpus import mark_shared, request_index
from .model import Run, Usage


def _pick_sessions(root: str, everything: bool) -> List[str]:
    paths = find_sessions(root)
    return paths if everything else [p for p in paths if subagent_files(p)]


def _find_one(root: str, prefix: str) -> str:
    hits = [p for p in find_sessions(root) if session_id_of(p).startswith(prefix)]
    if not hits:
        raise SystemExit(f"no session starts with {prefix!r}")
    if len(hits) > 1:
        raise SystemExit(f"{prefix!r} matches {len(hits)} sessions; give more characters")
    return hits[0]


# ---------------------------------------------------------------- list

def cmd_list(args) -> int:
    rows = []
    for path in _pick_sessions(args.root, args.all):
        run = parse_session(path)
        rows.append(run)
    rows.sort(key=lambda r: r.started.timestamp() if r.started else 0)
    print(f"{'session':10} {'started':16} {'length':>8} {'subagents':>9} {'calls':>6} {'tokens':>8}  title")
    for run in rows:
        u = run.usage()
        print(
            f"{run.session_id[:8]:10} {fmt.when(run.started):16} {fmt.duration(run.started, run.ended):>8} "
            f"{len(run.subagents):>9} {len(run.requests()):>6} {fmt.tokens(u.total):>8}  "
            f"{fmt.text(run.title or '', args.redact, 40)}"
        )
    print(f"\n{len(rows)} sessions" + ("" if args.all else " with subagents (use --all for every session)"))
    return 0


# ---------------------------------------------------------------- show

def cmd_show(args) -> int:
    run = parse_session(_find_one(args.root, args.session))
    mark_shared(run, request_index(args.root))
    total = run.usage()
    print(f"Session {run.session_id}  {fmt.text(run.title or '', args.redact, 60)}")
    print(f"  {fmt.when(run.started)} → {fmt.when(run.ended)}  ({fmt.duration(run.started, run.ended)})"
          f"  · Claude Code {', '.join(sorted(run.versions)[-1:]) or '?'}")
    print(f"  {len(run.agents)} agents · {len(run.requests())} model calls · {len(run.tool_calls())} tool calls"
          f" · {len(run.human_messages)} messages from you")
    print(f"  {fmt.usage_line(total)}\n")

    for agent, depth in run.tree():
        pad = "    " * depth
        if agent.id == "main":
            head = f"lead · {', '.join(agent.models()) or '?'}"
        else:
            mode = {True: "background", False: "waited for", None: "?"}[agent.background]
            head = (f"{agent.kind} · {', '.join(agent.models()) or agent.model_requested or '?'} · {mode}"
                    f"  \"{fmt.text(agent.description, args.redact, 50)}\"")
            if agent.transcript is None:
                head += "  [no transcript]"
        print(f"{pad}{'└─ ' if depth else ''}{head}")
        b = run.branch_usage(agent.id)
        own = agent.usage()
        tools = Counter(c.name for c in agent.tool_calls)
        tool_str = ", ".join(f"{n} {c}" for n, c in tools.most_common(5))
        line = f"{len(agent.requests)} calls · {fmt.tokens(own.total)} tokens"
        if run.children(agent.id):
            line += f" (branch {fmt.tokens(b.total)})"
        line += f" · {fmt.duration(agent.started, agent.ended)}"
        if agent.id != "main" and agent.report_at and agent.started:
            line += f" · report reached parent after {fmt.span(agent.report_at - agent.started)}"
        print(f"{pad}{'   ' if depth else ''}  {line}")
        if tool_str:
            print(f"{pad}{'   ' if depth else ''}  tools: {tool_str}")
        if args.instructions and agent.instructions and not args.redact:
            print(f"{pad}{'   ' if depth else ''}  instructions: {agent.instructions}")

    approvals = run.approvals()
    if approvals:
        by = Counter(a.source for a in approvals)
        rejected = sum(1 for a in approvals if a.decision != "accept")
        print(f"\nApprovals: {len(approvals)} decisions · " + ", ".join(f"{s} {n}" for s, n in by.most_common())
              + (f" · {rejected} rejected" if rejected else ""))
    else:
        print("\nApprovals: none recorded (older Claude Code versions did not write them)")
    shared = [r for r in run.requests() if r.shared_with]
    if shared:
        others = {s[:8] for r in shared for s in r.shared_with}
        print(f"Copied history: {len(shared)} of {len(run.requests())} model calls also appear in "
              f"{len(others)} other session file(s): {', '.join(sorted(others))}")
    if run.compactions:
        print(f"Compactions: {len(run.compactions)}")
    for note in run.notes:
        print(f"Note: {note}")
    if run.unknown_record_types:
        print(f"Unknown record types (format may have changed): {dict(run.unknown_record_types)}")
    return 0


# ---------------------------------------------------------------- check

def _pct(ours: int, theirs: int) -> str:
    if theirs == 0:
        return "n/a" if ours == 0 else "+inf"
    return f"{(ours - theirs) / theirs * 100:+.1f}%"


def cmd_check(args) -> int:
    index = request_index(args.root)
    results = []
    for path in _pick_sessions(args.root, args.all):
        run = parse_session(path)
        mark_shared(run, index)
        results.append(check_run(run))
    results.sort(key=lambda c: c.run.started.timestamp() if c.run.started else 0)

    for c in results:
        tag = "clean" if c.clean else "     "
        line = f"{c.run.session_id[:8]}  {fmt.when(c.run.started)}  {tag}  {c.verdict}"
        if c.verdict == "transcript missing calls":
            gap = Usage()
            for m in c.models:
                if m.ours is not None:
                    gap.add(m.missing())
            line += f"  (fresh in +{gap.input}, cache read +{fmt.tokens(gap.cache_read)}, out +{gap.output})"
        print(line)
        if c.verdict in ("OVERCOUNT", "differs (explained)") or args.verbose:
            for m in c.models:
                if m.status not in ("match", "not in transcript"):
                    parts = []
                    for name in FIELDS:
                        o = getattr(m.ours, name) if m.ours else 0
                        t = getattr(m.theirs, name) if m.theirs else 0
                        parts.append(f"{name} {_pct(o, t)}")
                    print(f"      {m.model}: {m.status} ({' · '.join(parts)})")
            for reason in c.reasons:
                print(f"      why: {reason}")

    with_totals = [c for c in results if c.has_totals]
    clean = [c for c in with_totals if c.clean]
    exact = [c for c in clean if c.verdict == "match"]
    missing = [c for c in clean if c.verdict == "transcript missing calls"]
    over = [c for c in clean if c.verdict == "OVERCOUNT"]
    other = [c for c in with_totals if not c.clean]

    print("\nSummary")
    print(f"  sessions checked: {len(results)}  ·  with Claude Code totals: {len(with_totals)}"
          f"  ·  clean: {len(clean)}  ·  copied, compacted or still running: {len(other)}")
    print(f"  clean sessions:  exact match {len(exact)}  ·  transcript missing calls {len(missing)}"
          f"  ·  OVERCOUNT (reader bug) {len(over)}")

    def theirs_total(cs):
        return sum(m.theirs.total for c in cs for m in c.models if m.theirs)

    gap = Usage()
    for c in missing:
        for m in c.models:
            if m.ours is not None:
                gap.add(m.missing())
    if missing:
        t = theirs_total(missing)
        print(f"  same-model calls missing from transcripts ({len(missing)} sessions): {fmt.tokens(gap.total)} of"
              f" {fmt.tokens(t)} tokens ({gap.total / t * 100:.1f}%) · fresh in {gap.input} · cache read"
              f" {fmt.tokens(gap.cache_read)} · out {gap.output}")
    side_tokens = sum(m.theirs.total for c in clean for m in c.side_models)
    side_names = Counter(m.model for c in clean for m in c.side_models)
    if side_tokens:
        t = theirs_total(clean)
        print(f"  calls on models that never appear in the transcript: {fmt.tokens(side_tokens)} of {fmt.tokens(t)}"
              f" tokens ({side_tokens / t * 100:.1f}%) · " + ", ".join(f"{m} in {n} sessions" for m, n in side_names.most_common()))
    return 0


# ---------------------------------------------------------------- analyze

def _target_label(t: str, redact: bool, limit: int = 60) -> str:
    kind, _, rest = t.partition(" ")
    if redact:
        if kind == "file":
            ext = rest.split(" ")[0].rsplit(".", 1)[-1] if "." in rest.split(" ")[0] else "?"
            return f"file (.{ext})"
        return kind
    return fmt.text(t, False, limit)


def _money(x: float) -> str:
    return f"${x:,.2f}" if x >= 0.995 else f"${x:.3f}"


def _agent_label(agent, redact: bool) -> str:
    if agent.id == "main":
        return "lead"
    return f"{agent.kind} \"{fmt.text(agent.description, redact, 30)}\""


def _analysis(run: Run, scale, only_owned: bool = False) -> dict:
    from . import analyze as az
    from .pricing import request_cost

    owned_ids = {r.id for r in run.requests() if r.owned or not only_owned}
    reqs = [r for r in run.requests() if r.id in owned_ids]
    costs = [request_cost(r) for r in reqs]
    by_agent_cost = Counter()
    for r, c in zip(reqs, costs):
        by_agent_cost["lead" if r.agent_id == "main" else "sub"] += c or 0.0
    return {
        "run": run,
        "cost": sum(c or 0.0 for c in costs),
        "cost_unknown": sum(1 for c in costs if c is None),
        "lead_cost": by_agent_cost["lead"],
        "sub_cost": by_agent_cost["sub"],
        "startup": [s for s in az.startup_costs(run) if not only_owned or all(r.id in owned_ids for r in s.agent.requests)],
        "repeats": [x for x in az.find_repeats(run, scale) if x.call.request_id in owned_ids],
        "lineage": az.lineage(run),
        "conflicts": az.file_conflicts(run),
        "approvals": az.approvals(run),
        "peak": az.peak_parallel(run),
    }


def cmd_analyze(args) -> int:
    from . import analyze as az

    corpus = request_index(args.root)
    paths = _pick_sessions(args.root, False) if args.all else [_find_one(args.root, args.session)]
    runs = []
    for p in paths:
        run = parse_session(p)
        mark_shared(run, corpus)
        runs.append(run)
    scale = az.calibrate(parse_session(p) for p in find_sessions(args.root))
    if args.all:
        return _analyze_all(runs, scale, args)
    return _analyze_one(runs[0], scale, args)


def _analyze_one(run: Run, scale, args) -> int:
    a = _analysis(run, scale)
    print(f"Session {run.session_id[:8]}  {fmt.text(run.title or '', args.redact, 50)}")
    print(f"  {len(run.agents)} agents · {len(run.requests())} model calls · API-equivalent cost {_money(a['cost'])}"
          f" (lead {_money(a['lead_cost'])}, subagents {_money(a['sub_cost'])})"
          + (f" · {a['cost_unknown']} calls on models with no known price" if a["cost_unknown"] else ""))
    shared = sum(1 for r in run.requests() if not r.owned)
    if shared:
        print(f"  {shared} of these calls are copies of history from an older file")
    print(f"  token sizes measured from {scale.samples} steps: about {scale.overall:.1f} characters per token"
          f" (Read {scale.ratio('Read'):.1f}, Bash {scale.ratio('Bash'):.1f})")

    print("\nStartup cost: the context each agent starts with, re-read on every call")
    for s in a["startup"]:
        print(f"  {_agent_label(s.agent, args.redact):42} first input {fmt.tokens(s.first_input):>6} · {s.calls:3} calls"
              f" · {s.token_share * 100:4.0f}% of its input tokens · {s.cost_share * 100:4.0f}% of its cost")

    reps = a["repeats"]
    print("\nRepeated work (same target, identical result)")
    for within, label in ((True, "inside one agent (it already had this)"), (False, "across agents (another agent had it)")):
        group = [x for x in reps if x.within_agent == within]
        if group:
            print(f"  {label}: {len(group)} repeats · {fmt.tokens(sum(x.tokens for x in group))} tokens added again"
                  f" · up to {fmt.tokens(sum(x.carried_tokens for x in group))} carried · up to {_money(sum(x.cost for x in group))}")
        else:
            print(f"  {label}: none")
    top = Counter()
    for x in reps:
        top[az.target(x.call) or "?"] += 1
    for t, n in top.most_common(5):
        print(f"    {n}× {_target_label(t, args.redact)}")

    print("\nDid subagent reports reach the parent's later work? (text matching; paraphrase is missed)")
    for ln in a["lineage"]:
        parts = [f"report: {ln.report_status}"]
        if ln.report_status in ("used", "some trace", "no trace found"):
            parts[0] += f" ({ln.items_reused}/{ln.items_total} items, {ln.phrase_overlap * 100:.0f}% phrases)"
        if ln.files_written:
            parts.append(f"files: {ln.files_written} written, {ln.files_picked_up} picked up, "
                         f"{ln.files_revised} revised, {ln.files_replaced} replaced")
        print(f"  {_agent_label(ln.agent, args.redact):42} {ln.status.upper():26} " + " · ".join(parts))
    if not a["lineage"]:
        print("  no subagents")

    print("\nFiles written by more than one agent")
    if a["conflicts"]:
        for c in a["conflicts"]:
            print(f"  {c.kind}: {_target_label('file ' + c.path, args.redact)} by {', '.join(x[:8] for x in c.agents)}")
    else:
        print("  none")

    ap = a["approvals"]
    print("\nApprovals")
    if ap.total:
        print(f"  {ap.total} decisions · " + ", ".join(f"{k} {v}" for k, v in ap.by_source.most_common())
              + (f" · {ap.rejected} rejected" if ap.rejected else ""))
        waits = [s for _, s in ap.human if s is not None]
        if waits:
            print(f"  by a person: {len(waits)} · median {statistics.median(waits):.0f}s from the call to its result"
                  f" (includes running time)")
        risky = ap.quick_risky()
        if risky:
            print(f"  risky commands a person allowed in under 3s: {len(risky)}")
    else:
        print("  none recorded")
    if run.subagents:
        print(f"\nAt most {a['peak']} subagents were running at the same time.")
    print("\nCarried costs assume content stays in context until the agent stops, so they are upper bounds.")
    return 0


def _analyze_all(runs: List[Run], scale, args) -> int:
    rows = [_analysis(r, scale, only_owned=True) for r in runs]
    rows.sort(key=lambda a: a["run"].started.timestamp() if a["run"].started else 0)
    print(f"{'session':9} {'subs':>4} {'cost':>9} {'sub cost':>9} {'repeats in/x':>12} {'repeat cost':>11}"
          f" {'used/trace/none/late':>20} {'people ok':>9}")
    for a in rows:
        run = a["run"]
        ln = Counter(x.status for x in a["lineage"])
        reps = a["repeats"]
        print(f"{run.session_id[:8]:9} {len(run.subagents):>4} {_money(a['cost']):>9} {_money(a['sub_cost']):>9}"
              f" {sum(x.within_agent for x in reps):>5}/{sum(not x.within_agent for x in reps):<6}"
              f" {_money(sum(x.cost for x in reps)):>11}"
              f" {ln['used']:>5}/{ln['some trace']}/{ln['no trace found']}/{ln['arrived after parent stopped']:<6}"
              f" {len(a['approvals'].human):>9}")

    total = sum(a["cost"] for a in rows)
    sub_total = sum(a["sub_cost"] for a in rows)
    subs = [s for a in rows for s in a["startup"] if s.agent.id != "main"]
    reps = [x for a in rows for x in a["repeats"]]
    lin = Counter(x.status for a in rows for x in a["lineage"])
    print(f"\nAcross {len(rows)} multi-agent sessions (copied history counted once):")
    print(f"  API-equivalent cost {_money(total)}; subagents {_money(sub_total)} ({sub_total / total * 100:.0f}%)" if total else "")
    if subs:
        firsts = sorted(s.first_input for s in subs)
        carried = sum(s.carried_cost for s in subs)
        sub_cost = sum(s.total_cost for s in subs)
        print(f"  startup: {len(subs)} subagents with transcripts · median first input {fmt.tokens(int(statistics.median(firsts)))} tokens"
              f" · base context = {carried / sub_cost * 100:.0f}% of subagent cost" if sub_cost else "")
    for within, label in ((True, "repeats inside one agent"), (False, "repeats across agents")):
        g = [x for x in reps if x.within_agent == within]
        by_tool = Counter(x.call.name for x in g)
        print(f"  {label}: {len(g)} · {fmt.tokens(sum(x.tokens for x in g))} tokens added again · up to"
              f" {_money(sum(x.cost for x in g))} ({sum(x.cost for x in g) / total * 100:.1f}% of cost)"
              + (" · " + ", ".join(f"{k} {v}" for k, v in by_tool.most_common(4)) if g else ""))
    print("  subagent reports: " + ", ".join(f"{k} {v}" for k, v in lin.most_common()))
    print(f"  files written by several agents: {sum(1 for a in rows for c in a['conflicts'] if c.kind == 'several writers')}"
          f" · stale writes: {sum(1 for a in rows for c in a['conflicts'] if c.kind == 'stale write')}")
    print(f"  approvals by a person: {sum(len(a['approvals'].human) for a in rows)}"
          f" of {sum(a['approvals'].total for a in rows)} decisions")
    return 0


# ---------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="flightrec",
        description="Read Claude Code transcripts and explain multi-agent runs. Nothing leaves this machine.",
    )
    p.add_argument("--version", action="version", version=f"flightrec {__version__}")
    p.add_argument("--root", default=None, help="transcripts folder (default: ~/.claude/projects)")
    p.add_argument("--redact", action="store_true", help="hide titles, descriptions and instructions")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("list", help="list sessions (those with subagents, unless --all)")
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="show one run as a tree with tokens and time per branch")
    s.add_argument("session", help="session id, or its first few characters")
    s.add_argument("--instructions", action="store_true", help="print each subagent's full instructions")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("analyze", help="findings for one run: startup cost, repeated work, lineage, conflicts, approvals")
    s.add_argument("session", nargs="?", help="session id, or its first few characters")
    s.add_argument("--all", action="store_true", help="summarize every session that used subagents")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("check", help="compare our token counts with Claude Code's own totals")
    s.add_argument("--all", action="store_true", help="check every session, not only those with subagents")
    s.add_argument("--verbose", action="store_true", help="show per-model detail for every session")
    s.set_defaults(func=cmd_check)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    args.root = args.root or default_root()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
