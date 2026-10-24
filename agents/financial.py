"""Financial agent: stress signal for suppliers that are (or belong to) listed companies.

Ticker, fundamentals and news sentiment (all Alpha Vantage) are combined into one stress signal.
"""
import asyncio
import logging

from uagents import Agent, Context

from adapters import sources
from adapters.base import friendly_reason
from agents.common import PORTS, address, seed
from models.messages import SignalRequest, SignalResult
from scoring.financial import combine, fundamentals_signal, sentiment_to_signal

log = logging.getLogger("supplypulse.financial")
PORTS.setdefault("financial", 8005)
agent = Agent(name="financial", seed=seed("financial"), port=PORTS["financial"],
              endpoint=[f"http://127.0.0.1:{PORTS['financial']}/submit"])


async def compute(req: SignalRequest) -> SignalResult:
    base = dict(scan_id=req.scan_id, supplier_id=req.supplier_id, kind="financial")
    if req.replay:
        return SignalResult(**base, note="financial data isn't part of the replay")
    tick = await sources.resolve_ticker(req.company)
    if not tick.ok:
        log.warning("ticker for %r unavailable: %s", req.company, "; ".join(tick.errors))
        return SignalResult(**base, note=f"couldn't find a listed company for '{req.company}' "
                                         f"({friendly_reason(tick.errors)})")
    ticker = tick.value
    sent, over = await asyncio.gather(sources.get_sentiment(req.company, ticker), sources.get_overview(ticker))
    for name, r in (("sentiment", sent), ("overview", over)):
        if not r.ok:
            log.warning("%s for %s unavailable: %s", name, ticker, "; ".join(r.errors))
    s_sig = sentiment_to_signal(sent.value["score"]) if sent.ok else None
    f_sig, f_ev = fundamentals_signal(over.value) if over.ok else (None, [])
    signal = combine(s_sig, f_sig)
    used = ", ".join(dict.fromkeys(sources.source_name(r.source) for r in (tick, sent, over) if r.ok))
    if signal is None:
        return SignalResult(**base, note=f"found {ticker} but couldn't get news sentiment or fundamentals "
                                         f"({friendly_reason(sent.errors + over.errors)})", via=used)
    evidence, links = list(f_ev), []
    if sent.ok:
        evidence = [f"News sentiment for {ticker}: {sent.value['score']:+.2f} across {sent.value['n']} articles"] \
            + [t for t in sent.value["titles"] if t] + evidence
        links = [u for u in sent.value["urls"] if u]
    missing = [n for n, r in (("news sentiment", sent), ("fundamentals", over)) if not r.ok]
    return SignalResult(**base, signal=signal, evidence=evidence, sources=links, via=used,
                        note=("couldn't check " + " or ".join(missing)) if missing else "")


@agent.on_message(SignalRequest)
async def on_request(ctx: Context, sender: str, msg: SignalRequest):
    asyncio.create_task(_answer(ctx, msg))


async def _answer(ctx: Context, msg: SignalRequest):
    try:
        res = await compute(msg)
    except Exception as e:  # noqa: BLE001
        ctx.logger.exception("financial failed")
        res = SignalResult(scan_id=msg.scan_id, supplier_id=msg.supplier_id, kind="financial",
                           note=f"couldn't check financial health ({type(e).__name__})")
    await ctx.send(address("scorer"), res)
