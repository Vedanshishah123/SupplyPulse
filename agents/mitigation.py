"""Mitigation agent: alternate suppliers and drafted emails."""
import asyncio

from uagents import Agent, Context

from agents.common import PORTS, address, seed
from mitigation.alternatives import find_alternatives
from mitigation.email import draft_email
from models.messages import MitigationRequest, MitigationResult, PortRec

PORTS.setdefault("mitigation", 8006)
agent = Agent(name="mitigation", seed=seed("mitigation"), port=PORTS["mitigation"],
              endpoint=[f"http://127.0.0.1:{PORTS['mitigation']}/submit"])


def render_alternatives(s, found) -> dict:
    """Returns MitigationResult fields (text, alternatives)."""
    lines = [f"**Alternatives for {s.component}** (currently {s.city})", ""]
    if found.matched:
        lines += ["**Businesses whose name matches** (unverified: please check each one yourself)"]
        lines += [f"{i}. {c}" for i, c in enumerate(found.matched, 1)]
    else:
        lines += ["No businesses with a matching name were found. Small suppliers are often missing from map data."]
    leads = found.leads or {}
    lines += ["", f"**Where else to look** ({'AI suggestions from general knowledge, unverified' if leads.get('source') == 'asi1' else 'generic leads'})"]
    if leads.get("terms"):
        lines.append("- Search terms: " + ", ".join(leads["terms"]))
    if leads.get("hubs"):
        lines.append("- Known manufacturing areas: " + ", ".join(
            f"{h['city']}{', ' + h['country'] if h['country'] else ''}" for h in leads["hubs"]))
    if leads.get("platforms"):
        lines.append("- Sourcing platforms: " + ", ".join(leads["platforms"]))
    if found.via:
        lines.append(f"_Searched with: {', '.join(sorted(found.via))}._")
    lines += [f"\n_{note}_" for note in found.notes]
    lines += ["", "Check any candidate yourself before relying on it."]
    if found.candidates:
        lines.append("Reply `inquire N` to draft an inquiry email, or `email <supplier number>` to write to your current supplier.")
    return {"text": "\n".join(lines), "alternatives": found.candidates}


async def handle(req: MitigationRequest) -> MitigationResult:
    s = req.supplier
    if req.kind == "alternatives":
        port = PortRec(name=req.port_city, lat=req.port_lat, lon=req.port_lon) if req.port_lat is not None else None
        return MitigationResult(user=req.user, kind=req.kind, supplier_id=s.id, **render_alternatives(
            s, await find_alternatives(s, port)))
    text, method = await draft_email("inquiry" if req.kind == "inquiry" else "delay", s, req.reason, req.evidence,
                                     req.port_city, req.alternative)
    note = "" if method == "asi1" else "\n\n_Template draft; set `ASI1_API_KEY` for a tailored version._"
    return MitigationResult(user=req.user, kind=req.kind, supplier_id=s.id, text=f"**Draft email**\n\n{text}{note}")


@agent.on_message(MitigationRequest)
async def on_request(ctx: Context, sender: str, msg: MitigationRequest):
    asyncio.create_task(_answer(ctx, msg))


async def _answer(ctx: Context, msg: MitigationRequest):
    try:
        res = await handle(msg)
    except Exception as e:  # noqa: BLE001
        ctx.logger.exception("mitigation failed")
        res = MitigationResult(user=msg.user, kind=msg.kind, supplier_id=msg.supplier.id,
                               text=f"Sorry, that failed ({e}). You have not been charged.", ok=False)
    await ctx.send(address("orchestrator"), res)
