"""Tests for scripts/scrub_transcripts.py, which prepares transcripts for a public demo.

Run:  python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import scrub_transcripts as scrub  # noqa: E402


class ScrubTests(unittest.TestCase):
    def test_records_flightrec_does_not_read_are_dropped(self):
        self.assertIsNone(scrub.scrub_record({"type": "api-request", "params": {"system": "..."}}))
        self.assertIsNone(scrub.scrub_record({"type": "some-new-type"}))

    def test_account_ids_and_private_attachments_removed(self):
        r = scrub.scrub_record({"type": "attachment", "accountUuid": "acc", "organizationUuid": "org",
                                "attachment": {"type": "session_context", "context": {"userEmail": "me@school.edu"}}})
        self.assertNotIn("accountUuid", r)
        self.assertNotIn("organizationUuid", r)
        self.assertEqual(r["attachment"], {"type": "session_context", "scrubbed": True})
        kept = scrub.scrub_record({"type": "attachment", "attachment": {"type": "queued_command",
                                                                         "origin": {"kind": "peer", "from": "a1"}}})
        self.assertEqual(kept["attachment"]["origin"]["from"], "a1")  # hand-backs survive

    def test_text_replacements_and_leak_check(self):
        pairs = [("/Users/alex", "/home/demo"), ("alex", "demo")]
        out = scrub.scrub_text('{"cwd":"/Users/alex/code","note":"mail alex@school.edu or noreply@anthropic.com"}', pairs)
        self.assertEqual(json.loads(out)["cwd"], "/home/demo/code")
        self.assertIn("user@example.com", out)
        self.assertIn("noreply@anthropic.com", out)
        self.assertEqual(scrub.leaks(out, ["alex", "/Users/alex"]), {})
        self.assertEqual(scrub.leaks("contact a@b.org", []), {"<email address>": 1})


if __name__ == "__main__":
    unittest.main()
