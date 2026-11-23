"""API list prices, used to weight token types fairly.

On a flat Pro or Max plan nobody pays these per call. They answer "what would
this work cost on the API", which is the fair way to compare a cheap cache
read with an expensive output token.

Prices are US dollars per million tokens from Anthropic's published list
(Nov 2026). Checked against Claude Code's own cost records on Samyak's machine
(docs/learn/02-what-a-token-costs.md): they reproduce its recorded cost to the
cent in 37 of 38 Opus 5.5 records that split cache writes by duration, 5 of 7
Sonnet 5.5, 2 of 2 Fable 5.2, and every Haiku record.
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from .model import Request, Usage

# fresh input, 5-minute cache write, 1-hour cache write, cache read, output
PRICES = {
    "claude-fable-5-2": (8.00, 10.00, 16.00, 0.20, 40.00),
    "claude-opus-5-5": (4.00, 5.00, 8.00, 0.20, 20.00),
    "claude-sonnet-5-5": (2.00, 2.50, 4.00, 0.10, 10.00),
    "claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),  # 1-hour write not seen in records; assumes the usual 2x
    "claude-haiku-4-5-20251001": (1.00, 1.25, 2.00, 0.10, 5.00),
}
WEB_SEARCH_PER_REQUEST = 0.01
FAST_MODE_MULTIPLIER = {"claude-opus-5-5": 2.0}


def usage_cost(u: Usage, model: str, speed: Optional[str] = None) -> Optional[float]:
    p = PRICES.get(model)
    if p is None:
        return None
    w1h = min(u.cache_write_1h, u.cache_write)
    w5m = u.cache_write - w1h
    cost = (u.input * p[0] + w5m * p[1] + w1h * p[2] + u.cache_read * p[3] + u.output * p[4]) / 1e6
    cost += u.web_searches * WEB_SEARCH_PER_REQUEST
    if speed == "fast":
        cost *= FAST_MODE_MULTIPLIER.get(model, 1.0)
    return cost


def request_cost(req: Request) -> Optional[float]:
    return usage_cost(req.usage, req.model, req.speed)


def total_cost(requests: Iterable[Request]) -> Tuple[float, int]:
    """Summed cost, plus how many calls had a model with no known price."""
    total, unknown = 0.0, 0
    for r in requests:
        c = request_cost(r)
        if c is None:
            unknown += 1
        else:
            total += c
    return total, unknown


def cache_read_price(model: str) -> Optional[float]:
    p = PRICES.get(model)
    return p[3] if p else None
