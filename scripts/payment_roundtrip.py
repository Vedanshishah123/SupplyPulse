"""Phase-1 gate helper: local payment round trip with a simulated buyer wallet.

    ALLOW_UNVERIFIED_PAYMENTS=1 python -m scripts.payment_roundtrip
This proves the message flow only (Request -> Commit -> Complete). A real testnet
payment needs a funded wallet on the ASI:One side and on-chain verification.
"""
import os
from datetime import datetime
from uuid import uuid4

os.environ["USE_MAILBOX"] = "0"
# Local harness: own bureau port and own agent addresses, so a running real deployment is untouched.
os.environ["BUREAU_PORT"] = "8110"
os.environ.setdefault("AGENT_SEED_PREFIX", "supplypulse-localtest")
os.environ.setdefault("STATE_PATH", os.path.join(__import__("tempfile").gettempdir(), "supplypulse-test-state.json"))
if os.path.exists(os.environ["STATE_PATH"]) and not os.getenv("KEEP_STATE"):
    os.remove(os.environ["STATE_PATH"])  # local runs start clean

from uagents import Agent, Context, Protocol  # noqa: E402
from uagents_core.contrib.protocols.chat import ChatMessage, ChatAcknowledgement, TextContent  # noqa: E402
from uagents_core.contrib.protocols.payment import (  # noqa: E402
    CommitPayment, CompletePayment, CancelPayment, RequestPayment, payment_protocol_spec)

from agents import bureau  # noqa: E402
from agents.common import address, seed  # noqa: E402

buyer = Agent(name="buyer", seed=seed("buyer"), port=8001, endpoint=["http://127.0.0.1:8001/submit"])
pay = Protocol(spec=payment_protocol_spec, role="buyer")


@buyer.on_event("startup")
async def go(ctx: Context):
    await ctx.send(address("orchestrator"), ChatMessage(
        timestamp=datetime.utcnow(), msg_id=uuid4(), content=[TextContent(type="text", text="upgrade")]))


@pay.on_message(RequestPayment)
async def on_request(ctx: Context, sender: str, msg: RequestPayment):
    print("BUYER got RequestPayment:", msg.accepted_funds[0], "->", msg.recipient[:12], "…", flush=True)
    await ctx.send(sender, CommitPayment(funds=msg.accepted_funds[0], recipient=msg.recipient,
                                         transaction_id="SIMULATED-TX-1", reference=msg.reference))


@pay.on_message(CompletePayment)
async def on_complete(ctx: Context, sender: str, msg: CompletePayment):
    print("BUYER got CompletePayment:", msg.transaction_id, flush=True)
    await ctx.send(address("orchestrator"), ChatMessage(
        timestamp=datetime.utcnow(), msg_id=uuid4(), content=[TextContent(type="text", text="plans")]))


@pay.on_message(CancelPayment)
async def on_cancel(ctx: Context, sender: str, msg: CancelPayment):
    print("BUYER got CancelPayment:", msg.reason, flush=True)
    os._exit(0)


@buyer.on_message(ChatMessage)
async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.utcnow(), acknowledged_msg_id=msg.msg_id))
    text = "".join(c.text for c in msg.content if isinstance(c, TextContent))
    print("BUYER chat:", " ".join(text[:90].split()), flush=True)
    if "You are on **Pro**" in text:
        print("ENTITLEMENT CONFIRMED: Pro active", flush=True)
        os._exit(0)


@buyer.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


buyer.include(pay)

if __name__ == "__main__":
    bureau.build(extra=(buyer,)).run()
