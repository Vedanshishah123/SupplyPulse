"""Events agent: strikes, closures, fires in the news for a supplier's city and port (GDELT)."""
import asyncio
import logging

from uagents import Agent, Context

from adapters import sources
from adapters.base import friendly_reason
from agents.common import PORTS, address, seed
from models.messages import SignalRequest, SignalResult
from scoring.events import event_signal

log = logging.getLogger("supplypulse.events")
PORTS.setdefault("events", 8004)
agent = Agent(name="events", seed=seed("events"), port=PORTS["events"],
              endpoint=[f"http://127.0.0.1:{PORTS['events']}/submit"])


async def compute(req: SignalRequest) -> SignalResult:
    base = dict(scan_id=req.scan_id, supplier_id=req.supplier_id, kind="events")
    if req.replay:
        return SignalResult(**base, note="news isn't part of the replay")
    places = [req.city] + ([req.port_city] if req.port_city and req.port_city.lower() != req.city.lower() else [])
    results = await asyncio.gather(*(sources.get_events(p) for p in places))
    scored, evidence, links, failed, used = [], [], [], [], []
    for place, r in zip(places, results):
        if not r.ok:
            log.warning("news for %s unavailable: %s", place, "; ".join(r.errors))
            failed.append((place, r))
            continue
        sig, ev, urls = event_signal(r.value, place)
        scored.append(sig)
        evidence += ev
        links += urls
        used.append(sources.source_name(r.source))
    if not scored:  # nothing could be checked: unavailable, never "no events"
        reason = friendly_reason([e for _, r in failed for e in r.errors])
        return SignalResult(**base, note=f"couldn't check local news ({reason})")
    notes = []
    if failed:
        notes.append("news for " + ", ".join(p for p, _ in failed) + " couldn't be checked")
    return SignalResult(**base, signal=max(scored), evidence=evidence or ["No strike, closure or fire news found"],
                        sources=links, note="; ".join(notes), via=", ".join(dict.fromkeys(used)))


@agent.on_message(SignalRequest)
async def on_request(ctx: Context, sender: str, msg: SignalRequest):
    # uAgents runs handlers one at a time; a task keeps a slow, throttled news lookup from blocking other scans.
    asyncio.create_task(_answer(ctx, msg))


async def _answer(ctx: Context, msg: SignalRequest):
    try:
        res = await compute(msg)
    except Exception as e:  # noqa: BLE001
        ctx.logger.exception("events failed")
        res = SignalResult(scan_id=msg.scan_id, supplier_id=msg.supplier_id, kind="events",
                           note=f"couldn't check local news ({type(e).__name__})")
    await ctx.send(address("scorer"), res)
