"""Drafted emails: ASI:One when a key is set, otherwise a plain template."""
from __future__ import annotations

import os

import httpx


def template(kind: str, s, reason: str, port: str, alternative: str) -> str:
    who = "our company"
    if kind == "inquiry":
        return (f"Subject: Supply inquiry - {s.component}\n\nDear Sir/Madam,\n\n"
                f"{who} is looking for an additional source of {s.component}. We found {alternative} and would like to "
                f"understand whether you can supply this item.\n\nCould you share: minimum order quantity, price range, "
                f"typical lead time{' to ' + port if port else ''}, and available certifications or samples?\n\n"
                f"We would value a short call this week.\n\nBest regards,\n{who}")
    return (f"Subject: Order status and shipment timing - {s.component}\n\nDear team at {s.name},\n\n"
            f"We are reviewing shipment timing for {s.component} from {s.city}. We are seeing reports that may affect "
            f"your area ({reason or 'local disruption risk'}).\n\nCould you confirm: (1) current stock and production "
            f"status for our open orders, (2) any expected delay, and (3) whether you can ship any part of the order "
            f"early or via another route?\n\nPlease reply by end of day tomorrow so we can plan accordingly.\n\n"
            f"Best regards,\n{who}")


async def draft_email(kind: str, s, reason: str, evidence: list[str], port: str,
                      alternative: str = "") -> tuple[str, str]:
    """Returns (text, method). Falls back to the template if the LLM call fails."""
    key = os.getenv("ASI1_API_KEY")
    fallback = template(kind, s, reason, port, alternative)
    if not key:
        return fallback, "template"
    purpose = ("an inquiry to a potential alternate supplier" if kind == "inquiry"
               else "a polite, factual delay-risk inquiry to the current supplier")
    if kind == "inquiry":
        # Never tell a prospective supplier about our current supplier or why we are looking.
        facts = (f"Component needed: {s.component}. Recipient: {alternative}."
                 + (f" We ship through {port}." if port else "")
                 + " We are qualifying additional sources. Do not mention any current supplier, any disruption or any event.")
    else:
        facts = f"Component: {s.component}. Supplier: {s.name}, {s.city}. Risk: {reason}. Evidence: {'; '.join(evidence[:3])}."
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.asi1.ai/v1/chat/completions",
                             headers={"Authorization": f"Bearer {key}"},
                             json={"model": "asi1", "max_tokens": 1500, "messages": [
                                 {"role": "system", "content": "You write short, professional business emails "
                                  "(under 150 words) with a Subject line. Use only the facts given; never invent "
                                  "quantities, prices or dates."},
                                 {"role": "user", "content": f"Write {purpose}. {facts}"}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip(), "asi1"
    except Exception:  # noqa: BLE001
        return fallback, "template"
