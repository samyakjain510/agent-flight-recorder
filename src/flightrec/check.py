"""Cross-check our token counts against Claude Code's own totals.

Claude Code keeps a running total per session (the cost-state record). A
"clean" session shares no calls with other files, was never compacted, and is
not still running. For a clean session the rule is:

  * we may count LESS than Claude Code, because some calls never reach the
    transcript (side calls on other models, and a few small same-model calls
    whose purpose we have not identified);
  * we must never count MORE. That would mean the reader double-counts.

For other sessions we report the gap and the reason.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model import Run, Usage

FIELDS = {
    "input": "inputTokens",
    "cache_write": "cacheCreationInputTokens",
    "cache_read": "cacheReadInputTokens",
    "output": "outputTokens",
}
LIVE_SECONDS = 15 * 60


def reported_by_model(run: Run) -> Dict[str, Usage]:
    totals = (run.reported_totals or {}).get("modelUsage") or {}
    out = {}
    for model, d in totals.items():
        if isinstance(d, dict):
            out[model] = Usage(**{ours: int(d.get(theirs) or 0) for ours, theirs in FIELDS.items()})
    return out


@dataclass
class ModelCheck:
    model: str
    ours: Optional[Usage]
    theirs: Optional[Usage]

    @property
    def status(self) -> str:
        if self.ours is None:
            return "not in transcript"
        if self.theirs is None:
            return "not in Claude Code's totals"
        if self.ours.core() == self.theirs.core():
            return "match"
        if all(o <= t for o, t in zip(self.ours.core(), self.theirs.core())):
            return "transcript missing calls"
        return "overcount"

    def missing(self) -> Usage:
        """Tokens Claude Code counted that the transcript does not show."""
        if self.ours is None or self.theirs is None:
            return Usage()
        return Usage(*(max(t - o, 0) for o, t in zip(self.ours.core(), self.theirs.core())))


@dataclass
class SessionCheck:
    run: Run
    models: List[ModelCheck]
    shared_calls: int
    compactions: int
    live: bool
    reasons: List[str] = field(default_factory=list)

    @property
    def has_totals(self) -> bool:
        return self.run.reported_totals is not None

    @property
    def clean(self) -> bool:
        return self.has_totals and not self.shared_calls and not self.compactions and not self.live

    @property
    def transcript_models_match(self) -> bool:
        seen = [m for m in self.models if m.ours is not None]
        return bool(seen) and all(m.status == "match" for m in seen)

    @property
    def side_models(self) -> List[ModelCheck]:
        return [m for m in self.models if m.status == "not in transcript"]

    @property
    def verdict(self) -> str:
        if not self.has_totals:
            return "no totals recorded"
        if not any(m.ours is not None for m in self.models):
            return "no model calls"
        if self.transcript_models_match:
            return "match"
        if not self.clean:
            return "differs (explained)"
        statuses = {m.status for m in self.models if m.ours is not None}
        if "overcount" in statuses or "not in Claude Code's totals" in statuses:
            return "OVERCOUNT"
        return "transcript missing calls"


def check_run(run: Run, now: Optional[float] = None) -> SessionCheck:
    now = time.time() if now is None else now
    ours = run.usage_by_model()
    theirs = reported_by_model(run)
    models = [ModelCheck(m, ours.get(m), theirs.get(m)) for m in sorted(set(ours) | set(theirs))]
    shared = sum(1 for r in run.requests() if r.shared_with)
    live = (now - os.path.getmtime(run.path)) < LIVE_SECONDS if os.path.exists(run.path) else False
    result = SessionCheck(run=run, models=models, shared_calls=shared, compactions=len(run.compactions), live=live)
    if shared:
        others = {s for r in run.requests() for s in r.shared_with}
        result.reasons.append(f"{shared} calls also in {len(others)} other file(s) (fork/resume copies)")
    if run.compactions:
        result.reasons.append(f"{len(run.compactions)} compaction(s); summarizing calls are not in the transcript")
    if live:
        result.reasons.append("still running; Claude Code's totals lag")
    return result
