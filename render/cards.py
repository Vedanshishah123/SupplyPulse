"""ASI:One interactive cards (https://innovationlab.fetch.ai/resources/docs/interactive-cards/asi-interactive-cards).

A card is an ordinary chat message with a second block: `MetadataContent` whose values are all strings. The
`card_payload` is a JSON *string*. A click comes back as the `selection` object we put on the button, as JSON
when the user @-mentions the agent, so `parse_selection` turns it into the text commands the dialog already
understands (`why 2`, `alternatives 2`, ...).

We use the `custom` element tree, whose schema is fully documented: section, group, heading, text, badge, button,
divider. Limits: payload <= 64 KB, nesting <= 8 levels. Anything invalid is shown by ASI:One as plain text, so the
text fallback in the chat message must stay useful on its own.

`rich=False` uses only elements and values the docs show (section, text, heading, divider, button). `rich=True`
adds badges with warning/danger variants and horizontal button groups, whose valid values the docs do not list:
check them in the Card Playground (https://asi1.ai/developer/card-playground) before relying on them.
"""
from __future__ import annotations

import json
import os

from models.messages import ScanReport
from render.text import ICON, WORD, _summary

PROTOCOL_VERSION = "1"
MAX_PAYLOAD_BYTES = 64 * 1024
BADGE_VARIANT = {"red": "danger", "amber": "warning", "green": "success", "unknown": "info"}  # rich only
ORDER = {"red": 0, "amber": 1, "unknown": 2, "green": 3}


RESULTS_WORDS = ("results", "result", "status", "dashboard", "show results")


def enabled() -> bool:
    """Cards are on by default; USE_CARDS=0 sends the plain-text version only."""
    return os.getenv("USE_CARDS", "1") == "1"


def dashboard_requested(command: str) -> bool:
    """Commands that should be answered with the dashboard card (when there is a dashboard to show)."""
    return command.strip().lower() in RESULTS_WORDS


def narration(report: ScanReport) -> str:
    """The text above the card. Short, but enough on its own if a client cannot draw the card."""
    replay = "🎬 Replay: " if report.replay else ""
    return (f"{replay}**SupplyPulse risk check** — overall risk: {ICON[report.overall_band]} "
            f"**{WORD[report.overall_band]}**. {_summary(report)}\n\nUse the buttons below, or reply `plain` for the text version.")


def button(label: str, action: str, primary: bool = False, **selection) -> dict:
    """The selection keys are ours: they are what the agent receives when the button is pressed."""
    return {"type": "button", "label": label, "primary": primary, "action": {"selection": {"action": action, **selection}}}


def metadata(kind: str, payload: dict, terminal: bool = False, width_px: int | None = 560) -> dict[str, str]:
    """The metadata block of the chat message. Every value must be a string (the wire type is dict[str, str])."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("card payload exceeds 64 KB")
    md = {"card_protocol_version": PROTOCOL_VERSION, "requires_card_interaction": "false" if terminal else "true",
          "card_kind": kind, "card_payload": body}
    if terminal:
        md["is_terminal"] = "true"
    if width_px:
        md["preferred_drawer_width_px"] = str(width_px)
    return md


def _supplier_section(i: int, s, worst: bool, rich: bool) -> dict:
    children: list[dict] = []
    if rich:
        children.append({"type": "badge", "label": WORD[s.band], "variant": BADGE_VARIANT[s.band]})
    children.append({"type": "text", "value": s.reason, "style": "body"})
    actions = [button("Why?", "why", n=i),
               button("Find alternatives", "alternatives", primary=worst and s.band in ("red", "amber"), n=i),
               button("Draft email", "email", n=i)]
    children.append({"type": "group", "direction": "horizontal", "gap": 8, "children": actions} if rich else
                    {"type": "section", "children": actions})
    return {"type": "section", "title": f"{ICON[s.band]} {s.component.title()} · {s.city}", "children": children}


def dashboard_payload(report: ScanReport, rich: bool = False) -> dict:
    """The risk check as a `custom` card: one section per supplier (worst first), then completeness and next steps."""
    ranked = sorted(enumerate(report.scores, 1), key=lambda t: (ORDER[t[1].band], t[0]))
    children: list[dict] = []
    if report.replay:
        children.append({"type": "text", "value": f"🎬 Replay mode ({report.replay}): a recorded past event, not live conditions.",
                         "style": "muted"})
    children.append({"type": "text", "value": "🔴 act now · 🟠 keep an eye on it · 🟢 fine for now · ⚪ not enough data",
                     "style": "muted"})
    for pos, (i, s) in enumerate(ranked):
        children.append(_supplier_section(i, s, worst=pos == 0, rich=rich))
    children.append({"type": "divider"})
    children.append({"type": "heading", "value": "How complete is this check?", "level": 3})
    children += [{"type": "text", "value": line, "style": "muted"} for line in report.coverage]
    if report.timed_out:
        children.append({"type": "text", "value": "⏱️ Some checks didn't finish in time. Scan again to fill the gaps.", "style": "muted"})
    children.append({"type": "divider"})
    children.append({"type": "group", "direction": "horizontal", "gap": 8, "children": [
        button("Scan again", "scan"), button("Plans", "plans")]} if rich else
        {"type": "section", "children": [button("Scan again", "scan"), button("Plans", "plans")]})
    score = "" if report.overall_score is None else f" ({report.overall_score:.2f})"
    return {"root": {"type": "section", "title": f"SupplyPulse risk check · {ICON[report.overall_band]} {WORD[report.overall_band]}{score}",
                     "subtitle": _summary(report), "children": children}}


def depth(node, level: int = 1) -> int:
    """Nesting depth of an element tree (the docs allow at most 8, counting the root as 1)."""
    kids = node.get("children", []) if isinstance(node, dict) else []
    kids = kids + [c for item in node.get("items", []) for c in item.get("children", [])] if isinstance(node, dict) else kids
    return max([level] + [depth(k, level + 1) for k in kids])


# ---------------------------------------------------------------- clicks coming back
_WITH_NUMBER = {"why", "alternatives", "email", "inquire"}
_PLAIN = {"scan", "plans", "confirm", "upgrade", "results", "alerts", "dismiss", "restart", "help"}


def parse_selection(text: str) -> str | None:
    """Turn a selection JSON ({"action": "why", "n": 2}) into the text command the dialog understands.
    Returns None when the text is not one of our selections, so ordinary chat text is left alone."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    if action in _PLAIN:
        return action
    if action in _WITH_NUMBER:
        n = data.get("n")
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            return f"{action} {n}"
    return None
