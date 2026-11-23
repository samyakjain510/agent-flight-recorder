"""Small formatting helpers for terminal output."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .model import Usage


def tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def usage_line(u: Usage) -> str:
    return (
        f"{tokens(u.total)} tokens (fresh in {tokens(u.input)} · cache write {tokens(u.cache_write)}"
        f" · cache read {tokens(u.cache_read)} · out {tokens(u.output)})"
    )


def duration(start: Optional[datetime], end: Optional[datetime]) -> str:
    if not start or not end:
        return "?"
    return span(end - start)


def span(d: timedelta) -> str:
    s = int(d.total_seconds())
    if s < 0:
        return "?"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60:02d}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


def when(ts: Optional[datetime]) -> str:
    return ts.astimezone().strftime("%Y-%m-%d %H:%M") if ts else "?"


def text(value: str, redact: bool, limit: int = 70) -> str:
    if redact:
        return f"<{len(value)} chars>" if value else ""
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"
