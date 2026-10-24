"""Tiny JSON persistence for sessions and paid entitlements (survives restarts).

Stores the minimum: supplier lists and plan state, no message history. Writes are
atomic. Not encrypted yet (see README: supplier lists are commercially sensitive).
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def state_path() -> Path:
    return Path(os.getenv("STATE_PATH", Path(__file__).parent / "data" / "state.json"))


def save(sessions: dict, entitlements: dict, path: Path | None = None) -> None:
    path = path or state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sessions": {u: s.to_dict() for u, s in sessions.items()}, "entitlements": entitlements}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path | None = None) -> dict:
    path = path or state_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sessions": {}, "entitlements": {}}
