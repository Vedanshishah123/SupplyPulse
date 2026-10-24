"""Hazard agent: weather (current + forecast), cyclone/flood alerts and earthquakes for a supplier and its port."""
import asyncio
import logging

from uagents import Agent, Context

from adapters import sources
from adapters.base import friendly_reason
from agents.common import PORTS, address, seed
from models.messages import HazardRequest, SignalResult
from replay import loader
from scoring.hazard import hazard_signal

log = logging.getLogger("supplypulse.hazard")
agent = Agent(name="hazard", seed=seed("hazard"), port=PORTS["hazard"],
              endpoint=[f"http://127.0.0.1:{PORTS['hazard']}/submit"])


def _places(req: HazardRequest):
    places = [(req.city or "the supplier", req.lat, req.lon)]
    if req.port_lat is not None and req.port_lon is not None:
        places.append((f"{req.port_city} port" if req.port_city else "the port", req.port_lat, req.port_lon))
    return places


async def compute_replay(req: HazardRequest, places) -> SignalResult:
    """Recorded event through the same scoring path; live weather is deliberately not mixed in."""
    sc = loader.load(req.replay)
    if sc is None:
        return SignalResult(scan_id=req.scan_id, supplier_id=req.supplier_id, kind="hazard",
                            note=f"unknown replay scenario {req.replay!r}")
    signal, evidence, links = hazard_signal(places, [None] * len(places), sc["gdacs"], [], sc["as_of_dt"])
    tag = f"[REPLAY: {sc['title']}] "
    return SignalResult(scan_id=req.scan_id, supplier_id=req.supplier_id, kind="hazard", signal=signal,
                        evidence=[tag + e for e in evidence], sources=links, note="replay data",
                        via="recorded GDACS data")


async def compute(req: HazardRequest) -> SignalResult:
    places = _places(req)
    if req.replay:
        return await compute_replay(req, places)
    forecasts, currents, gdacs, quakes = await asyncio.gather(
        asyncio.gather(*(sources.get_forecast(la, lo) for _, la, lo in places)),
        asyncio.gather(*(sources.get_current(label, la, lo) for label, la, lo in places)),
        sources.get_gdacs_events(), sources.get_quakes())
    signal, evidence, links = hazard_signal(
        places, [f.value if f.ok else None for f in forecasts], gdacs.value if gdacs.ok else None,
        quakes.value if quakes.ok else None,
        currents=[(c.value if c.ok else None, sources.source_name(c.source)) for c in currents])

    checks = (("weather forecast", forecasts[0]), ("cyclone and flood alerts", gdacs), ("earthquake feed", quakes),
              ("current conditions", currents[0]))
    failed = [(name, r) for name, r in checks if not r.ok]
    for name, r in failed:
        log.warning("hazard %s unavailable: %s", name, "; ".join(r.errors))
    notes = []
    if failed:
        notes.append("couldn't check " + ", ".join(n for n, _ in failed) +
                     f" ({friendly_reason([e for _, r in failed for e in r.errors])})")
    stale = [r for _, r in checks if r.ok and r.stale]
    if stale:
        notes.append(f"some weather data is {max(r.age_s for r in stale) / 60:.0f} min old")
    used = list(dict.fromkeys(sources.source_name(r.source) for _, r in checks if r.ok))
    return SignalResult(scan_id=req.scan_id, supplier_id=req.supplier_id, kind="hazard", signal=signal,
                        evidence=evidence, sources=links, note="; ".join(notes), via=", ".join(used))


@agent.on_message(HazardRequest)
async def on_request(ctx: Context, sender: str, msg: HazardRequest):
    asyncio.create_task(_answer(ctx, msg))  # handlers run one at a time; don't let a slow fetch block others


async def _answer(ctx: Context, msg: HazardRequest):
    try:
        res = await compute(msg)
    except Exception as e:  # noqa: BLE001
        ctx.logger.exception("hazard failed")
        res = SignalResult(scan_id=msg.scan_id, supplier_id=msg.supplier_id, kind="hazard", signal=None,
                           note=f"couldn't check weather and disaster alerts ({type(e).__name__})")
    await ctx.send(address("scorer"), res)


@agent.on_interval(period=600.0)  # first run is immediate, which warms the cache
async def refresh(ctx: Context):
    await sources.refresh_gdacs()
