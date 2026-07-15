from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .atomic_io import atomic_write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class QualityGateResult:
    name: str
    passed: bool
    detail: str
    required: bool = True


@dataclass(slots=True)
class RunManifest:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    status: str = "initializing"
    topic_source: str = ""
    selected_topic: str = ""
    niche: str = ""
    script: dict[str, Any] = field(default_factory=dict)
    exact_narration_text: str = ""
    narration_word_count: int = 0
    narration_audio_path: str = ""
    narration_duration: float = 0.0
    subtitle_alignment_results: dict[str, Any] = field(default_factory=dict)
    scene_list: list[dict[str, Any]] = field(default_factory=list)
    visual_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_visuals: list[dict[str, Any]] = field(default_factory=list)
    media_attribution: list[dict[str, Any]] = field(default_factory=list)
    final_video_path: str = ""
    final_duration: float = 0.0
    quality_gate_results: list[dict[str, Any]] = field(default_factory=list)
    upload_status: str = "not_requested"
    youtube_url: str = ""
    queue_transaction_status: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def save(self, path: str | Path) -> None:
        self.updated_at = utc_now()
        atomic_write_json(path, asdict(self))

    def add_error(self, message: str) -> None:
        self.errors.append(str(message))
        self.status = "failed"
