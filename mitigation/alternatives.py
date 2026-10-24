"""Alternate-supplier search.

Honest limits, stated in the output as well:
- Map data (Geoapify/OpenStreetMap) is sparse for small suppliers, so name-matched results are often few.
- Coverage inside China is poor; Chinese hubs appear only as leads, never as searched results.
- Results are businesses near a hub, not verified suppliers of the component.
Only places whose NAME matches a component term are listed (generic factories were tried and dropped: a gas
bottling plant is not an alternate zipper supplier). Unverified AI/static leads (hubs, platforms) follow.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from adapters import sources
from adapters.base import friendly_reason
from mitigation.guidance import get_guidance
from scoring.hazard import haversine_km

MIN_DISTANCE_KM = 300  # an alternative must be in a different region from the failing supplier
MAX_HUBS = 3
MAX_SUGGESTED_HUBS = 2
NAME_RADIUS_M = 60000
MAX_MATCHED = 5
MAX_HUB_TERMS = 3
CHINA = {"china", "cn", "hong kong", "prc"}

Hub = tuple  # (name, lat, lon, country_code, is_standard_hub)


@dataclass
class Found:
    matched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    leads: dict = field(default_factory=dict)
    via: set = field(default_factory=set)  # names of the data sources that answered

    @property
    def candidates(self) -> list[str]:
        """Numbered for `inquire N`."""
        return self.matched


def static_hubs(supplier, port) -> list[Hub]:
    """Standard hubs in India/Vietnam/Bangladesh at least MIN_DISTANCE_KM from the supplier, nearest the port first."""
    ref = (port.lat, port.lon) if port else (supplier.lat, supplier.lon)
    hubs = []
    for cc, names in sources.ALT_HUBS.items():
        for n in names:
            lat, lon, _ = sources.GAZETTEER[n]
            if supplier.lat is not None and haversine_km(supplier.lat, supplier.lon, lat, lon) < MIN_DISTANCE_KM:
                continue
            hubs.append((n.title(), lat, lon, cc, True))
    hubs.sort(key=lambda h: haversine_km(ref[0], ref[1], h[1], h[2]))
    return hubs


async def pick_hubs(supplier, port, suggested: list[dict]) -> tuple[list[Hub], list[str]]:
    """Suggested hubs first (located, outside China, in another region), then standard ones."""
    notes: list[str] = []
    searchable = []
    for h in suggested:
        if h.get("country", "").strip().lower() in CHINA:
            notes.append(f"{h['city']} (China) is a good area to look, but map data there is too poor to search; "
                         "use a web search or sourcing platform.")
        else:
            searchable.append(h)
    geocoded = await asyncio.gather(*(sources.geocode(h["city"]) for h in searchable[:MAX_HUBS]))
    chosen: list[Hub] = []
    seen: set[str] = set()
    for h, g in zip(searchable, geocoded):
        if not g.ok or len(chosen) >= MAX_SUGGESTED_HUBS:
            continue
        lat, lon, cc = g.value
        if cc == "CN" or h["city"].lower() in seen:
            continue
        if supplier.lat is not None and haversine_km(supplier.lat, supplier.lon, lat, lon) < MIN_DISTANCE_KM:
            continue
        seen.add(h["city"].lower())
        chosen.append((h["city"], lat, lon, cc, False))
    for hub in static_hubs(supplier, port):
        if len(chosen) >= MAX_HUBS:
            break
        if hub[0].lower() not in seen:
            seen.add(hub[0].lower())
            chosen.append(hub)
    return chosen, notes


def name_matches(name: str, term: str) -> bool:
    """Whole-word match (plural allowed), so 'fabric' does not match 'Fabrication'."""
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?:s|es)?(?!\w)", name, re.I) is not None


def _describe(p: dict, hub: str, cc: str, tag: str) -> str:
    return f"{p['name'].strip()} — {p.get('city') or hub}, {p.get('country') or cc} ({tag})"


async def _search_hub(hub: Hub, terms: list[str]):
    _, lat, lon, _, _ = hub
    return await asyncio.gather(*(sources.places_near(lat, lon, NAME_RADIUS_M, 10, term, sources.BUSINESS_CATEGORIES)
                                  for term in terms[:MAX_HUB_TERMS]))


async def find_alternatives(supplier, port) -> Found:
    found = Found()
    if supplier.country == "CN":
        found.notes.append("Map-data coverage inside China is poor; for Chinese alternatives use a web search "
                           "or a sourcing platform. Results below are outside China.")
    guidance = await get_guidance(supplier.component)
    found.leads = guidance
    terms = guidance["terms"]
    hubs, hub_notes = await pick_hubs(supplier, port, guidance["hubs"])
    found.notes += hub_notes
    searched = await asyncio.gather(*(_search_hub(h, terms) for h in hubs))
    seen: set[str] = set()
    failed: list = []
    for hub, named in zip(hubs, searched):
        for term, r in zip(terms, named):
            if not r.ok:
                failed.append(r)
                continue
            found.via.add(sources.source_name(r.source))
            for p in r.value:
                nm = (p.get("name") or "").strip()
                if nm and nm.lower() not in seen and len(found.matched) < MAX_MATCHED and name_matches(nm, term):
                    seen.add(nm.lower())
                    found.matched.append(_describe(p, hub[0], hub[3], f"name matches '{term}'"))
    if failed:
        found.notes.append(f"{len(failed)} map searches couldn't be completed "
                           f"({friendly_reason([e for r in failed for e in r.errors])})")
    return found
