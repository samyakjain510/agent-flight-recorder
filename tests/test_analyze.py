"""Tests for the analyzers. Runs are built by hand so every expected number is known.

Run:  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flightrec import analyze as az  # noqa: E402
from flightrec.model import Agent, Approval, Event, Request, Run, ToolCall, Usage  # noqa: E402
from flightrec.pricing import usage_cost  # noqa: E402

OPUS = "claude-opus-5-5"
T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def req(rid, agent, t, total_in, out=100, model=OPUS):
    # all input as cache reads except a small fresh part, so prices are easy to check
    return Request(rid, agent, model, at(t), at(t), Usage(input=10, cache_write=0, cache_read=total_in - 10, output=out))


def call(cid, agent, name, t, done=None, chars=0, h=None, **inp):
    return ToolCall(cid, agent, None, name, inp, at(t), at(done if done is not None else t + 1),
                    result_chars=chars, result_hash=h)


def make_run():
    run = Run("s1", "/tmp/s1.jsonl", "proj")
    run.agents["main"] = Agent("main", None, "lead")
    return run


class TokenSizeTests(unittest.TestCase):
    def test_tokens_per_char_from_context_growth(self):
        a = Agent("main", None, "lead")
        a.requests = [req("r1", "main", 0, 1000, out=100), req("r2", "main", 10, 2100)]
        a.tool_calls = [call("t1", "main", "Read", 1, chars=2000)]
        a.events = [Event("call", at(0), "r1"), Event("result", at(2), "t1", 2000), Event("call", at(10), "r2")]
        steps = list(az.one_result_steps(a))
        self.assertEqual(steps, [("Read", 2000, 1000)])  # 2100 - 1000 - 100 tokens for 2000 chars

    def test_later_calls(self):
        a = Agent("main", None, "lead")
        a.events = [Event("call", at(0), "r1"), Event("result", at(1), "t1"), Event("call", at(2), "r2"),
                    Event("call", at(3), "r3")]
        self.assertEqual(az.later_calls(a), {"t1": 2})


class RepeatTests(unittest.TestCase):
    def setUp(self):
        self.run = make_run()
        lead = self.run.agents["main"]
        lead.requests = [req("r1", "main", 0, 5000), req("r2", "main", 5, 6000), req("r3", "main", 9, 7000)]
        lead.tool_calls = [
            call("t1", "main", "Read", 1, chars=400, h="AAA", file_path="/x.py"),
            call("t2", "main", "Read", 6, chars=400, h="AAA", file_path="/x.py"),   # identical: repeat
            call("t3", "main", "Read", 7, chars=400, h="BBB", file_path="/x.py"),   # file changed: not a repeat
        ]
        lead.events = [Event("call", at(0), "r1"), Event("result", at(2), "t1", 400), Event("call", at(5), "r2"),
                       Event("result", at(7), "t2", 400), Event("result", at(8), "t3", 400), Event("call", at(9), "r3")]
        sub = Agent("a1", "main", "general-purpose")
        sub.requests = [req("q1", "a1", 3, 4000)]
        sub.tool_calls = [call("u1", "a1", "Read", 3, chars=400, h="AAA", file_path="/x.py")]
        sub.events = [Event("call", at(3), "q1"), Event("result", at(4), "u1", 400)]
        self.run.agents["a1"] = sub
        self.scale = az.TokenScale({"Read": 2.0}, 2.0, 0)

    def test_finds_within_and_across(self):
        reps = az.find_repeats(self.run, self.scale)
        self.assertEqual(sorted((r.call.id, r.within_agent) for r in reps), [("t2", True), ("u1", False)])

    def test_repeat_cost(self):
        rep = [r for r in az.find_repeats(self.run, self.scale) if r.call.id == "t2"][0]
        self.assertEqual(rep.tokens, 200)  # 400 chars / 2.0
        self.assertEqual(rep.later_calls, 1)
        # no cache writes recorded, so the write price falls back to 5-minute ($5/M); reads at $0.20/M
        self.assertAlmostEqual(rep.cost, 200 * (5.00 + 1 * 0.20) / 1e6)


class StartupTests(unittest.TestCase):
    def test_base_context_share(self):
        run = make_run()
        sub = Agent("a1", "main", "general-purpose")
        sub.requests = [req("q1", "a1", 0, 40000), req("q2", "a1", 5, 42000), req("q3", "a1", 9, 44000)]
        run.agents["a1"] = sub
        s = [x for x in az.startup_costs(run) if x.agent.id == "a1"][0]
        self.assertEqual(s.first_input, 40000)
        self.assertAlmostEqual(s.token_share, 40000 * 3 / (40000 + 42000 + 44000))
        first_input_cost = usage_cost(Usage(input=10, cache_read=39990), OPUS)
        self.assertAlmostEqual(s.carried_cost, first_input_cost + 2 * 40000 * 0.20 / 1e6)


class LineageTests(unittest.TestCase):
    def _run(self, parent_outputs, report="See https://example.org/data and AgentTreeView: 53.5%", sub_calls=(),
             parent_calls=()):
        run = make_run()
        lead = run.agents["main"]
        lead.outputs = parent_outputs
        lead.tool_calls = list(parent_calls)
        sub = Agent("a1", "main", "general-purpose", instructions="Find data about trees")
        sub.report_text, sub.report_at = report, at(100)
        sub.tool_calls = list(sub_calls)
        run.agents["a1"] = sub
        return az.lineage(run)[0]

    def test_used_when_items_reappear_later(self):
        ln = self._run([(at(150), "Per https://example.org/data, 53.5% use AgentTreeView.")])
        self.assertEqual(ln.status, "used")
        self.assertEqual(ln.items_reused, 3)

    def test_items_parent_already_had_do_not_count(self):
        ln = self._run([(at(50), "I know https://example.org/data and AgentTreeView"), (at(150), "https://example.org/data")])
        self.assertEqual(ln.report_status, "no trace found")

    def test_report_after_parent_stopped(self):
        ln = self._run([(at(50), "final answer")])
        self.assertEqual(ln.status, "arrived after parent stopped")

    def test_file_channel(self):
        write = call("w1", "a1", "Write", 90, file_path="/out/notes.md", content="x")
        picked = self._run([(at(150), "thanks")], sub_calls=[write],
                           parent_calls=[call("p1", "main", "Read", 120, file_path="/out/notes.md")])
        self.assertEqual((picked.status, picked.files_picked_up), ("used", 1))
        replaced = self._run([(at(150), "thanks")], sub_calls=[write],
                             parent_calls=[call("p2", "main", "Write", 120, file_path="/out/notes.md", content="y")])
        self.assertEqual(replaced.status, "replaced by another agent")
        left = self._run([(at(150), "thanks")], sub_calls=[write])
        self.assertEqual(left.status, "file left as written")


class ConflictTests(unittest.TestCase):
    def test_several_writers_and_stale_write(self):
        run = make_run()
        run.agents["main"].tool_calls = [call("m1", "main", "Edit", 20, file_path="/f.py")]
        sub = Agent("a1", "main", "general-purpose")
        sub.tool_calls = [call("s1", "a1", "Read", 10, file_path="/f.py"), call("s2", "a1", "Edit", 30, file_path="/f.py")]
        run.agents["a1"] = sub
        kinds = sorted(c.kind for c in az.file_conflicts(run))
        self.assertEqual(kinds, ["several writers", "stale write"])


class ApprovalTests(unittest.TestCase):
    def test_sources_and_quick_risky(self):
        run = make_run()
        c1 = call("c1", "main", "Bash", 0, done=1, command="rm -rf build")
        c1.approval = Approval("c1", "accept", "user_temporary")
        c2 = call("c2", "main", "Read", 0, done=1, file_path="/a")
        c2.approval = Approval("c2", "accept", "config")
        c3 = call("c3", "main", "Bash", 0, done=40, command="rm -rf dist")
        c3.approval = Approval("c3", "accept", "user_temporary")
        run.agents["main"].tool_calls = [c1, c2, c3]
        rep = az.approvals(run)
        self.assertEqual(rep.by_source["user_temporary"], 2)
        self.assertEqual([c.id for c in rep.quick_risky()], ["c1"])


class ParallelTests(unittest.TestCase):
    def test_peak(self):
        run = make_run()
        for i, (s, e) in enumerate([(0, 10), (5, 15), (12, 20)]):
            a = Agent(f"a{i}", "main", "general-purpose")
            a.started, a.report_at = at(s), at(e)
            run.agents[a.id] = a
        self.assertEqual(az.peak_parallel(run), 2)


if __name__ == "__main__":
    unittest.main()
