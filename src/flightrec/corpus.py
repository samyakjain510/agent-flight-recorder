"""Facts that need more than one session file.

Claude Code copies history into a new file when a session is forked, resumed
or continued. The same model call then appears in several files. This index
lets a run say which of its calls also live elsewhere, so copied history is
never counted as new spend.

Ownership rule: a shared call belongs to the file that was created first. A
fork or resume file is always created after the file it copies from. On macOS
the file's birth time is used; elsewhere, its first timestamp, then its name.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Dict, Optional, Set, Tuple

from .claude_code import find_sessions, session_id_of, subagent_files
from .model import Run


class Corpus:
    def __init__(self, root: Optional[str] = None):
        self.calls: Dict[str, Set[str]] = defaultdict(set)  # request id -> session ids
        self.created: Dict[str, Tuple[float, str, str]] = {}  # session id -> sort key
        for path in find_sessions(root):
            sid = session_id_of(path)
            first_ts = ""
            for p in [path] + subagent_files(path):
                with open(p, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if not first_ts and '"timestamp"' in line:
                            try:
                                first_ts = str(json.loads(line).get("timestamp") or "")
                            except json.JSONDecodeError:
                                pass
                        if '"requestId"' not in line:
                            continue
                        try:
                            r = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if r.get("type") == "assistant" and r.get("requestId"):
                            self.calls[r["requestId"]].add(sid)
            st = os.stat(path)
            self.created[sid] = (getattr(st, "st_birthtime", 0.0), first_ts, sid)

    def owner(self, request_id: str) -> Optional[str]:
        sids = self.calls.get(request_id)
        if not sids:
            return None
        return min(sids, key=lambda s: self.created.get(s, (0.0, "", s)))

    def mark(self, run: Run) -> None:
        for req in run.requests():
            sids = self.calls.get(req.id, set())
            req.shared_with = set(sids) - {run.session_id}
            owner = self.owner(req.id)
            req.owned = owner is None or owner == run.session_id


def request_index(root: Optional[str] = None) -> Corpus:
    """Kept for older callers: returns the Corpus."""
    return Corpus(root)


def mark_shared(run: Run, corpus: Corpus) -> None:
    corpus.mark(run)
