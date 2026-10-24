"""Turn raw weather / disaster data into a 0-1 hazard signal plus plain-language evidence."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

ALERT_SCORE = {"green": 0.4, "orange": 0.7, "red": 1.0}
GDACS_RADIUS_KM = {"TC": 600, "FL": 300}
QUAKE_RADIUS_KM = 300
EVENT_NAMES = {"TC": "tropical cyclone", "FL": "flood"}
# words in a current-conditions description (Open-Meteo) and how worrying they are
SEVERE_CONDITIONS = {"hurricane": 1.0, "typhoon": 1.0, "cyclone": 1.0, "tornado": 1.0, "violent": 0.7,
                     "blizzard": 0.8, "thunderstorm": 0.6, "thunder": 0.6, "storm": 0.6, "heavy rain": 0.6,
                     "flood": 0.7, "hail": 0.5, "heavy snow": 0.5, "freezing rain": 0.4, "fog": 0.2}


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def gust_score(kmh: float) -> float:
    for limit, s in ((40, 0.0), (60, 0.2), (90, 0.5), (120, 0.8)):
        if kmh < limit:
            return s
    return 1.0


def rain_score(mm_per_day: float) -> float:
    for limit, s in ((25, 0.0), (50, 0.3), (100, 0.6), (200, 0.8)):
        if mm_per_day < limit:
            return s
    return 1.0


def severity_word(score: float) -> str:
    return "Severe" if score >= 0.6 else "Strong" if score >= 0.3 else "Mild"


def _weather(forecast: dict | None, place: str):
    """Only conditions worth mentioning (score > 0) produce evidence."""
    if not forecast:
        return []
    daily = forecast.get("daily", {})
    gusts = [g for g in daily.get("wind_gusts_10m_max", []) if g is not None]
    rain = [r for r in daily.get("precipitation_sum", []) if r is not None]
    out = []
    if gusts and gust_score(max(gusts)) > 0:
        s = gust_score(max(gusts))
        out.append((s, f"{severity_word(s)} winds forecast near {place} (gusts up to {max(gusts):.0f} km/h)"))
    if rain and rain_score(max(rain)) > 0:
        s = rain_score(max(rain))
        out.append((s, f"{severity_word(s)} rain forecast near {place} (up to {max(rain):.0f} mm in a day)"))
    return out


def current_score(current: dict | None) -> float:
    if not current:
        return 0.0
    cond = str(current.get("condition", "")).lower()
    words = [w for k, w in SEVERE_CONDITIONS.items() if k in cond]
    return max([gust_score(float(current.get("wind_kph") or 0))] + words)


def _current(current: dict | None, place: str, source: str):
    s = current_score(current)
    if s <= 0:
        return []
    return [(s, f"Right now in {place}: {str(current.get('condition', '')).lower()}, "
                f"wind {float(current.get('wind_kph') or 0):.0f} km/h (via {source})")]


def _gdacs(features: list[dict], lat, lon, place: str, now: datetime):
    out = []
    for f in features or []:
        p = f.get("properties", {})
        etype = p.get("eventtype")
        if etype not in GDACS_RADIUS_KM:
            continue
        try:
            end = datetime.fromisoformat(p.get("todate", "")).replace(tzinfo=timezone.utc)
            if end < now - timedelta(days=1):
                continue  # event already over
        except ValueError:
            pass
        coords = f.get("geometry", {}).get("coordinates")
        if not coords:
            continue
        d = haversine_km(lat, lon, coords[1], coords[0])
        if d > GDACS_RADIUS_KM[etype]:
            continue
        level = str(p.get("alertlevel", "")).lower()
        score = ALERT_SCORE.get(level, 0.4) * (1 - 0.4 * d / GDACS_RADIUS_KM[etype])
        name = p.get("eventname") or p.get("name")
        out.append((score, f"{str(p.get('alertlevel')).title()}-level alert: {EVENT_NAMES[etype]} {name} "
                           f"is {d:.0f} km from {place}", (p.get("url") or {}).get("report")))
    return out


def _quakes(features: list[dict], lat, lon, place: str):
    out = []
    for f in features or []:
        p = f.get("properties", {})
        coords = f.get("geometry", {}).get("coordinates")
        mag = p.get("mag")
        if not coords or mag is None or mag < 5.0:
            continue
        d = haversine_km(lat, lon, coords[1], coords[0])
        if d > QUAKE_RADIUS_KM:
            continue
        score = min(1.0, 0.3 + 0.2 * (mag - 5.0)) * (1 - 0.5 * d / QUAKE_RADIUS_KM)
        out.append((score, f"Magnitude {mag:.1f} earthquake {d:.0f} km from {place} ({p.get('place')})", p.get("url")))
    return out


def hazard_signal(places: list[tuple[str, float, float]], forecasts: list[dict | None],
                  gdacs: list[dict] | None, quakes: list[dict] | None,
                  now: datetime | None = None, currents: list[tuple[dict | None, str]] | None = None):
    """places: [(label, lat, lon)], forecasts aligned with places.
    currents: optional [(current-conditions dict | None, source name)] aligned with places.

    Returns (signal | None, evidence, sources). Signal is None only when *no* source returned data at all
    (so absence of data is not read as safety).
    """
    now = now or datetime.now(timezone.utc)
    currents = currents or [(None, "")] * len(places)
    if not any(forecasts) and not any(c for c, _ in currents) and gdacs is None and quakes is None:
        return None, [], []
    scored: list[tuple[float, str, str | None]] = []
    for (label, lat, lon), fc, (cur, cur_src) in zip(places, forecasts, currents):
        scored += [(s, e, None) for s, e in _weather(fc, label)]
        scored += [(s, e, None) for s, e in _current(cur, label, cur_src)]
        scored += _gdacs(gdacs or [], lat, lon, label, now)
        scored += _quakes(quakes or [], lat, lon, label)
    if not scored:
        return 0.0, ["No weather warnings, disaster alerts or earthquakes found nearby"], []
    scored.sort(key=lambda t: t[0], reverse=True)
    top = [t for t in scored if t[0] > 0][:3]
    signal = scored[0][0]
    evidence = [t[1] for t in top] or ["No weather warnings, disaster alerts or earthquakes found nearby"]
    sources = [t[2] for t in top if t[2]]
    return signal, evidence, sources
