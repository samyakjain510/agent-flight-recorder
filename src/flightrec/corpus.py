"""Facts that need more than one session file.

Claude Code copies history into a new file when a session is forked, resumed
or continued. The same model call then appears in several files. This index
lets a run say which of its calls also live elsewhere, so copied history is
never counted as new spend.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Dict, Optional, Set

from .claude_code import find_sessions, session_id_of, subagent_files
from .model import Run


def request_index(root: Optional[str] = None) -> Dict[str, Set[str]]:
    """Map each model call (request id) to the session files that contain it."""
    index: Dict[str, Set[str]] = defaultdict(set)
    for path in find_sessions(root):
        sid = session_id_of(path)
        for p in [path] + subagent_files(path):
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"requestId"' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("type") == "assistant" and r.get("requestId"):
                        index[r["requestId"]].add(sid)
    return index


def mark_shared(run: Run, index: Dict[str, Set[str]]) -> None:
    for req in run.requests():
        req.shared_with = set(index.get(req.id, set())) - {run.session_id}
