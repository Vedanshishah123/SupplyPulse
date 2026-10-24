"""Plans, entitlements and the Payment Protocol seller side.

The Payment Protocol must live on the agent the user's wallet talks to, so it is attached to the orchestrator
via `attach(...)` rather than running as a separate agent. Seller role: we send RequestPayment,
CompletePayment, CancelPayment and receive CommitPayment, RejectPayment.

ASI:One only accepts a payment request that follows Fetch.ai's reference seller: `reference` is the chat
session id, `metadata` names the agent/service/network, and the amount is a small FET value. On commit, the
buyer's wallet comes from `metadata.buyer_fet_wallet` and the transfer is verified on-chain before anything is
granted. ALLOW_UNVERIFIED_PAYMENTS=1 skips only the on-chain check (local/demo testing).

Payment safety: entitlements are granted only after verification. A deep-report credit is per supplier and is
not consumed by delivery, so if a report fails the user keeps access and can retry at no extra cost.
"""
import asyncio
import logging
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Awaitable, Callable

from uagents import Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CancelPayment, CommitPayment, CompletePayment, Funds, RejectPayment,
    RequestPayment, payment_protocol_spec)

log = logging.getLogger("supplypulse.billing")

PRO_DAYS = 30
FREE_MAX_SUPPLIERS = 5
FREE_SCANS = 1
FREE_MITIGATION_PREVIEWS = int(os.getenv("FREE_MITIGATION_PREVIEWS", "1"))
# Testnet demo prices in FET (override with PRO_PRICE_FET / DEEP_PRICE_FET). Kept small: testnet faucets are small.
PRICES = {"pro": (os.getenv("PRO_PRICE_FET", "0.5"),
                  f"SupplyPulse Pro, {PRO_DAYS} days: unlimited scans and suppliers, scheduled monitoring with "
                  "alerts, alternates and drafted emails"),
          "deep": (os.getenv("DEEP_PRICE_FET", "0.2"),
                   "SupplyPulse deep report for one supplier: alternates and drafted emails")}
ATTO = 10 ** 18  # 1 FET = 10^18 base units (afet / atestfet)

# user -> {"pro_until": epoch seconds, "deep": [supplier ids]}; persisted by the orchestrator
entitlements: dict[str, dict] = {}
# user -> {"plan", "supplier_id", "amount", "reference"} awaiting payment
pending: dict[str, dict] = {}


def use_testnet() -> bool:
    return os.getenv("FET_USE_TESTNET", "true").lower() != "false"


def is_pro(user: str, now: float | None = None) -> bool:
    return entitlements.get(user, {}).get("pro_until", 0) > (now or time.time())


def has_deep(user: str, supplier_id: str) -> bool:
    return supplier_id in entitlements.get(user, {}).get("deep", [])


def grant(user: str, plan: str, supplier_id: str = "", now: float | None = None) -> None:
    ent = entitlements.setdefault(user, {"pro_until": 0, "deep": []})
    if plan == "pro":
        ent["pro_until"] = max(ent["pro_until"], now or time.time()) + PRO_DAYS * 86400
    elif plan == "deep" and supplier_id not in ent["deep"]:
        ent["deep"].append(supplier_id)


# ---------------------------------------------------------------- building the request
def make_request(plan: str, recipient: str, session_id: str, supplier_id: str = "") -> RequestPayment:
    amount, description = PRICES[plan]
    service = "SupplyPulse Pro" if plan == "pro" else "SupplyPulse deep report"
    metadata = {"agent": "supplypulse", "service": service,
                "fet_network": "stable-testnet" if use_testnet() else "mainnet",
                "mainnet": "false" if use_testnet() else "true",
                "provider_agent_wallet": recipient, "content": description}
    return RequestPayment(accepted_funds=[Funds(amount=amount, currency="FET", payment_method="fet_direct")],
                          recipient=recipient, deadline_seconds=300, reference=session_id,
                          description=description, metadata=metadata)


# ---------------------------------------------------------------- verifying the commit
def _fet_amount_ok(committed: str, required: str) -> bool:
    try:
        return Decimal(committed) >= Decimal(required)
    except InvalidOperation:
        return False


def _transfer_matches(events: dict, seller: str, buyer: str, need_base: int, denom: str) -> bool:
    ev = events.get("transfer", {})
    if ev.get("recipient") != seller or ev.get("sender") != buyer:
        return False
    for coin in str(ev.get("amount", "")).split(","):  # "100atestfet" or "100atestfet,5uatom"
        if coin.endswith(denom):
            try:
                if int(coin[: -len(denom)]) >= need_base:
                    return True
            except ValueError:
                pass
    return False


def _verify_on_chain(tx_hash: str, amount_fet: str, buyer: str, seller: str) -> bool:
    """Blocking ledger lookup (run in a thread). The transaction must have succeeded and contain a transfer
    from the buyer to us of at least the price. Per-message logs are checked, so network fees don't count."""
    from cosmpy.aerial.client import LedgerClient, NetworkConfig
    testnet = use_testnet()
    ledger = LedgerClient(NetworkConfig.fetchai_stable_testnet() if testnet else NetworkConfig.fetchai_mainnet())
    denom = "atestfet" if testnet else "afet"
    need = int(Decimal(amount_fet) * ATTO)
    tx = None
    for _ in range(8):  # a fresh transaction can take a few seconds to be indexed
        try:
            tx = ledger.query_tx(tx_hash)
            break
        except Exception as e:  # noqa: BLE001 - not found yet
            log.info("transaction %s not found yet: %s", tx_hash[:10], type(e).__name__)
            time.sleep(2)
    if tx is None or not tx.is_successful():
        return False
    return any(_transfer_matches(m.events, seller, buyer, need, denom) for m in tx.logs) or \
        _transfer_matches(tx.events, seller, buyer, need, denom)


async def verify_commit(commit: CommitPayment, expected: dict, seller: str) -> tuple[bool, str]:
    """Returns (ok, reason). Rejects the wrong method/currency/amount before touching the chain."""
    f = commit.funds
    if f.payment_method != "fet_direct" or f.currency != "FET":
        return False, "only FET (fet_direct) payments are accepted"
    if not _fet_amount_ok(f.amount, expected["amount"]):
        return False, f"the amount is below the price ({expected['amount']} FET)"
    if os.getenv("ALLOW_UNVERIFIED_PAYMENTS") == "1":
        return True, "unverified (testing mode)"
    meta = commit.metadata or {}
    buyer = meta.get("buyer_fet_wallet") or meta.get("buyer_fet_address")
    if not buyer or not isinstance(buyer, str):
        return False, "the payment did not say which wallet it came from"
    try:
        ok = await asyncio.to_thread(_verify_on_chain, commit.transaction_id, expected["amount"], buyer, seller)
    except Exception as e:  # noqa: BLE001
        log.warning("on-chain verification failed to run: %s", e)
        return False, "could not check the transaction on the Fetch.ai network"
    return (True, "verified on-chain") if ok else (False, "no matching transfer found on the Fetch.ai network")


async def request_payment(ctx: Context, user: str, plan: str, recipient: str, supplier_id: str = "") -> None:
    req = make_request(plan, recipient, str(ctx.session), supplier_id)
    pending[user] = {"plan": plan, "supplier_id": supplier_id, "amount": PRICES[plan][0],
                     "reference": req.reference, "seller": recipient}
    await ctx.send(user, req)


def attach(agent, notify: Callable[[Context, str, str], Awaitable[None]],
           on_change: Callable[[], None] = lambda: None):
    """notify(ctx, user, text) sends a chat message; on_change persists entitlements."""
    proto = Protocol(spec=payment_protocol_spec, role="seller")

    @proto.on_message(CommitPayment)
    async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
        item = pending.get(sender)
        if item is None:
            await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason="no pending request"))
            return
        ok, why = await verify_commit(msg, item, item["seller"])
        log.info("payment from %s for %s: %s (%s)", sender[-8:], item["plan"], "accepted" if ok else "refused", why)
        if ok:
            pending.pop(sender, None)
            grant(sender, item["plan"], item["supplier_id"])
            on_change()
            await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
            what = "**Pro** is active" if item["plan"] == "pro" else "your **deep report** is unlocked"
            await notify(ctx, sender, f"Payment received, {what}. Thank you!")
        else:
            await ctx.send(sender, CancelPayment(transaction_id=msg.transaction_id, reason=why))
            await notify(ctx, sender, f"I couldn't confirm that payment ({why}), so nothing was activated.")

    @proto.on_message(RejectPayment)
    async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
        pending.pop(sender, None)
        await notify(ctx, sender, "Payment declined. You can try again any time with `plans`.")

    agent.include(proto, publish_manifest=True)
