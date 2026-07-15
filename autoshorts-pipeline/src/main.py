from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .atomic_io import atomic_write_json, read_json
from .config import Settings
from .fact_check import build_fact_check
from .models import RunManifest, utc_now
from .quality_gates import required_gates_pass, run_quality_gates
from .script_gen import generate_script, repair_script_sources
from .subtitles import render_subtitle_test, transcribe_and_align
from .topic_engine import QueueManager, QueueReservation
from .uploader import check_auth, upload_video
from .video_assembly import allocate_scene_durations_from_alignment, assemble_video, smoke_render
from .visuals import select_visuals
from .voiceover import generate_canonical_narration

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("autoshorts")


def _append_upload_history(path: Path, manifest: RunManifest) -> None:
    history = read_json(path, [])
    history.append({
        "run_id": manifest.run_id,
        "video_id": manifest.youtube_url.rstrip("/").split("/")[-1],
        "url": manifest.youtube_url,
        "title": manifest.script.get("title", ""),
        "topic": manifest.selected_topic,
        "niche": manifest.niche,
        "published_at": utc_now(),
        "visual_ids": [row.get("candidate_id") for row in manifest.selected_visuals],
    })
    atomic_write_json(path, history[-1000:])


def run_pipeline(topic: str | None, publish: bool, settings: Settings, network_fact_check: bool = True) -> RunManifest:
    settings.prepare()
    manifest_path = settings.output_dir / "run_manifest.json"
    manifest = RunManifest()
    manifest.save(manifest_path)
    queue = QueueManager(settings.queue_file.parent, settings.queue_file.name)
    reservation: QueueReservation | None = None

    try:
        reservation = queue.reserve(topic)
        manifest.topic_source = reservation.source
        manifest.selected_topic = reservation.topic
        manifest.niche = reservation.niche
        manifest.queue_transaction_status = {"status": reservation.status, "transaction_id": reservation.transaction_id, "source": reservation.source}
        manifest.status = "reserved"
        manifest.save(manifest_path)

        if publish:
            manifest.upload_status = "oauth_preflight"
            manifest.save(manifest_path)
            check_auth()

        script = generate_script(reservation.topic, settings.upload_history_file)
        if publish and script.get("generation_mode") != "gemini":
            raise RuntimeError("Publishing is blocked because the script was not generated in source-backed Gemini mode")
        manifest.script = script
        manifest.niche = script.get("niche", manifest.niche)
        manifest.scene_list = script["scenes"]
        manifest.exact_narration_text = script["narration"]
        manifest.narration_word_count = int(script["narration_word_count"])
        manifest.status = "script_ready"
        manifest.save(manifest_path)

        fact_report = build_fact_check(script, settings.output_dir / "fact_check.json", network_verify=network_fact_check)
        if not fact_report["passed"] and script.get("generation_mode") == "gemini":
            failed_scenes = [row.get("scene") for row in fact_report.get("claims", []) if not row.get("passed")]
            logger.warning(
                "Fact-check failed for scenes %s; performing one batched source repair",
                failed_scenes,
            )
            script = repair_script_sources(script, fact_report)
            manifest.script = script
            manifest.scene_list = script["scenes"]
            manifest.exact_narration_text = script["narration"]
            manifest.narration_word_count = int(script["narration_word_count"])
            manifest.save(manifest_path)
            fact_report = build_fact_check(
                script,
                settings.output_dir / "fact_check.json",
                network_verify=network_fact_check,
            )
        if not fact_report["passed"]:
            failed_details = []
            for row in fact_report.get("claims", []):
                if row.get("passed"):
                    continue
                statuses = ", ".join(
                    f"{source.get('url')} ({source.get('detail')})"
                    for source in row.get("sources", [])
                ) or "no source URLs"
                failed_details.append(f"scene {row.get('scene')}: {statuses}")
            raise RuntimeError(
                "Fact-check gate failed after one batched source repair: " + "; ".join(failed_details)
            )

        raw_audio = settings.work_dir / "narration_raw.mp3"
        canonical_audio = settings.output_dir / "narration_final.wav"
        duration = generate_canonical_narration(
            manifest.exact_narration_text, raw_audio, canonical_audio, settings.voice, settings.voice_rate
        )
        manifest.narration_audio_path = str(canonical_audio)
        manifest.narration_duration = duration
        if not settings.min_duration <= duration <= settings.max_duration:
            raise RuntimeError(f"Narration duration {duration:.2f}s is outside {settings.min_duration}-{settings.max_duration}s")
        manifest.status = "narration_ready"
        manifest.save(manifest_path)

        aligned_words, chunks, subtitle_report = transcribe_and_align(
            canonical_audio, manifest.exact_narration_text, duration, settings.output_dir,
            settings.whisper_primary, settings.whisper_fallback, settings.whisper_device, settings.whisper_compute_type,
        )
        manifest.subtitle_alignment_results = subtitle_report
        scene_durations = allocate_scene_durations_from_alignment(script["scenes"], aligned_words, duration)
        for scene, scene_duration in zip(manifest.scene_list, scene_durations):
            scene["duration_seconds"] = scene_duration
        if os.getenv("RENDER_SUBTITLE_TEST", "false").lower() in {"1", "true", "yes"}:
            render_subtitle_test(canonical_audio, settings.output_dir / "captions.ass", settings.output_dir / "subtitle_test.mp4")
        manifest.status = "subtitles_ready"
        manifest.save(manifest_path)

        selected, candidates, attribution = select_visuals(
            script["scenes"], manifest.niche, settings.work_dir / "visuals", settings.output_dir,
            settings.upload_history_file, settings.min_visual_score,
        )
        manifest.selected_visuals = selected
        manifest.visual_candidates = candidates
        manifest.media_attribution = attribution
        if len(selected) < 4:
            raise RuntimeError("Fewer than four usable visual scenes were selected")
        manifest.status = "visuals_ready"
        manifest.save(manifest_path)

        final_path = settings.output_dir / "final_short.mp4"
        final_duration, music_meta = assemble_video(
            canonical_audio, script["scenes"], selected, settings.output_dir / "captions.ass",
            final_path, settings.work_dir / "render", settings.project_dir / "assets/music",
            mood=str(script.get("mood", "neutral")),
            scene_durations=scene_durations,
        )
        if music_meta:
            atomic_write_json(settings.output_dir / "music_attribution.json", music_meta)
        manifest.final_video_path = str(final_path)
        manifest.final_duration = final_duration
        manifest.status = "rendered"
        manifest.save(manifest_path)

        gate_results = run_quality_gates(
            asdict(manifest), final_path, subtitle_report, [asdict(row) for row in chunks], selected,
            settings.output_dir / "fact_check.json", settings.output_dir / "media_attribution.json",
            settings.upload_history_file, settings.min_duration, settings.max_duration, settings.min_visual_score,
        )
        manifest.quality_gate_results = [asdict(row) for row in gate_results]
        atomic_write_json(settings.output_dir / "quality_gate_report.json", manifest.quality_gate_results)
        if not required_gates_pass(gate_results):
            failed = [row.name for row in gate_results if row.required and not row.passed]
            raise RuntimeError("Quality gates failed: " + ", ".join(failed))
        manifest.status = "quality_passed"
        manifest.save(manifest_path)

        if publish:
            manifest.upload_status = "uploading"
            manifest.save(manifest_path)
            manifest.youtube_url = upload_video(
                final_path, str(script["title"]), str(script["description"]), list(script.get("tags", []))
            )
            manifest.upload_status = "uploaded"
            _append_upload_history(settings.upload_history_file, manifest)
        else:
            manifest.upload_status = "artifact_ready"

        transaction = queue.finalize(reservation, True)
        manifest.queue_transaction_status = transaction
        manifest.status = "completed"
        manifest.save(manifest_path)
        logger.info("Pipeline completed: %s", manifest.youtube_url or final_path)
        return manifest
    except Exception as exc:
        logger.exception("Pipeline failed")
        manifest.add_error(str(exc))
        if reservation is not None:
            try:
                manifest.queue_transaction_status = queue.finalize(reservation, False)
            except Exception as queue_exc:
                manifest.errors.append(f"Queue rollback status failed: {queue_exc}")
        manifest.save(manifest_path)
        raise
    finally:
        if settings.work_dir.exists() and os.getenv("KEEP_WORK_DIR", "false").lower() not in {"1", "true", "yes"}:
            shutil.rmtree(settings.work_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and optionally publish a source-checked YouTube Short")
    parser.add_argument("--topic", default="", help="Manual topic. Empty uses transactional queue mode.")
    parser.add_argument("--publish", action="store_true", help="Upload after all required quality gates pass")
    parser.add_argument("--skip-network-fact-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-render", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_render:
        report = smoke_render("output/smoke_render.mp4")
        atomic_write_json("output/smoke_render_report.json", report)
        print(json.dumps(report, indent=2))
        return
    run_pipeline(args.topic.strip() or None, args.publish, Settings(), network_fact_check=not args.skip_network_fact_check)


if __name__ == "__main__":
    main()
