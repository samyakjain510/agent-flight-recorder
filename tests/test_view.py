"""Tests for the page data: the squeezed clock, redaction, and safe embedding.

Run:  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flightrec import analyze as az  # noqa: E402
from flightrec import view  # noqa: E402
from flightrec.model import Agent, Approval, Request, Run, ToolCall, Usage  # noqa: E402

T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
SECRET = "my-secret-project-name"


def at(s):
    return T0 + timedelta(seconds=s)


def make_run():
    run = Run("abcdef12-0000", "/tmp/x.jsonl", "-Users-someone-" + SECRET, title="Plan for " + SECRET)
    lead = Agent("main", None, "lead")
    lead.requests = [Request("r1", "main", "claude-opus-5-5", at(0), at(5), Usage(input=10, cache_read=1000, output=50)),
                     Request("r2", "main", "claude-opus-5-5", at(4000), at(4010), Usage(input=10, cache_read=2000, output=50))]
    c = ToolCall("t1", "main", "r1", "Bash", {"command": f"cat ~/{SECRET}/notes.md\necho done"}, at(1), at(2), result_chars=300)
    c.approval = Approval("t1", "accept", "user_temporary")
    lead.tool_calls = [c, ToolCall("t2", "main", "r1", "Agent", {"prompt": "x", "description": "look up " + SECRET},
                                   at(3), at(60), spawned_agent_id="a1")]
    lead.said = [(at(0), "I'll check " + SECRET + " first."), (at(4000), "Back after a break.")]
    lead.outputs = list(lead.said)
    lead.started, lead.ended = at(0), at(4010)
    sub = Agent("a1", "main", "general-purpose", description="look up " + SECRET,
                instructions="Find facts about " + SECRET, transcript="/tmp/a1.jsonl")
    sub.requests = [Request("r3", "a1", "claude-opus-5-5", at(10), at(20), Usage(input=40000, output=100))]
    sub.started, sub.ended, sub.report_at = at(10), at(55), at(60)
    sub.report_text = "Found it: " + SECRET + " ships 2026-12-01."
    run.agents = {"main": lead, "a1": sub}
    run.human_messages = [at(0), at(3990)]
    return run


class ClockTests(unittest.TestCase):
    def test_squeezes_long_gaps_only(self):
        clock = view.Clock([0, 10, 20, 2020, 2030], gap=600, break_len=5)
        self.assertEqual([clock(t) for t in (0, 10, 20, 2020, 2030)], [0, 10, 20, 25, 35])
        self.assertEqual(clock.breaks, [{"d": 20, "len": 5, "real": 2000}])
        self.assertEqual((clock.active, clock.total), (30, 35))

    def test_moment_inside_a_gap_lands_inside_the_break(self):
        clock = view.Clock([0, 1000], gap=600, break_len=10)
        self.assertEqual(clock(500), 5)

    def test_default_break_is_small_but_visible(self):
        clock = view.Clock([0, 100, 10_000], gap=600)
        self.assertEqual(clock.break_len, 10.0)  # a tenth of the 100 s of activity
        many = view.Clock([i * 1000 for i in range(40)], gap=600)  # 39 breaks, no activity: floor of 5 s
        self.assertEqual(many.break_len, 5.0)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.run = make_run()
        self.scale = az.TokenScale({}, 2.0, 0)

    def test_tree_times_and_findings(self):
        p = view.build(self.run, self.scale)
        self.assertEqual([a["label"] for a in p["agents"]], ["lead", "general-purpose · look up " + SECRET])
        self.assertEqual(p["agents"][1]["parent"], 0)
        self.assertEqual(len(p["breaks"]), 1)  # the 3,930 s pause before the second lead call
        self.assertLess(p["display"], 200)
        kinds = sorted({f["kind"] for f in p["findings"]})
        self.assertEqual(kinds, ["approval", "lineage", "startup"])
        self.assertEqual(p["tools"][1]["spawned"], 1)
        self.assertEqual(p["tools"][0]["target"], f"cat ~/{SECRET}/notes.md")  # first line of the command

    def test_redaction_removes_transcript_text(self):
        page = view.render(view.build(self.run, self.scale, redact=True))
        self.assertNotIn(SECRET, page)
        self.assertNotIn("someone", page)
        self.assertIn("general-purpose #1", page)

    def test_text_cannot_end_the_script_early(self):
        self.run.agents["a1"].report_text = "</script><script>alert(1)</script>"
        page = view.render(view.build(self.run, self.scale))
        self.assertEqual(page.count("</script>"), view.render(view.build(make_run(), self.scale)).count("</script>"))


if __name__ == "__main__":
    unittest.main()
