"""Deterministic risk model: Risk = Likelihood x Impact. No LLM involved."""
from __future__ import annotations

WEIGHTS = {"hazard": 0.45, "events": 0.25, "financial": 0.10, "concentration": 0.20}
CRITICALITY = {"low": 0.4, "medium": 0.7, "high": 1.0}
AMBER_AT = 0.20
RED_AT = 0.50


def concentration_signal(supplier, all_suppliers) -> float:
    """Single-source items and suppliers sharing a city raise likelihood."""
    base = 0.2 if supplier.has_backup else 0.7
    shared = any(
        o.id != supplier.id and o.city.lower() == supplier.city.lower()
        for o in all_suppliers
    )
    return min(1.0, base + (0.3 if shared else 0.0))


def combine_likelihood(signals: dict[str, float | None]) -> tuple[float | None, list[str]]:
    """Weighted mean of the signals that are available; returns (value, missing).

    Unavailable signals are excluded and weights renormalised, but they are
    reported in `missing` so a gap is never presented as low risk.
    """
    missing = [k for k, v in signals.items() if v is None]
    present = {k: v for k, v in signals.items() if v is not None}
    if not present:
        return None, missing
    total = sum(WEIGHTS[k] for k in present)
    return sum(WEIGHTS[k] * v for k, v in present.items()) / total, missing


def impact(supplier) -> float:
    crit = CRITICALITY.get(supplier.criticality, 0.7)
    backup = 0.6 if supplier.has_backup else 1.0
    ratio = supplier.lead_time_days / max(supplier.stock_days, 1)
    exposure = min(max(ratio, 0.25), 1.5) / 1.5  # replacing supply slower than stock -> high
    return crit * backup * (0.3 + 0.7 * exposure)


def band(risk: float | None) -> str:
    if risk is None:
        return "unknown"
    return "red" if risk >= RED_AT else "amber" if risk >= AMBER_AT else "green"


def score_supplier(supplier, signals: dict[str, float | None]) -> dict:
    lik, missing = combine_likelihood(signals)
    imp = impact(supplier)
    # Without any external signal (hazard/events) we only know the user's own data.
    external = [signals.get("hazard"), signals.get("events")]
    if lik is None or all(s is None for s in external):
        return {"likelihood": lik, "impact": imp, "risk": None, "band": "unknown", "missing": missing}
    risk = lik * imp
    return {"likelihood": lik, "impact": imp, "risk": risk, "band": band(risk), "missing": missing}


def overall(scores: list[dict]) -> tuple[float | None, str]:
    known = [s["risk"] for s in scores if s["risk"] is not None]
    if not known:
        return None, "unknown"
    value = 0.6 * max(known) + 0.4 * (sum(known) / len(known))
    return value, band(value)
