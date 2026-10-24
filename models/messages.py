"""Shared message models passed between SupplyPulse agents."""
from uagents import Model


class SupplierRec(Model):
    id: str
    component: str
    name: str
    city: str
    country: str = ""
    lat: float | None = None
    lon: float | None = None
    criticality: str = "medium"  # low | medium | high
    stock_days: int = 30
    has_backup: bool = False
    lead_time_days: int = 21
    company: str = ""  # legal/listed company name, if the user gave one (enables the financial signal)


class PortRec(Model):
    name: str
    lat: float
    lon: float


class ScanStart(Model):
    scan_id: str
    user: str
    port: PortRec | None = None
    suppliers: list[SupplierRec]
    deadline_s: float = 20.0
    replay: str = ""  # scenario name when replaying recorded data
    monitor: bool = False


class HazardRequest(Model):
    scan_id: str
    supplier_id: str
    lat: float
    lon: float
    port_lat: float | None = None
    port_lon: float | None = None
    replay: str = ""
    city: str = ""
    port_city: str = ""


class SignalRequest(Model):
    """Events / financial request for one supplier."""
    scan_id: str
    supplier_id: str
    city: str
    port_city: str = ""
    company: str = ""
    replay: str = ""


class SignalResult(Model):
    scan_id: str
    supplier_id: str
    kind: str = "hazard"  # hazard | events | financial
    signal: float | None = None  # None = unavailable (never treated as low risk)
    evidence: list[str] = []
    sources: list[str] = []
    note: str = ""  # short plain-language remark shown to the user (never raw errors or URLs)
    via: str = ""  # names of the data sources actually used, e.g. "Open-Meteo, GDACS"


class SupplierScore(Model):
    supplier_id: str
    name: str
    component: str
    city: str
    band: str  # green | amber | red | unknown
    risk: float | None = None
    likelihood: float | None = None
    impact: float = 0.0
    reason: str = ""
    evidence: list[str] = []
    sources: list[str] = []
    missing: list[str] = []
    signals: dict[str, float] = {}


class ScanReport(Model):
    scan_id: str
    user: str
    overall_band: str
    overall_score: float | None = None
    scores: list[SupplierScore]
    coverage: list[str] = []
    timed_out: bool = False
    replay: str = ""
    monitor: bool = False


class MitigationRequest(Model):
    user: str
    kind: str  # alternatives | email | inquiry
    supplier: SupplierRec
    reason: str = ""
    evidence: list[str] = []
    alternative: str = ""  # alternate supplier description, for kind == inquiry
    port_city: str = ""
    port_lat: float | None = None
    port_lon: float | None = None


class MitigationResult(Model):
    user: str
    kind: str
    supplier_id: str
    text: str
    ok: bool = True
    alternatives: list[str] = []  # short descriptions, indexed from 1 by the user


class MonitorTick(Model):
    ts: float
