"""Turn GDELT news headlines (last 7 days) into a 0-1 event signal: strikes, closures, fires...

Article = {"title", "url"}. Only headlines that mention the place and an event keyword count.
"""
from __future__ import annotations

KEYWORDS = {
    "strike": 0.7, "walkout": 0.6, "lockout": 0.7, "shutdown": 0.8, "shut down": 0.8,
    "closure": 0.7, "closed": 0.5, "blockade": 0.8, "congestion": 0.6, "backlog": 0.5,
    "fire": 0.8, "explosion": 0.8, "blast": 0.7, "collapse": 0.7, "flood": 0.7,
    "cyclone": 0.7, "typhoon": 0.7, "power cut": 0.6, "curfew": 0.6, "protest": 0.4,
    "suspended": 0.5, "halt": 0.5, "lockdown": 0.8,
}
MAX_EVIDENCE = 3


def event_signal(articles: list[dict], place: str) -> tuple[float, list[str], list[str]]:
    """Only articles that mention the place and an event keyword count. Several independent articles raise
    the score; a single stray headline cannot reach red on its own."""
    hits = []
    for a in articles or []:
        title = a.get("title") or ""
        text = title.lower()
        if place.lower() not in text:
            continue
        weights = [w for k, w in KEYWORDS.items() if k in text]
        if weights:
            hits.append((max(weights), title, a.get("url", "")))
    if not hits:
        return 0.0, [], []
    hits.sort(reverse=True)
    top = hits[0][0]
    corroboration = min(len(hits) - 1, 3) * 0.1
    signal = min(1.0, 0.6 * top + corroboration + (0.1 if top >= 0.7 else 0))
    return signal, [t for _, t, _ in hits[:MAX_EVIDENCE] if t], [u for _, _, u in hits[:MAX_EVIDENCE] if u]
