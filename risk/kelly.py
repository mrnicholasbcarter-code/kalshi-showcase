"""Portfolio-level fractional Kelly sizing across a candidate pool."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class KellyAllocation:
    domain: str
    ticker: str
    side: str
    kelly_fraction: float
    sized_usd: float


def _per_trade_kelly_fraction(p_win: float, price: float, fee_per_dollar: float = 0.0) -> float:
    """Full-Kelly fraction for a binary bet at given price."""
    if price <= 0 or price >= 1:
        return 0.0
    p_loss = 1.0 - p_win
    b = (1.0 - price) / price  # odds ratio
    edge = p_win * b - p_loss
    if edge <= 0:
        return 0.0
    raw = edge / b
    return max(0.0, raw - fee_per_dollar)


def portfolio_kelly_sizes(
    candidates: list[dict],
    bankroll: float,
    max_portfolio_usd: float,
    kelly_fraction: float = 0.25,
    min_sized_usd: float = 0.5,
    max_trade_usd: float = 5.0,
) -> list[KellyAllocation]:
    """
    Allocate a shared bankroll across candidates using fractional Kelly.

    Each candidate dict needs: domain, ticker, side, p_win, price, fee_per_dollar (optional).
    Returns allocations in descending size order, total capped at max_portfolio_usd.
    """
    if not candidates or bankroll <= 0:
        return []
    budget = min(bankroll, max_portfolio_usd)
    allocations: list[KellyAllocation] = []
    remaining = budget
    for c in candidates:
        if remaining < min_sized_usd:
            break
        p_win = float(c.get("p_win", 0.0))
        price = float(c.get("price", 0.5))
        fee = float(c.get("fee_per_dollar", 0.0))
        frac = _per_trade_kelly_fraction(p_win, price, fee) * kelly_fraction
        sized = min(max_trade_usd, frac * bankroll, remaining)
        if sized < min_sized_usd:
            continue
        remaining -= sized
        allocations.append(KellyAllocation(
            domain=str(c.get("domain", "")),
            ticker=str(c.get("ticker", "")),
            side=str(c.get("side", "YES")),
            kelly_fraction=frac,
            sized_usd=round(sized, 4),
        ))
    return allocations
