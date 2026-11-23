"""The flightrec command. Everything it reads and writes stays on this machine."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from typing import List, Optional

from . import __version__, fmt
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
