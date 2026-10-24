"""Text 'cards'. The Chat Protocol only carries text/resource/metadata content
(verified in uagents_core 0.4.12), so cards are formatted markdown with
numbered replies instead of buttons.

Wording rule: say what it means and what to do next, in plain language. Never put raw errors,
exception names or URLs in these messages (URLs make chat apps render unrelated link previews);
technical detail goes to the logs."""
from agents.billing import PRICES
from models.messages import ScanReport, SupplierRec, SupplierScore

ICON = {"green": "🟢", "amber": "🟠", "red": "🔴", "unknown": "⚪"}
WORD = {"red": "HIGH", "amber": "MEDIUM", "green": "LOW", "unknown": "UNKNOWN"}
KIND_TITLES = {"hazard": "Weather and disasters", "events": "Local news", "financial": "Financial health"}


def supplier_map(suppliers: list[SupplierRec], port_name: str | None) -> str:
    lines = ["**Here's what I understood about your supply chain**", ""]
    for i, s in enumerate(suppliers, 1):
        co = f" · company: {s.company}" if s.company else ""
        lines.append(f"{i}. **{s.component.title()}** from {s.city}{', ' + s.country if s.country else ''} · "
                     f"importance: {s.criticality} · stock: {s.stock_days} days · replacement time: {s.lead_time_days} days · "
                     f"backup supplier: {'yes' if s.has_backup else 'no'}{co}")
    lines += ["", f"Shipping port: **{port_name or 'not detected'}**", "",
              "The importance, stock and replacement figures are my guesses. To correct one, reply like this: "
              "`zippers: stock=10 critical=high backup=yes lead=30`. If a supplier is a listed company, add "
              "`company=YKK Corp` and I'll check its financial health too.",
              "When it looks right, reply `confirm` and I'll run your first risk check."]
    return "\n".join(lines)


def _summary(report: ScanReport) -> str:
    n_red = sum(1 for s in report.scores if s.band == "red")
    n_amber = sum(1 for s in report.scores if s.band == "amber")
    n_unknown = sum(1 for s in report.scores if s.band == "unknown")
    parts = []
    if n_red:
        parts.append(f"{n_red} {'supplier needs' if n_red == 1 else 'suppliers need'} action now")
    if n_amber:
        parts.append(f"{n_amber} {'supplier' if n_amber == 1 else 'suppliers'} to keep an eye on")
    if n_unknown:
        parts.append(f"{n_unknown} {'supplier' if n_unknown == 1 else 'suppliers'} couldn't be judged (not enough data)")
    return ", ".join(parts).capitalize() + "." if parts else "No supplier needs action right now."


def dashboard(report: ScanReport) -> str:
    lines = []
    if report.replay:
        lines += [f"🎬 **REPLAY MODE ({report.replay}).** This is a recorded past event, not live conditions.", ""]
    lines += [f"**SupplyPulse risk check** — overall risk: {ICON[report.overall_band]} **{WORD[report.overall_band]}**",
              _summary(report), "",
              "🔴 act now · 🟠 keep an eye on it · 🟢 fine for now · ⚪ not enough data", ""]
    order = {"red": 0, "amber": 1, "unknown": 2, "green": 3}
    ranked = sorted(enumerate(report.scores, 1), key=lambda t: (order[t[1].band], t[0]))
    for i, s in ranked:
        lines += [f"{i}. {ICON[s.band]} **{s.component.title()}** from {s.city}", f"    {s.reason}", ""]
    lines += ["**How complete is this check?**"] + report.coverage
    if report.timed_out:
        lines.append("⏱️ Some checks didn't finish in time. `scan` again to fill the gaps.")
    worst = ranked[0][0] if ranked else 1
    lines += ["", f"**What next?** `why {worst}` shows the evidence · `alternatives {worst}` looks for other suppliers · "
                  f"`email {worst}` drafts a message to your supplier · `scan` checks again · `plans` shows pricing"]
    return "\n".join(lines)


def detail(s: SupplierScore, number: int) -> str:
    lik = "n/a" if s.likelihood is None else f"{s.likelihood:.2f}"
    risk = "n/a" if s.risk is None else f"{s.risk:.2f}"
    lines = [f"**{number}. {s.component.title()} from {s.city}** {ICON[s.band]} {WORD[s.band]} risk", s.reason, "",
             f"How I scored it: chance of disruption {lik} × how much it would hurt you {s.impact:.2f} = {risk}."]
    if s.signals:
        names = {"hazard": "weather and disasters", "events": "local news", "financial": "financial health",
                 "concentration": "how concentrated your supply is"}
        lines.append("Inputs (0 = none, 1 = severe): " + ", ".join(f"{names.get(k, k)} {v:.2f}" for k, v in s.signals.items()))
    lines += ["", "**What I found**"]
    grouped: dict[str, list[str]] = {}
    for e in s.evidence:
        kind, _, text = e[1:].partition("] ") if e.startswith("[") else ("", "", e)
        grouped.setdefault(kind, []).append(text or e)
    for kind, items in grouped.items():
        lines += [f"- *{KIND_TITLES.get(kind, kind.title())}:* {t}" for t in items]
    if not grouped:
        lines.append("- Nothing came back for this supplier.")
    if s.sources:
        lines += ["", "**Sources**"] + [f"- {u}" for u in dict.fromkeys(s.sources)]
    if s.missing:
        names = {"hazard": "weather and disaster data", "events": "local news", "financial": "financial data"}
        lines += ["", "Couldn't use: " + ", ".join(names.get(m, m) for m in s.missing if m != "concentration") + "."]
    lines += ["", f"Reply `alternatives {number}` to find other suppliers or `email {number}` to draft a message."]
    return "\n".join(lines)


def alert(number: int, s: SupplierScore, before: str, replay: str = "") -> str:
    tag = f" (replay: {replay})" if replay else ""
    return (f"⚠️ **SupplyPulse alert{tag}** — **{s.component.title()}** from {s.city} changed from "
            f"{ICON[before]} {WORD[before].lower()} to {ICON[s.band]} **{WORD[s.band].lower()}** risk.\n{s.reason}\n\n"
            f"What next? `why {number}` shows the evidence · `alternatives {number}` · `email {number}`")


def plans(is_pro: bool, pro_days_left: int = 0) -> str:
    status = f"\n_You are on **Pro** ({pro_days_left} days left)._" if is_pro else ""
    return ("**SupplyPulse plans**\n\n"
            "- **Free** — one risk check, up to 5 suppliers, one alternates/email preview, no monitoring\n"
            f"- **Pro** — {PRICES['pro'][0]} FET (testnet) for 30 days: unlimited checks and suppliers, scheduled monitoring "
            "with alerts, alternates and emails. Reply `upgrade`\n"
            f"- **Deep report** — {PRICES['deep'][0]} FET (testnet) per supplier: alternates and drafted emails. "
            "Reply `buy deep N` (N = supplier number from the dashboard)" + status)
