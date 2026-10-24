"""Sourcing guidance for a component: search terms, hubs and platforms.

With ASI1_API_KEY the LLM proposes these from general knowledge (labelled unverified in
the output); otherwise a static synonym map and generic platform list are used. The
terms drive real map searches; hubs and platforms are leads for the user, never facts.
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

# component keyword -> name-search terms that OpenStreetMap-style place names tend to contain
SYNONYMS = {
    "zipper": ["zip", "zipper", "fastener"], "button": ["button", "garment accessor", "trims"],
    "fabric": ["textile", "weaving", "cloth", "mill"], "cloth": ["textile", "cloth", "weaving"],  # not "fabric": matches metal fabrication
    "yarn": ["yarn", "spinning", "thread"], "thread": ["thread", "yarn", "spinning"],
    "dye": ["dye", "dyeing", "chemical", "pigment"], "label": ["label", "printing", "tags"],
    "packag": ["packaging", "carton", "box", "corrugat"], "plastic": ["plastic", "polymer", "moulding"],
    "steel": ["steel", "metal", "forging"], "electronic": ["electronic", "circuit", "components"],
    "leather": ["leather", "tannery"], "rubber": ["rubber", "polymer"],
}
TOO_GENERIC = {"colour", "colours", "color", "colors", "company", "industries", "trading", "limited", "enterprise",
               "enterprises", "works", "factory"}  # match bakeries, shops and everything else
GENERIC_PLATFORMS = ["IndiaMART", "TradeIndia", "Alibaba", "Global Sources", "ThomasNet"]
MAX_TERMS = 4

log = logging.getLogger("supplypulse.guidance")
_cache: dict[str, dict] = {}


def static_terms(component: str) -> list[str]:
    low = component.lower()
    terms: list[str] = []
    for key, syn in SYNONYMS.items():
        if key in low:
            terms += syn
    if not terms:  # unknown component: fall back to its own words
        terms = re.findall(r"[a-z]{4,}", low)
    return list(dict.fromkeys(terms))[:MAX_TERMS]


def static_guidance(component: str) -> dict:
    return {"terms": static_terms(component), "hubs": [], "platforms": GENERIC_PLATFORMS[:3], "source": "static"}


def parse_guidance(text: str, component: str) -> dict | None:
    """Parse the LLM reply; reject anything malformed instead of guessing."""
    try:
        start = text.index("{")
        data, _ = json.JSONDecoder().raw_decode(text[start:])
        terms = [t.strip().lower() for t in data["terms"] if isinstance(t, str) and 2 < len(t) < 30]
        terms = [t for t in terms if t not in TOO_GENERIC]
        hubs = [{"city": h["city"].strip(), "country": str(h.get("country", "")).strip()}
                for h in data.get("hubs", []) if isinstance(h, dict) and h.get("city")]
        platforms = [p.strip() for p in data.get("platforms", []) if isinstance(p, str)][:4]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if not terms:
        return None
    return {"terms": list(dict.fromkeys(terms + static_terms(component)))[:MAX_TERMS],
            "hubs": hubs[:4], "platforms": platforms or GENERIC_PLATFORMS[:3], "source": "asi1"}


async def get_guidance(component: str) -> dict:
    key = component.lower().strip()
    if key in _cache:
        return _cache[key]
    api_key = os.getenv("ASI1_API_KEY")
    result = None
    if api_key:
        prompt = (f"A small manufacturer needs an alternate source of: {component}. Reply with JSON only: "
                  '{"terms": [3-5 short words that a supplier\'s business name might contain], '
                  '"hubs": [{"city": str, "country": str} for up to 4 well-known manufacturing cities for this item; include at least 2 outside China if any exist], '
                  '"platforms": [up to 3 real B2B sourcing marketplaces suited to it]}. '
                  "Only well-known facts; no company names.")
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post("https://api.asi1.ai/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {api_key}"},
                                 json={"model": "asi1", "max_tokens": 1500,
                                       "messages": [{"role": "user", "content": prompt}]})
                r.raise_for_status()
                result = parse_guidance(r.json()["choices"][0]["message"]["content"], component)
        except Exception as e:  # noqa: BLE001 - fall back to static guidance
            log.warning("guidance LLM failed for %r: %s %s", component, type(e).__name__, e)
            result = None
        if result is None:
            log.warning("guidance for %r unparseable or failed; using static terms", component)
    if result is None:
        return static_guidance(component)  # not cached: retry the LLM next time
    _cache[key] = result
    return result
