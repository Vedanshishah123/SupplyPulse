"""Turn a free-text supply-chain description into structured suppliers.

Primary: ASI:One API (JSON out). Fallback: a regex heuristic so onboarding still
works offline / without a key. Both feed the same confirmation step.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import httpx

SYSTEM_PROMPT = (
    "Extract a manufacturer's supply chain from the user's text. Reply with JSON only: "
    '{"port": {"city": str} | null, "suppliers": [{"component": str, "name": str, '
    '"city": str, "country": str}]}. Use "" for unknown fields. Do not invent suppliers.'
)


@dataclass
class Extracted:
    suppliers: list[dict] = field(default_factory=list)
    port_city: str | None = None
    method: str = "heuristic"


async def extract_llm(text: str, api_key: str, model: str = "asi1") -> Extracted | None:
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.asi1.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "max_tokens": 2000, "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text}]})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        data, _ = json.JSONDecoder().raw_decode(content[content.index("{"):])  # tolerate fences / chatter
        sup = [s for s in data.get("suppliers", []) if s.get("city") and s.get("component")]
        if not sup:
            return None
        port = (data.get("port") or {}).get("city")
        return Extracted(sup, port, "asi1")
    except Exception:  # noqa: BLE001 - fall back to the heuristic
        return None


_CHUNK = re.compile(
    r"(?:^|\s)(?:and\s+)?(?P<comp>(?!comes?)[A-Za-z]+(?:\s(?!comes?)[A-Za-z]+)?)\s+(?:comes?\s+)?from\s+"
    r"(?:(?:an?|the)\s+(?P<kind>[a-z]+)\s+(?:in|at|near)\s+|(?:in|at)\s+)?"
    r"(?P<city>[A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)*)\s*$")
_PORT = re.compile(r"(?:through|via|from)\s+(?:the\s+)?(?P<city>[A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)*?)\s+port|"
                   r"ship\w*\s+.*?\b(?:through|via)\s+(?P<city2>[A-Z][A-Za-z]+)", re.I)


def extract_heuristic(text: str) -> Extracted:
    port = None
    m = _PORT.search(text)
    if m:
        port = (m.group("city") or m.group("city2"))
    suppliers = []
    for sentence in re.split(r"[.;\n]", text):
        for chunk in re.split(r",\s*(?:and\s+)?|\s+and\s+(?=[a-z]+\s+from)", sentence):
            chunk = chunk.strip()
            cm = _CHUNK.search(chunk)
            if cm and cm.group("city").lower() != (port or "").lower():
                comp = re.sub(r"\s+comes?$", "", cm.group("comp").strip().lower())
                kind = cm.group("kind")
                suppliers.append({
                    "component": comp,
                    "name": f"{kind.title()} in {cm.group('city')}" if kind else f"{comp.title()} supplier",
                    "city": cm.group("city"), "country": ""})
    return Extracted(suppliers, port, "heuristic")


async def extract(text: str) -> Extracted:
    key = os.getenv("ASI1_API_KEY")
    if key:
        got = await extract_llm(text, key)
        if got:
            return got
    return extract_heuristic(text)
