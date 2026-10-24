"""Data sources: public APIs called directly. Every signal goes through an Adapter (adapters.base), which adds
a timeout, a per-source circuit breaker and a last-cached-result fallback:

  geocode          built-in city list -> Open-Meteo geocoding
  current weather  Open-Meteo current conditions
  forecast         Open-Meteo forecast
  disasters        GDACS cyclones/floods and USGS earthquakes
  events/news      GDELT
  company ticker   Alpha Vantage symbol search
  fundamentals     Alpha Vantage OVERVIEW
  news sentiment   Alpha Vantage NEWS_SENTIMENT
  alternates       Geoapify Places

Endpoints verified live on 2026-10-24: Open-Meteo (forecast, current, geocoding), GDACS, USGS, GDELT, Geoapify.
Alpha Vantage field names are from memory of its docs and untested without a key.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

from adapters.base import Adapter, Result

UA = {"User-Agent": "SupplyPulse/0.1 (hackathon prototype)"}

SOURCE_NAMES = {
    "open-meteo": "Open-Meteo", "open-meteo-current": "Open-Meteo", "gdacs": "GDACS", "usgs": "USGS",
    "gazetteer": "built-in city list", "open-meteo-geocoding": "Open-Meteo geocoding", "gdelt": "GDELT news",
    "alphavantage-search": "Alpha Vantage", "alphavantage-overview": "Alpha Vantage",
    "alphavantage-news": "Alpha Vantage", "geoapify": "Geoapify",
}


def source_name(label: str) -> str:
    return SOURCE_NAMES.get(label.replace(" (cached)", ""), label)


weather_adapter = Adapter("weather", ttl_s=1800)
current_adapter = Adapter("current-weather", ttl_s=1800, timeout_s=20)
# GDACS takes 10-25s to answer, so it is refreshed in the background and scans read the cache.
gdacs_adapter = Adapter("gdacs", ttl_s=600, timeout_s=60)
usgs_adapter = Adapter("usgs", ttl_s=300)
geocode_adapter = Adapter("geocode", ttl_s=30 * 86400, timeout_s=12)
events_adapter = Adapter("events", ttl_s=1800, timeout_s=30)
ticker_adapter = Adapter("ticker", ttl_s=30 * 86400, timeout_s=15)
overview_adapter = Adapter("overview", ttl_s=24 * 3600, timeout_s=15)
sentiment_adapter = Adapter("sentiment", ttl_s=12 * 3600, timeout_s=25)
places_adapter = Adapter("places", ttl_s=86400, timeout_s=45)

# Small offline gazetteer so the demo never depends on a geocoder being up.
GAZETTEER = {
    "tiruppur": (11.1085, 77.3411, "IN"), "erode": (11.3410, 77.7172, "IN"),
    "ankleshwar": (21.6264, 73.0152, "IN"), "chennai": (13.0827, 80.2707, "IN"),
    "shenzhen": (22.5431, 114.0579, "CN"), "ningbo": (29.8683, 121.5440, "CN"),
    "mumbai": (19.0760, 72.8777, "IN"), "ho chi minh city": (10.8231, 106.6297, "VN"),
    "dhaka": (23.8103, 90.4125, "BD"), "chittagong": (22.3569, 91.7832, "BD"),
    "surat": (21.1702, 72.8311, "IN"), "ludhiana": (30.9010, 75.8573, "IN"),
    "coimbatore": (11.0168, 76.9558, "IN"), "pune": (18.5204, 73.8567, "IN"),
    "ahmedabad": (23.0225, 72.5714, "IN"), "hanoi": (21.0285, 105.8542, "VN"),
    "da nang": (16.0544, 108.2022, "VN"),
}
# Regions searched for alternate suppliers. Map coverage inside China is poor.
ALT_HUBS = {"IN": ["surat", "ludhiana", "coimbatore", "pune", "ahmedabad"],
            "VN": ["ho chi minh city", "hanoi", "da nang"], "BD": ["dhaka", "chittagong"]}

_gates: dict[tuple, object] = {}


def _loop_local(name: str, factory):
    """asyncio primitives bind to one event loop; keep one per running loop."""
    k = (name, id(asyncio.get_running_loop()))
    if k not in _gates:
        _gates[k] = factory()
    return _gates[k]


async def _get_json(url: str, params: dict | None = None, timeout: float = 15) -> dict:
    async with httpx.AsyncClient(timeout=timeout, headers=UA) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------- geocoding
async def geocode(city: str) -> Result:
    """Returns Result.value = (lat, lon, country_code)."""
    key = city.strip().lower()

    async def gazetteer():
        if key in GAZETTEER:
            return GAZETTEER[key]
        raise KeyError(key)

    async def open_meteo():
        data = await _get_json("https://geocoding-api.open-meteo.com/v1/search", {"name": city, "count": 1})
        top = data["results"][0]
        return (top["latitude"], top["longitude"], top.get("country_code", ""))

    return await geocode_adapter.get(key, [("gazetteer", gazetteer), ("open-meteo-geocoding", open_meteo)])


# ---------------------------------------------------------------- weather
async def get_forecast(lat: float, lon: float) -> Result:
    async def open_meteo():
        return await _get_json("https://api.open-meteo.com/v1/forecast", {
            "latitude": lat, "longitude": lon, "forecast_days": 5, "timezone": "auto",
            "daily": "wind_gusts_10m_max,precipitation_sum,weather_code"})
    return await weather_adapter.get(f"{lat:.2f},{lon:.2f}", [("open-meteo", open_meteo)])


WMO_CONDITIONS = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
                  51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
                  66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
                  77: "snow grains", 80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
                  85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail",
                  99: "severe thunderstorm with hail"}


async def get_current(city: str, lat: float, lon: float) -> Result:
    """Current conditions. value = {"temp_c", "wind_kph", "humidity", "condition"}."""
    async def open_meteo():
        d = (await _get_json("https://api.open-meteo.com/v1/forecast", {
            "latitude": lat, "longitude": lon, "wind_speed_unit": "kmh",
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,weather_code"}))["current"]
        return {"temp_c": d["temperature_2m"], "wind_kph": max(d["wind_speed_10m"], d.get("wind_gusts_10m") or 0),
                "humidity": d["relative_humidity_2m"], "condition": WMO_CONDITIONS.get(d["weather_code"], "unknown")}

    return await current_adapter.get(f"{city.lower()}|{lat:.2f},{lon:.2f}", [("open-meteo-current", open_meteo)])


# ---------------------------------------------------------------- disasters
async def get_gdacs_events(wait_s: float = 8.0) -> Result:
    """Cached events of any age; else wait up to wait_s for the first fetch (GDACS is slow) and carry on without
    it, so the sources that did answer still produce a result. The fetch keeps running and fills the cache."""
    if hit := gdacs_adapter.peek("events"):
        return hit
    task = asyncio.create_task(refresh_gdacs(force=False))
    try:
        return await asyncio.wait_for(asyncio.shield(task), wait_s)
    except asyncio.TimeoutError:
        return Result(False, errors=("gdacs: still fetching",))


async def refresh_gdacs(force: bool = True) -> Result:
    async def gdacs():
        start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        data = await _get_json("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH", {
            "eventlist": "TC;FL", "alertlevel": "Green;Orange;Red", "fromdate": start})
        return data.get("features", [])
    return await gdacs_adapter.get("events", [("gdacs", gdacs)], force=force)


async def get_quakes() -> Result:
    async def usgs():
        data = await _get_json("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson")
        return data.get("features", [])
    return await usgs_adapter.get("4.5_week", [("usgs", usgs)])


# ---------------------------------------------------------------- events (GDELT)
# GDELT allows one request per 5 seconds, so its queries are serialised and cached; a scan waits a bounded
# time and otherwise reports the signal as unavailable while the fetch finishes in the background.
_gdelt_last = 0.0
_events_inflight: dict[str, asyncio.Task] = {}
GDELT_MIN_GAP_S = 5.5
EVENT_TERMS = "(strike OR shutdown OR closure OR congestion OR fire OR flood OR blockade OR explosion OR lockdown)"


async def _gdelt_query(city: str) -> list[dict]:
    global _gdelt_last
    async with _loop_local("gdelt", asyncio.Lock):
        wait = GDELT_MIN_GAP_S - (time.monotonic() - _gdelt_last)
        if wait > 0:
            await asyncio.sleep(wait)
        _gdelt_last = time.monotonic()
        async with httpx.AsyncClient(timeout=25, headers=UA) as c:
            r = await c.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
                "query": f'"{city}" {EVENT_TERMS}', "mode": "artlist", "format": "json",
                "maxrecords": 50, "timespan": "7d"})
    r.raise_for_status()  # 429 when rate-limited
    try:
        arts = r.json().get("articles", [])
    except ValueError as e:  # GDELT answers with plain text on errors / empty results
        if not r.text.strip() or "Please limit requests" in r.text:
            raise RuntimeError("gdelt rate-limited") from e
        return []
    return [{"title": a.get("title", ""), "url": a.get("url", "")} for a in arts]


async def get_events(city: str, wait_s: float = 17.0) -> Result:
    """Articles for a city ({"title", "url"}): cached, else wait up to wait_s while the throttled fetch runs."""
    key = city.lower()
    if (hit := events_adapter.peek(key)) and not hit.stale:
        return hit
    task = _events_inflight.get(key)
    if task is None or task.done():
        task = asyncio.create_task(events_adapter.get(key, [("gdelt", lambda: _gdelt_query(city))]))
        _events_inflight[key] = task
    try:
        return await asyncio.wait_for(asyncio.shield(task), wait_s)
    except asyncio.TimeoutError:
        return events_adapter.peek(key) or Result(False, errors=("events: still fetching",))


# ---------------------------------------------------------------- financial (Alpha Vantage)
def alphavantage_key() -> str | None:
    return os.getenv("ALPHAVANTAGE_API_KEY") or None


async def _av_call(params: dict) -> dict:
    key = alphavantage_key()
    if not key:
        raise RuntimeError("ALPHAVANTAGE_API_KEY not set")
    data = await _get_json("https://www.alphavantage.co/query", {**params, "apikey": key})
    if "Information" in data or "Note" in data:  # quota / rate limit
        raise RuntimeError(data.get("Information") or data.get("Note"))
    return data


async def resolve_ticker(company: str) -> Result:
    """value = ticker symbol."""
    async def alpha():
        matches = (await _av_call({"function": "SYMBOL_SEARCH", "keywords": company})).get("bestMatches") or []
        if not matches:
            raise LookupError(f"no listed company matches {company!r}")
        return matches[0]["1. symbol"]
    return await ticker_adapter.get(company.lower(), [("alphavantage-search", alpha)])


async def get_overview(ticker: str) -> Result:
    """value = dict of Alpha Vantage OVERVIEW fields (strings)."""
    async def alpha():
        data = await _av_call({"function": "OVERVIEW", "symbol": ticker})
        if not data.get("Symbol"):
            raise LookupError("no overview")
        return data
    return await overview_adapter.get(ticker.upper(), [("alphavantage-overview", alpha)])


async def get_sentiment(company: str, ticker: str) -> Result:
    """value = {"score": -1..1, "n": articles, "titles": [...], "urls": [...]}."""
    async def alpha():
        feed = (await _av_call({"function": "NEWS_SENTIMENT", "tickers": ticker, "limit": 50})).get("feed") or []
        rows = [(float(t["ticker_sentiment_score"]), it.get("title", ""), it.get("url", ""))
                for it in feed for t in it.get("ticker_sentiment", []) if t.get("ticker") == ticker]
        if not rows:
            raise LookupError("no ticker sentiment")
        worst = sorted(rows)[:2]
        return {"score": sum(r[0] for r in rows) / len(rows), "n": len(rows),
                "titles": [w[1] for w in worst], "urls": [w[2] for w in worst]}
    return await sentiment_adapter.get(f"{company.lower()}|{ticker}", [("alphavantage-news", alpha)])


# ---------------------------------------------------------------- alternates (Geoapify)
BUSINESS_CATEGORIES = "production,commercial,building.industrial,office.company"


async def places_near(lat: float, lon: float, radius_m: int = 40000, limit: int = 30,
                      name: str | None = None, categories: str = "production.factory") -> Result:
    """Businesses near a point. value = [{"name", "city", "country"}].

    `name` is a partial *name* filter over sparse OpenStreetMap data (verified: "textile" finds
    "Millennium Textile Market"), so a component term only finds businesses whose name contains it.
    """
    async def geoapify():
        key = os.getenv("GEOAPIFY_API_KEY")
        if not key:
            raise RuntimeError("GEOAPIFY_API_KEY not set")
        params = {"categories": categories, "filter": f"circle:{lon},{lat},{radius_m}", "limit": limit, "apiKey": key}
        if name:
            params["name"] = name
        async with _loop_local("places", lambda: asyncio.Semaphore(5)):  # stay under Geoapify's rate limit
            data = await _get_json("https://api.geoapify.com/v2/places", params, timeout=35)
        return [{"name": p.get("name", ""), "city": p.get("city", ""), "country": p.get("country", "")}
                for p in (f.get("properties", {}) for f in data.get("features", []))]

    cache_key = f"{lat:.2f},{lon:.2f},{radius_m},{categories},{(name or '').lower()}"
    return await places_adapter.get(cache_key, [("geoapify", geoapify)])
