"""Financial stress (0-1) from two inputs: news sentiment and weakening fundamentals."""
from __future__ import annotations

NEUTRAL_OR_BETTER = 0.15   # sentiment at/above this -> no stress
VERY_NEGATIVE = -0.35      # sentiment at/below this -> maximum stress


def sentiment_to_signal(score: float) -> float:
    span = NEUTRAL_OR_BETTER - VERY_NEGATIVE
    return min(1.0, max(0.0, (NEUTRAL_OR_BETTER - score) / span))


def _num(overview: dict, key: str) -> float | None:
    try:
        return float(overview[key])
    except (KeyError, TypeError, ValueError):
        return None  # Alpha Vantage uses "None" / "-" for missing values


def fundamentals_signal(overview: dict) -> tuple[float | None, list[str]]:
    """Stress from the Company Overview fields: shrinking earnings or revenue, losses, and sell-side ratings.
    Returns (signal | None when no usable field, evidence)."""
    parts: list[float] = []
    notes: list[str] = []
    earn = _num(overview, "QuarterlyEarningsGrowthYOY")
    if earn is not None:
        parts.append(min(1.0, max(0.0, -earn / 0.5)))  # -50% or worse -> full stress
        if earn < 0:
            notes.append(f"quarterly earnings are down {abs(earn) * 100:.0f}% on last year")
    rev = _num(overview, "QuarterlyRevenueGrowthYOY")
    if rev is not None:
        parts.append(min(1.0, max(0.0, -rev / 0.3)))
        if rev < 0:
            notes.append(f"quarterly revenue is down {abs(rev) * 100:.0f}% on last year")
    margin = _num(overview, "ProfitMargin")
    if margin is not None:
        parts.append(1.0 if margin < 0 else 0.0)
        if margin < 0:
            notes.append("the company is currently loss-making")
    ratings = [_num(overview, k) or 0 for k in ("AnalystRatingStrongBuy", "AnalystRatingBuy", "AnalystRatingHold",
                                                "AnalystRatingSell", "AnalystRatingStrongSell")]
    if sum(ratings) >= 5:
        sell_share = (ratings[3] + ratings[4]) / sum(ratings)
        parts.append(min(1.0, sell_share / 0.5))
        if sell_share >= 0.3:
            notes.append(f"{sell_share * 100:.0f}% of analysts rate it sell")
    if not parts:
        return None, []
    return sum(parts) / len(parts), (["Financial health: " + "; ".join(notes)] if notes else [])


def combine(sentiment: float | None, fundamentals: float | None) -> float | None:
    parts = [p for p in (sentiment, fundamentals) if p is not None]
    return sum(parts) / len(parts) if parts else None
