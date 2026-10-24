"""Plain-language reasons for a supplier's colour. Deterministic: every sentence traces to the inputs."""
from __future__ import annotations

NO_EVENT_TEXTS = ("No weather warnings", "No strike, closure")


def exposure_sentence(s) -> str:
    backup = "you have a backup supplier" if s.has_backup else "it's your only supplier for this item"
    if s.lead_time_days > s.stock_days:
        stock = (f"your {s.stock_days} days of stock would run out before a replacement could arrive "
                 f"(about {s.lead_time_days} days)")
    else:
        stock = f"your {s.stock_days} days of stock covers the roughly {s.lead_time_days} days a replacement would take"
    return f"{backup[0].upper()}{backup[1:]}, and {stock}."


def reason(s, band: str, top_evidence: str | None) -> str:
    """top_evidence: the strongest *real* finding (not a 'nothing found' message), if any."""
    if band == "unknown":
        return "Not enough live data to judge this one. Try `scan` again in a few minutes."
    if band == "green":
        return "No significant risk found. " + exposure_sentence(s)
    if top_evidence:
        return top_evidence.rstrip(".") + ". " + exposure_sentence(s)
    return "No current event found, but you're exposed if something happens. " + exposure_sentence(s)


def is_real_finding(text: str) -> bool:
    return not text.startswith(NO_EVENT_TEXTS)
