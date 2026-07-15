from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .atomic_io import read_json


@dataclass(frozen=True, slots=True)
class AnalyticsSnapshot:
    enabled: bool
    reason: str
    records: tuple[dict, ...] = ()


def load_snapshot(enabled: bool = False, history_path: str | Path = "upload_history.json") -> AnalyticsSnapshot:
    """Modular analytics boundary. Disabled mode never changes topic weights or queue order."""
    if not enabled:
        return AnalyticsSnapshot(False, "analytics disabled; balanced topic policy is active")
    rows = read_json(history_path, [])
    records = tuple(row for row in rows if isinstance(row, dict))
    return AnalyticsSnapshot(True, "history loaded for future explicit analytics policy", records)
