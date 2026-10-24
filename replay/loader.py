"""Recorded scenarios replayed through the normal pipeline (always labelled as replays)."""
import json
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).parent / "scenarios"


def available() -> list[str]:
    return sorted(p.stem for p in DIR.glob("*.json"))


def load(name: str) -> dict | None:
    path = DIR / f"{Path(name).name}.json"  # basename only: no path traversal
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data["as_of_dt"] = datetime.fromisoformat(data["as_of"])
    return data
