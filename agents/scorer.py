"""Risk scorer: collects signals per scan until complete or the deadline passes."""
import time

from uagents import Agent, Context

from agents.common import PORTS, address, seed
from models.messages import ScanReport, ScanStart, SignalResult, SupplierScore
from scoring import risk
from scoring.explain import is_real_finding, reason as explain_reason

agent = Agent(name="scorer", seed=seed("scorer"), port=PORTS["scorer"],
              endpoint=[f"http://127.0.0.1:{PORTS['scorer']}/submit"])

# scan_id -> {"start": ScanStart, "results": {(supplier_id, kind): SignalResult}, "deadline": float, "expected": set}
_pending: dict[str, dict] = {}
_done: set[str] = set()  # idempotency: a scan id is only ever reported once
_early: dict[str, list[SignalResult]] = {}  # signals that outran their ScanStart (different agents, no ordering)

COVERAGE_LABELS = {"hazard": "Weather, cyclone, flood and earthquake alerts", "events": "Local news (strikes, closures, fires)"}


def expected_signals(start: ScanStart) -> set[tuple[str, str]]:
    exp = set()
    for s in start.suppliers:
        exp |= {(s.id, "hazard"), (s.id, "events")}
        if s.company:
            exp.add((s.id, "financial"))
    return exp


def _top_finding(s, results: dict) -> str | None:
    ranked = sorted((r for (sid, _), r in results.items() if sid == s.id and r.signal is not None and r.signal > 0),
                    key=lambda r: r.signal, reverse=True)
    for r in ranked:
        for e in r.evidence:
            if is_real_finding(e):
                return e
    return None


def _coverage(start: ScanStart, results: dict) -> list[str]:
    """Plain-language completeness lines. A missing signal is never presented as 'all clear'."""
    n = len(start.suppliers)
    lines: list[str] = []
    for kind, label in COVERAGE_LABELS.items():
        mine = [r for (sid, k), r in results.items() if k == kind]
        ok = [r for r in mine if r.signal is not None]
        if start.replay and kind == "events":
            lines.append("ℹ️ Local news isn't part of the replay.")
            continue
        via = ", ".join(sorted({r.via for r in ok if r.via}))
        if len(ok) >= n and n:
            lines.append(f"✅ {label}: checked for all {n} suppliers" + (f" (via {via})" if via else "") + ".")
        else:
            notes = sorted({r.note for r in mine if r.signal is None and r.note})
            reason = "no answer arrived in time"
            if notes:  # notes look like "couldn't check X (reason)"; show just the reason
                reason = notes[0][notes[0].rfind("(") + 1:].rstrip(")") if "(" in notes[0] else notes[0]
            lines.append(f"⚠️ {label}: couldn't be checked for {n - len(ok)} of {n} suppliers ({reason}). "
                         "Try `scan` again in a few minutes.")
    listed = [s for s in start.suppliers if s.company]
    if start.replay:
        lines.append("ℹ️ Financial health isn't part of the replay.")
    elif listed:
        got = sum(1 for s in listed if (r := results.get((s.id, "financial"))) and r.signal is not None)
        lines.append(f"{'✅' if got == len(listed) else '⚠️'} Financial health: checked for {got} of {len(listed)} "
                     f"suppliers with a company name.")
    else:
        lines.append("ℹ️ Financial health: skipped. Add `company=<name>` to a supplier that is a listed company "
                     "to include it.")
    # remarks on data that did arrive (e.g. an old cached value)
    extra = sorted({r.note for r in results.values() if r.signal is not None and r.note and r.note != "replay data"})
    lines += [f"ℹ️ {x[0].upper() + x[1:]}." for x in extra]
    return lines


def build_report(start: ScanStart, results: dict[tuple[str, str], SignalResult], timed_out: bool) -> ScanReport:
    scored, raw = [], []
    for s in start.suppliers:
        sig = lambda kind: (r.signal if (r := results.get((s.id, kind))) else None)
        signals = {"hazard": sig("hazard"), "events": sig("events"),
                   "financial": sig("financial") if s.company else None,
                   "concentration": risk.concentration_signal(s, start.suppliers)}
        r = risk.score_supplier(s, signals)
        raw.append(r)
        mine = [x for (sid, _), x in results.items() if sid == s.id]
        scored.append(SupplierScore(
            supplier_id=s.id, name=s.name, component=s.component, city=s.city, band=r["band"],
            risk=r["risk"], likelihood=r["likelihood"], impact=r["impact"],
            reason=explain_reason(s, r["band"], _top_finding(s, results)),
            evidence=[f"[{x.kind}] {e}" for x in mine for e in x.evidence],
            sources=[u for x in mine for u in x.sources],
            missing=[m for m in r["missing"] if not (m == "financial" and not s.company)],
            signals={k: v for k, v in signals.items() if v is not None}))
    total, band = risk.overall(raw)
    return ScanReport(scan_id=start.scan_id, user=start.user, overall_band=band, overall_score=total,
                      scores=scored, coverage=_coverage(start, results), timed_out=timed_out,
                      replay=start.replay, monitor=start.monitor)


async def _finish(ctx: Context, scan_id: str, timed_out: bool):
    p = _pending.pop(scan_id, None)
    if not p:
        return
    _done.add(scan_id)
    ctx.logger.info("scan %s complete (timed_out=%s, %d signals); sending report to orchestrator",
                    scan_id[:8], timed_out, len(p["results"]))
    await ctx.send(address("orchestrator"), build_report(p["start"], p["results"], timed_out))


@agent.on_message(ScanStart)
async def on_start(ctx: Context, sender: str, msg: ScanStart):
    if msg.scan_id in _pending or msg.scan_id in _done:
        return  # duplicate / retried message
    _pending[msg.scan_id] = {"start": msg, "results": {}, "expected": expected_signals(msg),
                             "deadline": time.monotonic() + msg.deadline_s}
    for early in _early.pop(msg.scan_id, []):
        _pending[msg.scan_id]["results"][(early.supplier_id, early.kind)] = early
    if _pending[msg.scan_id]["expected"] <= set(_pending[msg.scan_id]["results"]):
        await _finish(ctx, msg.scan_id, timed_out=False)


@agent.on_message(SignalResult)
async def on_signal(ctx: Context, sender: str, msg: SignalResult):
    p = _pending.get(msg.scan_id)
    if not p:
        if msg.scan_id not in _done:
            _early.setdefault(msg.scan_id, []).append(msg)
        return
    p["results"][(msg.supplier_id, msg.kind)] = msg
    if p["expected"] <= set(p["results"]):
        await _finish(ctx, msg.scan_id, timed_out=False)


@agent.on_interval(period=1.0)
async def check_deadlines(ctx: Context):
    now = time.monotonic()
    for scan_id in [k for k, v in _pending.items() if v["deadline"] <= now]:
        await _finish(ctx, scan_id, timed_out=True)
