import json
import subprocess
from pathlib import Path

from src.audio_utils import duration_seconds, ffprobe
from src.quality_gates import run_quality_gates
from src.video_assembly import prepare_visual_segment, smoke_render


def test_stock_clip_is_looped_to_scene_duration(tmp_path):
    source = tmp_path / "source.mp4"
    target = tmp_path / "target.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc2=s=360x640:r=30:d=0.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)
    ], check=True)
    prepare_visual_segment(source, "video", 1.8, target, 360, 640)
    assert abs(duration_seconds(target) - 1.8) < 0.2


def test_smoke_render_and_ffprobe_validation(tmp_path):
    output = tmp_path / "smoke.mp4"
    report = smoke_render(output, duration=3.0)
    assert output.exists()
    assert report["width"] == 360 and report["height"] == 640
    assert report["video_codec"] == "h264"
    assert report["audio_codec"] == "aac"
    probe = ffprobe(output)
    assert any(row.get("codec_type") == "audio" for row in probe["streams"])


def test_final_duration_gate(tmp_path):
    output = tmp_path / "smoke.mp4"
    smoke_render(output, duration=3.0)
    fact = tmp_path / "fact_check.json"
    fact.write_text(json.dumps({"passed": True, "scene_count": 4}), encoding="utf-8")
    attribution = tmp_path / "media_attribution.json"
    attribution.write_text("[]", encoding="utf-8")
    history = tmp_path / "upload_history.json"
    history.write_text("[]", encoding="utf-8")
    manifest = {"narration_duration": 3.0, "narration_word_count": 90, "script": {"title": "A Unique Test Title"}}
    subtitle_report = {"final_alignment_ratio": 1.0, "maximum_active_speech_caption_gap": 0.1}
    captions = [{"text": "test words", "start": 0.1, "end": 0.8}]
    visuals = [{"candidate_id": str(i), "provider": "local", "score": 2.0} for i in range(4)]
    results = run_quality_gates(manifest, output, subtitle_report, captions, visuals, fact, attribution, history, 2.5, 3.5)
    duration_gate = next(row for row in results if row.name == "duration_range")
    assert duration_gate.passed


def test_short_caption_is_not_misclassified_as_stale():
    from src.quality_gates import _caption_gate_metrics

    captions = [
        {"text": "fast", "start": 0.0, "end": 0.22},
        {"text": "speech continues", "start": 0.22, "end": 0.92},
    ]
    report = {
        "maximum_caption_tail_after_word": 0.14,
        "short_caption_count": 1,
    }
    metrics = _caption_gate_metrics(captions, report)
    assert metrics["staleness_ok"] is True
    assert metrics["minimum_duration_ok"] is False
