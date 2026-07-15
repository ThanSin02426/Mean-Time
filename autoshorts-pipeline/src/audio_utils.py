from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.debug("Running: %s", " ".join(command))
    return subprocess.run(command, check=check, text=True, capture_output=True)


def ffprobe(path: str | Path) -> dict[str, Any]:
    result = run_command([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ])
    return json.loads(result.stdout)


def duration_seconds(path: str | Path) -> float:
    data = ffprobe(path)
    duration = data.get("format", {}).get("duration")
    if duration is None:
        durations = [float(stream.get("duration", 0) or 0) for stream in data.get("streams", [])]
        return max(durations, default=0.0)
    return float(duration)


def has_audio_stream(path: str | Path) -> bool:
    return any(stream.get("codec_type") == "audio" for stream in ffprobe(path).get("streams", []))


def trim_to_canonical_wav(source_path: str | Path, output_path: str | Path) -> float:
    """Decode and trim leading/trailing silence exactly once into canonical PCM audio."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    filter_expr = (
        "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB,"
        "areverse,"
        "silenceremove=start_periods=1:start_duration=0.12:start_threshold=-45dB,"
        "areverse"
    )
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source_path),
        "-af", filter_expr, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(target),
    ])
    duration = duration_seconds(target)
    if duration <= 0.2:
        raise RuntimeError(f"Canonical narration is unexpectedly short: {duration:.3f}s")
    return duration
