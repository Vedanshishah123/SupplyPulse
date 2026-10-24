"""Conversation engine: onboarding, edits, scans, drill-down, mitigation, plans, replay, alerts.

Pure of uAgents types apart from the shared models and billing helpers, so it is
unit-testable. Also acts as the 'supplier graph': per-user supplier data lives in Session.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from adapters import sources
from agents import billing
from models.messages import MitigationRequest, PortRec, ScanReport, SupplierRec
from onboarding.extract import extract
from render import text as cards
from replay import loader

HELP = ("**SupplyPulse** watches your suppliers and ports for disruption risk.\n\n"
        "Describe your supply chain in plain language, e.g. _\"I make cotton garments in Tiruppur. "
        "Fabric comes from a mill in Erode, zippers from Shenzhen. I ship through Chennai port.\"_\n\n"
        "Commands: `confirm`, `scan`, `why N`, `alternatives N`, `email N`, `inquire N`, `plans`, `upgrade`, "
        "`alerts`, `replay <scenario>` / `replay off`, `delete my data`, `help`.")

DOMESTIC_LEAD_DAYS, ABROAD_LEAD_DAYS = 7, 25


def _dump(m) -> dict:
    return m.model_dump() if hasattr(m, "model_dump") else m.dict()


@dataclass
class Session:
    suppliers: list[SupplierRec] = field(default_factory=list)
    port: PortRec | None = None
    stage: str = "new"  # new | confirming | ready
    replay: str = ""
    scans_done: int = 0
    free_preview_for: str = ""  # supplier id that used the free alternates/email preview
    last_bands: dict[str, str] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    alt_for: str = ""
    last_report: ScanReport | None = None  # not persisted
    last_output: str = ""  # last dashboard / mitigation text, for `results` (not persisted)

    def to_dict(self) -> dict:
        return {"suppliers": [_dump(s) for s in self.suppliers], "port": _dump(self.port) if self.port else None,
                "stage": self.stage, "replay": self.replay, "scans_done": self.scans_done,
                "free_preview_for": self.free_preview_for, "last_bands": self.last_bands,
                "alerts": self.alerts, "alternatives": self.alternatives, "alt_for": self.alt_for}

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(suppliers=[SupplierRec(**s) for s in d.get("suppliers", [])],
                   port=PortRec(**d["port"]) if d.get("port") else None,
                   **{k: d[k] for k in ("stage", "replay", "scans_done", "free_preview_for", "last_bands",
                                         "alerts", "alternatives", "alt_for") if k in d})


StartScan = Callable[..., Awaitable[str]]


def apply_edit(suppliers: list[SupplierRec], text: str) -> str | None:
    """Parse '<component|number>: stock=10 critical=high backup=yes lead=30 company=YKK Corp'."""
    m = re.match(r"^\s*(?P<target>[\w ]+?)\s*:\s*(?P<rest>.+)$", text)
    if not m:
        return None
    target = m.group("target").lower()
    sup = next((s for i, s in enumerate(suppliers, 1)
                if target == str(i) or target in s.component.lower()), None)
    if not sup:
        return None
    rest = m.group("rest")
    changes = []
    if (cm := re.search(r"company\s*=\s*(.+?)(?=\s+\w+\s*=|$)", rest, re.I)):
        sup.company = cm.group(1).strip()
        changes.append(f"company={sup.company}")
        rest = rest.replace(cm.group(0), " ")
    for k, v in re.findall(r"(\w+)\s*=\s*(\w+)", rest):
        k, v = k.lower(), v.lower()
        if k == "stock" and v.isdigit():
            sup.stock_days = int(v)
        elif k in ("critical", "criticality") and v in ("low", "medium", "high"):
            sup.criticality = v
        elif k == "backup" and v in ("yes", "no", "true", "false"):
            sup.has_backup = v in ("yes", "true")
        elif k == "lead" and v.isdigit():
            sup.lead_time_days = int(v)
        else:
            continue
        changes.append(f"{k}={v}")
    return f"Updated **{sup.component}**: {', '.join(changes)}" if changes else None


class Dialog:
    def __init__(self, start_scan: StartScan,
                 request_mitigation: Callable[[MitigationRequest], Awaitable[None]] | None = None,
                 request_payment: Callable[[str, str, str], Awaitable[None]] | None = None,
                 on_change: Callable[[], None] = lambda: None):
        self.start_scan = start_scan
        self.request_mitigation = request_mitigation
        self.request_payment = request_payment
        self.on_change = on_change  # persistence hook
        self.sessions: dict[str, Session] = {}

    # ------------------------------------------------------------ onboarding
    async def _build(self, text: str) -> Session | None:
        ex = await extract(text)
        if not ex.suppliers:
            return None
        sess = Session(stage="confirming")
        port_cc = ""
        if ex.port_city:
            g = await sources.geocode(ex.port_city)
            if g.ok:
                sess.port = PortRec(name=ex.port_city, lat=g.value[0], lon=g.value[1])
                port_cc = g.value[2]
        for i, raw in enumerate(ex.suppliers, 1):
            g = await sources.geocode(raw["city"])
            lat, lon, cc = (g.value if g.ok else (None, None, raw.get("country", "")))
            sess.suppliers.append(SupplierRec(
                id=f"s{i}", component=raw["component"], name=raw.get("name") or raw["component"],
                city=raw["city"], country=cc, lat=lat, lon=lon,
                lead_time_days=DOMESTIC_LEAD_DAYS if cc and cc == port_cc else ABROAD_LEAD_DAYS))
        return sess

    # ------------------------------------------------------------ helpers
    def _supplier_for(self, sess: Session, n: int):
        """Dashboard numbering: position in the last report. Returns (supplier, score) or (None, msg)."""
        if not sess.last_report or not 1 <= n <= len(sess.last_report.scores):
            return None, "Run a scan first, then use a supplier number from the dashboard."
        score = sess.last_report.scores[n - 1]
        sup = next((s for s in sess.suppliers if s.id == score.supplier_id), None)
        return (sup, score) if sup else (None, "That supplier is no longer in your list.")

    def _mitigation_allowed(self, user: str, sess: Session, sid: str) -> str | None:
        if billing.is_pro(user) or billing.has_deep(user, sid):
            return None
        if billing.FREE_MITIGATION_PREVIEWS > 0 and sess.free_preview_for in ("", sid):
            sess.free_preview_for = sid
            self.on_change()
            return None
        return ("Alternates and drafted emails are part of **Pro**, or a **Deep report** for this supplier. "
                "Your free preview is used.\n\n" + cards.plans(False))

    async def _mitigate(self, user: str, sess: Session, kind: str, sup: SupplierRec, score, alternative: str = ""):
        gate = self._mitigation_allowed(user, sess, sup.id)
        if gate:
            return gate
        if not self.request_mitigation:
            return "Mitigation is not available in this build."
        await self.request_mitigation(MitigationRequest(
            user=user, kind=kind, supplier=sup, reason=score.reason, evidence=score.evidence[:3],
            alternative=alternative, port_city=sess.port.name if sess.port else "",
            port_lat=sess.port.lat if sess.port else None, port_lon=sess.port.lon if sess.port else None))
        return "Searching… (this can take up to 30s)" if kind == "alternatives" else "Drafting…"

    # ------------------------------------------------------------ main entry
    async def handle(self, user: str, text: str) -> str:
        t = text.strip()
        low = t.lower()
        sess = self.sessions.get(user)
        if low in ("help", "hi", "hello", "start"):
            return HELP
        if low.startswith("echo "):  # connectivity check
            return t[5:]
        if low in ("delete my data", "delete data"):
            self.sessions.pop(user, None)
            self.on_change()
            return "Done. Your supplier data has been deleted."
        if low in ("plans", "plan", "pricing"):
            ent = billing.entitlements.get(user, {})
            left = max(0, int((ent.get("pro_until", 0) - time.time()) / 86400))
            return cards.plans(billing.is_pro(user), left)
        if low in ("upgrade", "pay", "subscribe"):
            await self.request_payment(user, "pro", "")
            return "Sending a testnet payment request for **Pro**…"
        if sess is None or low in ("restart", "start over"):
            sess = await self._build(t)
            if sess is None:
                return ("I couldn't find suppliers in that. Try: _\"Zippers from Shenzhen, "
                        "dyes from Ankleshwar, I ship through Chennai port.\"_\n\n" + HELP)
            self.sessions[user] = sess
            self.on_change()
            msg = cards.supplier_map(sess.suppliers, sess.port.name if sess.port else None)
            missing = [s.city for s in sess.suppliers if s.lat is None]
            if missing:
                msg += f"\n\n⚠️ I couldn't locate: {', '.join(missing)}. They will be skipped in scans."
            return msg

        # ---- payments
        if (m := re.match(r"buy deep\s+(\d+)$", low)):
            sup, score = self._supplier_for(sess, int(m.group(1)))
            if sup is None:
                return score
            await self.request_payment(user, "deep", sup.id)
            return f"Sending a testnet payment request for the **{sup.component}** deep report…"

        # ---- replay mode
        if low.startswith("replay"):
            arg = low[6:].strip()
            if arg in ("", "list"):
                return "Replay scenarios: " + ", ".join(f"`{n}`" for n in loader.available()) + \
                    "\nUse `replay <name>` to switch on, `replay off` to return to live data, `replay alert` to " \
                    "show how a monitoring alert looks for the active scenario."
            if arg == "off":
                sess.replay = ""
                self.on_change()
                return "Replay mode off. `scan` now uses live data."
            if arg == "alert":
                if not sess.replay or not sess.last_bands:
                    return "Turn on a replay and run a live `scan` first, so there is a baseline to compare."
                await self.start_scan(user, sess, monitor=True)
                return "Simulating a monitoring check against the replay…"
            if loader.load(arg) is None:
                return f"Unknown scenario. Available: {', '.join(loader.available())}"
            sess.replay = arg
            self.on_change()
            sc = loader.load(arg)
            return f"🎬 Replay on: **{sc['title']}**. `scan` will use this recorded event instead of live data."

        # ---- alerts
        if low == "alerts":
            return "\n\n".join(sess.alerts) if sess.alerts else "No alerts. I only alert when a supplier's risk band changes."
        if low == "dismiss":
            sess.alerts.clear()
            self.on_change()
            return "Alerts cleared."

        # ---- results on demand (fallback if the platform does not deliver agent-initiated messages)
        if low in ("results", "result", "status", "dashboard", "show results"):
            return sess.last_output or "Nothing to show yet. Run `scan` first, or wait a few more seconds."

        # ---- scans
        if low in ("confirm", "scan", "rescan"):
            if low == "confirm":
                sess.stage = "ready"
                self.on_change()
            if sess.stage != "ready":
                return "Reply `confirm` first to lock in your supplier map."
            pro = billing.is_pro(user)
            if len(sess.suppliers) > billing.FREE_MAX_SUPPLIERS and not pro:
                return f"The free plan covers up to {billing.FREE_MAX_SUPPLIERS} suppliers.\n\n" + cards.plans(False)
            if low != "confirm" and not sess.replay and not pro and sess.scans_done >= billing.FREE_SCANS:
                return "Your free scan is used.\n\n" + cards.plans(False)
            located = [s for s in sess.suppliers if s.lat is not None]
            if not located:
                return "None of your suppliers could be located, so there is nothing to scan."
            await self.start_scan(user, sess)
            return (f"Scanning {len(located)} suppliers" + (" and your port" if sess.port else "") +
                    " for risks… this takes up to 20 seconds. If the results don't show up, reply `results`.")

        # ---- drill-down and mitigation
        if (m := re.match(r"why\s+(\d+)$", low)):
            n = int(m.group(1))
            if not sess.last_report or not 1 <= n <= len(sess.last_report.scores):
                return "Run a scan first, then `why N` with a supplier number from the dashboard."
            return cards.detail(sess.last_report.scores[n - 1], n)
        if (m := re.match(r"(alternatives|alternates|email)\s+(\d+)$", low)):
            sup, score = self._supplier_for(sess, int(m.group(2)))
            if sup is None:
                return score
            return await self._mitigate(user, sess, "alternatives" if m.group(1) != "email" else "email", sup, score)
        if (m := re.match(r"inquire\s+(\d+)$", low)):
            n = int(m.group(1))
            if not sess.alternatives or not 1 <= n <= len(sess.alternatives):
                return "Ask for `alternatives N` first, then `inquire M` with a number from that list."
            sup = next((s for s in sess.suppliers if s.id == sess.alt_for), None)
            score = next((x for x in (sess.last_report.scores if sess.last_report else []) if x.supplier_id == sess.alt_for), None)
            if sup is None or score is None:
                return "Run a scan and ask for alternatives again."
            return await self._mitigate(user, sess, "inquiry", sup, score, alternative=sess.alternatives[n - 1])

        if sess.stage == "confirming":
            note = apply_edit(sess.suppliers, t)
            if note:
                self.on_change()
                return note + "\n\n" + cards.supplier_map(sess.suppliers, sess.port.name if sess.port else None)
            # Not an edit: if it reads like a new supply-chain description, replace the unconfirmed map.
            fresh = await self._build(t)
            if fresh is not None:
                self.sessions[user] = fresh
                self.on_change()
                return ("Replaced your unconfirmed supplier map with this new description.\n\n" +
                        cards.supplier_map(fresh.suppliers, fresh.port.name if fresh.port else None))
            return ("I didn't understand that edit. Use `zippers: stock=10 critical=high backup=yes lead=30` "
                    "or `confirm`, or describe your supply chain again to start over.")
        return "Try `scan`, `why N`, `alternatives N`, `plans`, or `delete my data`. `help` lists everything."

    # ------------------------------------------------------------ results coming back
    def on_report(self, report: ScanReport) -> tuple[str | None, bool]:
        """Returns (text to send or None, is_alert). Monitoring scans only speak when a band changed."""
        sess = self.sessions.get(report.user)
        if sess is None:
            return cards.dashboard(report), False
        sess.last_report = report
        new = {s.supplier_id: s.band for s in report.scores}
        if report.monitor:
            changes = [(i, s, sess.last_bands[s.supplier_id]) for i, s in enumerate(report.scores, 1)
                       if s.supplier_id in sess.last_bands and sess.last_bands[s.supplier_id] != s.band
                       and "unknown" not in (sess.last_bands[s.supplier_id], s.band)]
            if not report.replay:
                sess.last_bands = new  # replay checks never rewrite the live baseline
            if not changes:
                self.on_change()
                return None, False
            text = "\n\n".join(cards.alert(i, s, before, report.replay) for i, s, before in changes)
            sess.alerts.append(text)
            sess.last_output = text
            self.on_change()
            return text, True
        if not report.replay:
            sess.last_bands = new
            sess.scans_done += 1
        sess.last_output = cards.dashboard(report)
        self.on_change()
        return sess.last_output, False

    def on_mitigation(self, res) -> str:
        sess = self.sessions.get(res.user)
        if sess and res.alternatives:
            sess.alternatives, sess.alt_for = list(res.alternatives), res.supplier_id
            self.on_change()
        text = res.text
        if sess and sess.replay and res.kind in ("email", "inquiry"):
            text += ("\n\n⚠️ _Drafted while replay mode is on: the event referenced is recorded, "
                     "not live. Edit before sending._")
        if sess:
            sess.last_output = text
        return text
