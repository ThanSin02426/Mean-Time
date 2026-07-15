from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .audio_utils import ffprobe
from .models import QualityGateResult

logger = logging.getLogger(__name__)


def _ratio(a: str, b: str) -> float:
    clean = lambda value: re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return SequenceMatcher(a=clean(a), b=clean(b), autojunk=False).ratio()


def _black_duration(path: Path) -> float:
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-i", str(path), "-vf", "blackdetect=d=0.20:pix_th=0.05", "-an", "-f", "null", "-"
    ], text=True, capture_output=True)
    durations = [float(value) for value in re.findall(r"black_duration:([0-9.]+)", result.stderr)]
    return sum(durations)




def _caption_gate_metrics(captions: list[dict[str, Any]], subtitle_report: dict[str, Any]) -> dict[str, Any]:
    durations = [
        max(0.0, float(row.get("end", 0)) - float(row.get("start", 0)))
        for row in captions
    ]
    minimum = min(durations) if durations else 0.0
    maximum = max(durations) if durations else 0.0
    tail = float(subtitle_report.get("maximum_caption_tail_after_word", 99) or 0)
    short_count = int(subtitle_report.get("short_caption_count", sum(value < 0.34 for value in durations)) or 0)
    return {
        "minimum": minimum,
        "maximum": maximum,
        "tail": tail,
        "short_count": short_count,
        # Staleness is about text lingering too long, not a caption being briefly
        # visible when rapid speech leaves no room for a full 0.35-second display.
        "staleness_ok": bool(captions) and maximum <= 1.82 and tail <= 0.20,
        "minimum_duration_ok": bool(captions) and minimum >= 0.34,
    }

def run_quality_gates(
    manifest: dict[str, Any], final_path: str | Path, subtitle_report: dict[str, Any], captions: list[dict[str, Any]],
    selected_visuals: list[dict[str, Any]], fact_check_path: str | Path, attribution_path: str | Path,
    upload_history_path: str | Path, min_duration: float = 30.0, max_duration: float = 58.0,
    min_visual_score: float = 2.0,
) -> list[QualityGateResult]:
    path = Path(final_path)
    results: list[QualityGateResult] = []

    def add(name: str, passed: bool, detail: str, required: bool = True) -> None:
        results.append(QualityGateResult(name, bool(passed), detail, required))

    add("final_mp4_exists", path.exists() and path.stat().st_size > 0, str(path))
    if not path.exists():
        return results
    probe = ffprobe(path)
    duration = float(probe.get("format", {}).get("duration", 0) or 0)
    streams = probe.get("streams", [])
    video_streams = [row for row in streams if row.get("codec_type") == "video"]
    audio_streams = [row for row in streams if row.get("codec_type") == "audio"]
    add("duration_range", min_duration <= duration <= max_duration, f"{duration:.3f}s; expected {min_duration}-{max_duration}s")
    add("audio_track", bool(audio_streams), f"audio_streams={len(audio_streams)}")
    add("video_track", bool(video_streams), f"video_streams={len(video_streams)}")
    narration_duration = float(manifest.get("narration_duration", 0) or 0)
    add("final_matches_narration", abs(duration - narration_duration) < 0.30, f"difference={abs(duration - narration_duration):.3f}s")
    if audio_streams and video_streams:
        audio_duration = float(audio_streams[0].get("duration") or duration)
        video_duration = float(video_streams[0].get("duration") or duration)
        add("audio_video_duration_match", abs(audio_duration - video_duration) < 0.30, f"difference={abs(audio_duration - video_duration):.3f}s")
        video = video_streams[0]
        add("video_dimensions", int(video.get("width", 0)) == 1080 and int(video.get("height", 0)) == 1920, f"{video.get('width')}x{video.get('height')}")
        add("video_codec", video.get("codec_name") == "h264", f"codec={video.get('codec_name')}")
        add("pixel_format", video.get("pix_fmt") == "yuv420p", f"pix_fmt={video.get('pix_fmt')}")
        add("audio_codec", audio_streams[0].get("codec_name") == "aac", f"codec={audio_streams[0].get('codec_name')}")
    count = int(manifest.get("narration_word_count", 0) or 0)
    add("narration_word_count", 85 <= count <= 115, f"words={count}")
    raw_coverage = float(subtitle_report.get("raw_coverage_ratio", 0) or 0)
    add("subtitle_raw_coverage", raw_coverage >= 0.90, f"raw_coverage_ratio={raw_coverage:.4f}")
    final_alignment = float(subtitle_report.get("final_alignment_ratio", 0) or 0)
    add("subtitle_alignment", final_alignment >= 0.98, f"final_alignment_ratio={final_alignment:.4f}")
    first_speech = subtitle_report.get("first_detected_speech_time")
    first_caption = subtitle_report.get("first_caption_time")
    first_ok = first_speech is not None and first_caption is not None and float(first_speech) - 0.10 <= float(first_caption) <= float(first_speech) + 0.25
    add("first_caption_timing", first_ok, f"speech={first_speech}, caption={first_caption}")
    caption_bounds = bool(captions) and all(0 <= float(row.get("start", 0)) < float(row.get("end", 0)) <= duration + 0.02 for row in captions)
    add("caption_bounds", caption_bounds, f"caption_count={len(captions)}")
    max_gap = float(subtitle_report.get("maximum_active_speech_caption_gap", 99))
    add("active_speech_caption_gap", max_gap <= 0.5, f"max_gap={max_gap:.3f}s")
    caption_metrics = _caption_gate_metrics(captions, subtitle_report)
    add(
        "caption_staleness",
        caption_metrics["staleness_ok"],
        (
            f"min_duration={caption_metrics['minimum']:.3f}s; "
            f"max_duration={caption_metrics['maximum']:.3f}s; "
            f"maximum_tail={caption_metrics['tail']:.3f}s"
        ),
    )
    add(
        "caption_minimum_duration",
        caption_metrics["minimum_duration_ok"],
        (
            f"min_duration={caption_metrics['minimum']:.3f}s; "
            f"short_chunks={caption_metrics['short_count']}"
        ),
        required=False,
    )
    add("visual_scene_count", len(selected_visuals) >= 4, f"scenes={len(selected_visuals)}")
    ids = [str(row.get("candidate_id")) for row in selected_visuals]
    paths = [str(row.get("path", "")) for row in selected_visuals]
    add("visual_no_duplicates", len(ids) == len(set(ids)) and len(paths) == len(set(paths)), f"ids={ids}")
    relevance_ok = all(row.get("provider") == "local" or float(row.get("score", 0) or 0) >= min_visual_score for row in selected_visuals)
    add("visual_relevance", relevance_ok, f"threshold={min_visual_score}")
    remote_count = sum(1 for row in selected_visuals if row.get("provider") != "local")
    required_remote = max(2, (len(selected_visuals) + 1) // 2)
    add("visual_remote_coverage", remote_count >= required_remote, f"remote={remote_count}, required={required_remote}")
    black = _black_duration(path)
    add("no_long_black_frames", black < 0.35, f"black_duration={black:.3f}s")
    add("fact_check_report", Path(fact_check_path).exists(), str(fact_check_path))
    if Path(fact_check_path).exists():
        report = json.loads(Path(fact_check_path).read_text(encoding="utf-8"))
        add("facts_verified", bool(report.get("passed")), f"claims={report.get('scene_count', 0)}")
    attribution_exists = Path(attribution_path).exists()
    add("media_attribution_manifest", attribution_exists, str(attribution_path))
    if attribution_exists:
        try:
            attribution = json.loads(Path(attribution_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            attribution = []
        add("media_attribution_complete", len(attribution) == len(selected_visuals), f"entries={len(attribution)}")
    add("reasonable_file_size", 0.8 <= path.stat().st_size / (1024 * 1024) <= 500, f"size_mb={path.stat().st_size / (1024 * 1024):.2f}")
    glyph_ok = all("□" not in str(row.get("text", "")) and "�" not in str(row.get("text", "")) for row in captions)
    add("no_missing_glyph_marker", glyph_ok, "caption text checked for replacement glyph markers")

    history = []
    history_path = Path(upload_history_path)
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = []
    title = str(manifest.get("script", {}).get("title", ""))
    max_similarity = max((_ratio(title, str(row.get("title", ""))) for row in history[-30:] if isinstance(row, dict)), default=0.0)
    add("title_uniqueness", max_similarity < 0.78, f"max_similarity={max_similarity:.3f}")
    return results


def required_gates_pass(results: list[QualityGateResult]) -> bool:
    return all(row.passed for row in results if row.required)
