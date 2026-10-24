"""Export a sample risk-dashboard card as JSON to try in the ASI:One Card Playground.

    python -m scripts.export_card

Writes data/cards/dashboard_safe.* and dashboard_rich.* (data/ is git-ignored):
  *.payload.json   the card_payload alone (paste it into the Playground's payload editor, kind = custom)
  *.metadata.json  the full metadata block exactly as sent in the chat message
The sample is a replay of Cyclone Michaung, so it needs no network and no keys.
"""
import asyncio
import json
import os
from pathlib import Path

os.environ.setdefault("USE_MAILBOX", "0")

from agents import scorer  # noqa: E402
from agents.hazard import compute_replay  # noqa: E402
from models.messages import HazardRequest, ScanStart, SignalResult, SupplierRec  # noqa: E402
from render import cards  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "cards"


def _supplier(i, comp, city, cc, lat, lon, **kw):
    base = dict(id=f"s{i}", component=comp, name=comp, city=city, country=cc, lat=lat, lon=lon,
                criticality="medium", stock_days=30, has_backup=False, lead_time_days=25)
    return SupplierRec(**{**base, **kw})


async def sample_report():
    sups = [_supplier(1, "fabric", "Erode", "IN", 11.34, 77.72, lead_time_days=7),
            _supplier(2, "zippers", "Shenzhen", "CN", 22.54, 114.06, criticality="high", stock_days=10, lead_time_days=30),
            _supplier(3, "buttons", "Ningbo", "CN", 29.87, 121.54, criticality="high", stock_days=8),
            _supplier(4, "dyes", "Ankleshwar", "IN", 21.63, 73.02, lead_time_days=7)]
    results = {}
    for s in sups:
        req = HazardRequest(scan_id="x", supplier_id=s.id, lat=s.lat, lon=s.lon, city=s.city, port_city="Chennai",
                            port_lat=13.08, port_lon=80.27, replay="chennai-cyclone")
        results[(s.id, "hazard")] = await compute_replay(req, [(s.city, s.lat, s.lon), ("Chennai port", 13.08, 80.27)])
        results[(s.id, "events")] = SignalResult(scan_id="x", supplier_id=s.id, kind="events")
    return scorer.build_report(ScanStart(scan_id="x", user="u", suppliers=sups, replay="chennai-cyclone"), results, False)


def main() -> None:
    report = asyncio.run(sample_report())
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rich in (("dashboard_safe", False), ("dashboard_rich", True)):
        payload = cards.dashboard_payload(report, rich=rich)
        (OUT / f"{name}.payload.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUT / f"{name}.metadata.json").write_text(
            json.dumps(cards.metadata("custom", payload), indent=2, ensure_ascii=False), encoding="utf-8")
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        print(f"{name}: {size} bytes, nesting depth {cards.depth(payload['root'])}")
    print(f"written to {OUT}")


if __name__ == "__main__":
    main()
