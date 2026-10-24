"""Orchestrator: the only agent registered on Agentverse / reachable from ASI:One."""
import uuid
from datetime import datetime

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, EndSessionContent, TextContent, chat_protocol_spec)

import store
from agents import billing
from agents.common import PORTS, address, seed, use_mailbox
from agents.dialog import Dialog, Session
from models.messages import (HazardRequest, MitigationRequest, MitigationResult, MonitorTick, ScanReport,
                             ScanStart, SignalRequest)

_kwargs = {"mailbox": True, "publish_agent_details": True} if use_mailbox() else {
    "endpoint": [f"http://127.0.0.1:{PORTS['orchestrator']}/submit"]}
agent = Agent(name="supplypulse", seed=seed("orchestrator"), port=PORTS["orchestrator"], **_kwargs)
chat = Protocol(spec=chat_protocol_spec)

_ctx: Context | None = None  # set on startup/handlers so Dialog can send from outside handlers


def persist() -> None:
    store.save(dialog.sessions, billing.entitlements)


async def start_scan(user: str, sess: Session, monitor: bool = False, live: bool = False) -> str:
    """live=True forces live data (scheduled monitoring never uses a replay)."""
    scan_id = uuid.uuid4().hex
    replay = "" if live else sess.replay
    _ctx.logger.info("scan %s started for %s (monitor=%s replay=%r)", scan_id[:8], user[-8:], monitor, replay)
    located = [s for s in sess.suppliers if s.lat is not None]
    await _ctx.send(address("scorer"), ScanStart(scan_id=scan_id, user=user, port=sess.port, suppliers=located,
                                                  replay=replay, monitor=monitor))
    port_city = sess.port.name if sess.port else ""
    for s in located:
        await _ctx.send(address("hazard"), HazardRequest(
            scan_id=scan_id, supplier_id=s.id, lat=s.lat, lon=s.lon, replay=replay, city=s.city, port_city=port_city,
            port_lat=sess.port.lat if sess.port else None, port_lon=sess.port.lon if sess.port else None))
        req = SignalRequest(scan_id=scan_id, supplier_id=s.id, city=s.city, port_city=port_city,
                            company=s.company, replay=replay)
        await _ctx.send(address("events"), req)
        if s.company:
            await _ctx.send(address("financial"), req)
    return scan_id


async def request_mitigation(req: MitigationRequest) -> None:
    await _ctx.send(address("mitigation"), req)


async def request_payment(user: str, plan: str, supplier_id: str) -> None:
    await billing.request_payment(_ctx, user, plan, str(agent.wallet.address()), supplier_id)


dialog = Dialog(start_scan, request_mitigation, request_payment, on_change=persist)


async def say(ctx: Context, user: str, text: str, end: bool = False):
    content = [TextContent(type="text", text=text)]
    if end:
        content.append(EndSessionContent(type="end-session"))
    await ctx.send(user, ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid.uuid4(), content=content))


@agent.on_event("startup")
async def on_startup(ctx: Context):
    global _ctx
    _ctx = ctx
    state = store.load()
    dialog.sessions = {u: Session.from_dict(d) for u, d in state.get("sessions", {}).items()}
    billing.entitlements.update(state.get("entitlements", {}))
    ctx.logger.info("orchestrator address: %s (%d saved sessions)", agent.address, len(dialog.sessions))


@chat.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    global _ctx
    _ctx = ctx
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
    text = "".join(c.text for c in msg.content if isinstance(c, TextContent))
    if not text.strip():
        return
    await say(ctx, sender, await dialog.handle(sender, text))


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


@agent.on_message(ScanReport)
async def on_report(ctx: Context, sender: str, msg: ScanReport):
    ctx.logger.info("scan %s report received from scorer (timed_out=%s); %s", msg.scan_id[:8], msg.timed_out,
                    "replying to user" if msg.user in dialog.sessions else "no session for user")
    text, _is_alert = dialog.on_report(msg)
    # Agent-initiated messages may not be delivered by ASI:One without a new user message (open
    # question in the plan); alerts are also kept in the session and shown by the `alerts` command.
    if text:
        await say(ctx, msg.user, text)


@agent.on_message(MitigationResult)
async def on_mitigation(ctx: Context, sender: str, msg: MitigationResult):
    await say(ctx, msg.user, dialog.on_mitigation(msg))


@agent.on_message(MonitorTick)
async def on_tick(ctx: Context, sender: str, msg: MonitorTick):
    global _ctx
    _ctx = ctx
    for user, sess in list(dialog.sessions.items()):
        if billing.is_pro(user) and sess.stage == "ready" and any(s.lat is not None for s in sess.suppliers):
            await start_scan(user, sess, monitor=True, live=True)


agent.include(chat, publish_manifest=True)


async def _notify(ctx: Context, user: str, text: str):
    await say(ctx, user, text)


billing.attach(agent, notify=_notify, on_change=persist)


if __name__ == "__main__":
    agent.run()
