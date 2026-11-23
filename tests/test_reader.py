"""Tests for the Claude Code adapter and the cross-check.

Each test builds a tiny fake ~/.claude/projects folder by hand, so no real
transcript is ever needed. Every assumption listed at the top of
src/flightrec/claude_code.py has a test here.

Run:  python3 -m unittest discover -s tests
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flightrec.check import check_run  # noqa: E402
from flightrec.claude_code import parse_session, parse_ts, text_len  # noqa: E402
from flightrec.corpus import mark_shared, request_index  # noqa: E402

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"


def ts(minute, second=0):
    return f"2026-11-01T10:{minute:02d}:{second:02d}.000Z"


def usage(inp=10, cw=100, cr=1000, out=50):
    return {"input_tokens": inp, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr, "output_tokens": out}


def assistant(rid, model, u, blocks, when, **extra):
    rec = {"type": "assistant", "requestId": rid, "timestamp": when, "version": "2.1.330",
           "message": {"model": model, "role": "assistant", "content": blocks, "usage": u, "stop_reason": "tool_use"}}
    rec.update(extra)
    return rec


def tool_use(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(tid, when, text="ok", decision=None, meta=None, **extra):
    rec = {"type": "user", "timestamp": when,
           "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}
    if decision:
        rec["permissionDecision"] = {"decision": decision[0], "source": decision[1]}
    if meta:
        rec["toolUseResult"] = meta
    rec.update(extra)
    return rec


def cost_state(by_model):
    keys = {"input": "inputTokens", "cw": "cacheCreationInputTokens", "cr": "cacheReadInputTokens", "out": "outputTokens"}
    return {"type": "cost-state", "modelUsage": {
        m: {keys[k]: v for k, v in d.items()} for m, d in by_model.items()}}


class FakeRoot:
    """A throwaway projects folder."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="flightrec-test-")
        self.project = os.path.join(self.root, "-tmp-demo")
        os.makedirs(self.project)

    def write_session(self, sid, records, raw_lines=()):
        path = os.path.join(self.project, sid + ".jsonl")
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
            for line in raw_lines:
                f.write(line + "\n")
        old = time.time() - 3600
        os.utime(path, (old, old))  # not "live"
        return path

    def write_subagent(self, sid, agent_id, records, meta):
        d = os.path.join(self.project, sid, "subagents")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"agent-{agent_id}.jsonl"), "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        with open(os.path.join(d, f"agent-{agent_id}.meta.json"), "w") as f:
            json.dump(meta, f)

    def cleanup(self):
        shutil.rmtree(self.root)


def multi_agent_session(fake, sid="s1", reported=None):
    """Lead spawns a1 in the background and a2 synchronously (a2 left no transcript)."""
    lead = [
        {"type": "user", "timestamp": ts(0), "origin": {"kind": "human"},
         "message": {"role": "user", "content": "please research"}},
        # one model call written as two lines; output grows from 20 to 50
        assistant("r1", OPUS, usage(out=20), [{"type": "thinking", "thinking": ""}], ts(1)),
        assistant("r1", OPUS, usage(out=50), [tool_use("t-spawn1", "Agent", description="look things up",
                                                       prompt="Find X", subagent_type="general-purpose",
                                                       run_in_background=True),
                                              tool_use("t-read", "Read", file_path="/a.txt")], ts(1, 5)),
        tool_result("t-spawn1", ts(1, 6), "launched", ("accept", "config"),
                    {"status": "async_launched", "agentId": "a1", "isAsync": True}),
        tool_result("t-read", ts(1, 7), "file text", ("accept", "user_temporary")),
        {"type": "assistant", "timestamp": ts(2), "message": {"model": "<synthetic>", "content": [], "usage": usage(9, 9, 9, 9)}},
        assistant("r2", OPUS, usage(), [tool_use("t-spawn2", "Agent", description="quick check", prompt="Check Y",
                                                 subagent_type="general-purpose")], ts(3)),
        tool_result("t-spawn2", ts(4), "sync report text", ("accept", "config"),
                    {"status": "completed", "agentId": "a2", "content": []}),
        {"type": "attachment", "timestamp": ts(6),
         "attachment": {"type": "queued_command", "origin": {"kind": "peer", "from": "a1", "handbackReportText": "found X"}}},
        {"type": "brand-new-record-type"},
    ]
    if reported is not None:
        lead.append(cost_state(reported))
    fake.write_session(sid, lead, raw_lines=["{not json"])
    fake.write_subagent(sid, "a1", [
        {"type": "user", "timestamp": ts(1, 10), "agentId": "a1", "isSidechain": True,
         "message": {"role": "user", "content": "Find X"}},
        assistant("r-a1", SONNET, usage(inp=5, cw=200, cr=300, out=40), [{"type": "text", "text": "found X"}], ts(5),
                  agentId="a1", isSidechain=True),
    ], {"agentType": "general-purpose", "description": "look things up", "toolUseId": "t-spawn1", "spawnDepth": 1})
    return os.path.join(fake.project, sid + ".jsonl")


EXPECTED = {OPUS: {"input": 20, "cw": 200, "cr": 2000, "out": 100},
            SONNET: {"input": 5, "cw": 200, "cr": 300, "out": 40}}


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRoot()

    def tearDown(self):
        self.fake.cleanup()

    def test_split_call_counted_once_with_max_output(self):
        run = parse_session(multi_agent_session(self.fake))
        r1 = [r for r in run.lead.requests if r.id == "r1"][0]
        self.assertEqual(r1.usage.output, 50)
        self.assertEqual(r1.usage.cache_read, 1000)
        self.assertEqual(len(run.lead.requests), 2)  # r1 and r2; the synthetic line is skipped

    def test_totals_by_model(self):
        run = parse_session(multi_agent_session(self.fake))
        by = run.usage_by_model()
        self.assertEqual(by[OPUS].core(), (20, 200, 2000, 100))
        self.assertEqual(by[SONNET].core(), (5, 200, 300, 40))
        self.assertNotIn("<synthetic>", by)

    def test_tree_links_subagents_to_spawning_call(self):
        run = parse_session(multi_agent_session(self.fake))
        a1, a2 = run.agents["a1"], run.agents["a2"]
        self.assertEqual(a1.parent_id, "main")
        self.assertEqual(a1.instructions, "Find X")
        self.assertTrue(a1.background)
        self.assertEqual(a2.parent_id, "main")
        self.assertFalse(a2.background)
        self.assertEqual([a.id for a, _ in run.tree()], ["main", "a1", "a2"])

    def test_subagent_without_transcript_is_kept_as_stub(self):
        run = parse_session(multi_agent_session(self.fake))
        self.assertEqual(run.missing_transcripts, ["a2"])
        self.assertIsNone(run.agents["a2"].transcript)
        self.assertEqual(run.agents["a2"].instructions, "Check Y")

    def test_reports_reach_parent(self):
        run = parse_session(multi_agent_session(self.fake))
        self.assertEqual(run.agents["a1"].report_at, parse_ts(ts(6)))  # background: via peer message
        self.assertEqual(run.agents["a1"].report_chars, len("found X"))
        self.assertEqual(run.agents["a2"].report_at, parse_ts(ts(4)))  # synchronous: in the tool result
        self.assertEqual(run.agents["a2"].report_chars, len("sync report text"))

    def test_approvals_and_human_messages(self):
        run = parse_session(multi_agent_session(self.fake))
        sources = sorted(a.source for a in run.approvals())
        self.assertEqual(sources, ["config", "config", "user_temporary"])
        self.assertEqual(sum(a.by_human for a in run.approvals()), 1)
        self.assertEqual(len(run.human_messages), 1)

    def test_format_drift_is_visible(self):
        run = parse_session(multi_agent_session(self.fake))
        self.assertEqual(run.unknown_record_types["brand-new-record-type"], 1)
        self.assertEqual(run.bad_lines, 1)

    def test_fingerprints_cover_images_and_skip_empty_results(self):
        def image_result(tid, data, when):
            rec = tool_result(tid, when)
            rec["message"]["content"][0]["content"] = [{"type": "image", "source": {"type": "base64", "data": data}}]
            return rec
        path = self.fake.write_session("img", [
            assistant("r1", OPUS, usage(), [tool_use("i1", "Read", file_path="/a.png"),
                                            tool_use("i2", "Read", file_path="/b.png"),
                                            tool_use("i3", "Read", file_path="/empty.txt")], ts(1)),
            image_result("i1", "AAAA", ts(2)),
            image_result("i2", "BBBB", ts(3)),
            tool_result("i3", ts(4), ""),
        ])
        calls = {c.id: c for c in parse_session(path).lead.tool_calls}
        self.assertTrue(calls["i1"].result_media)
        self.assertNotEqual(calls["i1"].result_hash, calls["i2"].result_hash)  # different pictures, different prints
        self.assertIsNone(calls["i3"].result_hash)

    def test_helpers(self):
        self.assertEqual(parse_ts("2026-11-01T10:00:00.000Z").utcoffset().total_seconds(), 0)
        self.assertIsNone(parse_ts("not a time"))
        self.assertEqual(text_len([{"type": "text", "text": "abc"}, {"type": "tool_result", "content": "de"}]), 5)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRoot()

    def tearDown(self):
        self.fake.cleanup()

    def _check(self, path):
        run = parse_session(path)
        mark_shared(run, request_index(self.fake.root))
        return check_run(run)

    def test_exact_match(self):
        c = self._check(multi_agent_session(self.fake, reported=EXPECTED))
        self.assertTrue(c.clean)
        self.assertEqual(c.verdict, "match")

    def test_claude_counts_more_means_missing_calls(self):
        more = {OPUS: dict(EXPECTED[OPUS], input=526, cr=9000), SONNET: EXPECTED[SONNET],
                "claude-haiku-5-5": {"input": 900, "cw": 0, "cr": 0, "out": 30}}
        c = self._check(multi_agent_session(self.fake, reported=more))
        self.assertEqual(c.verdict, "transcript missing calls")
        self.assertEqual([m.model for m in c.side_models], ["claude-haiku-5-5"])
        opus = [m for m in c.models if m.model == OPUS][0]
        self.assertEqual(opus.missing().input, 506)

    def test_reader_counting_more_is_flagged_as_bug(self):
        less = {OPUS: dict(EXPECTED[OPUS], out=60), SONNET: EXPECTED[SONNET]}
        c = self._check(multi_agent_session(self.fake, reported=less))
        self.assertEqual(c.verdict, "OVERCOUNT")

    def test_copied_history_is_detected(self):
        multi_agent_session(self.fake, sid="s1", reported=EXPECTED)
        fork = self.fake.write_session("s2", [
            {"type": "derived-from", "sessions": [{"type": "claude_code_session", "id": "s1", "via": "fork"}]},
            assistant("r1", OPUS, usage(out=50), [{"type": "text", "text": "copied"}], ts(1, 5)),
            assistant("r9", OPUS, usage(), [{"type": "text", "text": "new"}], ts(9)),
        ])
        c = self._check(fork)
        self.assertEqual(c.shared_calls, 1)
        self.assertFalse(c.clean)
        run = parse_session(fork)
        self.assertEqual(run.derived_from[0]["via"], "fork")


if __name__ == "__main__":
    unittest.main()
