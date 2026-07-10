import os
import argparse
import logging
import json
from typing import List

# Pillow 10+ removed Image.ANTIALIAS, while MoviePy 1.0.3 still references it.
try:
    from PIL import Image
    if not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
except Exception:
    pass

from src.script_gen import generate_script, get_local_fallback
from src.voiceover import generate_audio
from src.subtitles import transcribe_audio_with_whisper
from src.video_assembly import assemble_video
from src.uploader import upload_video, YouTubeAuthenticationError
from src.audio_utils import trim_silence_for_caption_sync, audio_duration_seconds
from src import topic_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("autoshorts")

MIN_DURATION_SECONDS = float(os.environ.get("MIN_FINAL_DURATION_SECONDS", "30"))
MAX_DURATION_SECONDS = float(os.environ.get("MAX_FINAL_DURATION_SECONDS", "58"))
MIN_NARRATION_WORDS = int(os.environ.get("MIN_NARRATION_WORDS", "95"))
TARGET_NARRATION_WORDS = int(os.environ.get("TARGET_NARRATION_WORDS", "108"))


def _word_count(text: str) -> int:
    return len([w for w in str(text or "").split() if w.strip()])


def _narration_text(script_data: dict) -> str:
    full_text = str(script_data.get("script") or "").strip()
    if full_text:
        return full_text
    return " ".join(str(s.get("text", "")) for s in script_data.get("scenes", [])).strip()


def _extend_narration(topic: str, current: str, target_words: int = TARGET_NARRATION_WORDS) -> str:
    """Deterministically extend too-short narration so TTS cannot produce a 3-second Short."""
    current = str(current or "").strip()
    if _word_count(current) >= target_words:
        return current
    fallback = get_local_fallback(topic).get("script", "")
    add_lines = [
        "Here is the part that makes it even stranger.",
        "The comparison sounds impossible at first, but it helps explain the scale.",
        "Most people hear the fact once and forget how extreme it really is.",
        "That is why scientists keep studying it, and why it still feels unreal.",
        "Save this one, because it changes how you look at the topic.",
    ]
    pieces = [current, fallback] + add_lines
    words: List[str] = []
    for piece in pieces:
        for word in str(piece).split():
            words.append(word)
            if len(words) >= target_words:
                return " ".join(words)
    return " ".join(words)


def _generate_final_narration_audio(full_text: str, raw_audio_path: str, final_audio_path: str, edge_json_path: str) -> float:
    logger.info("Generating TTS narration audio...")
    generate_audio(full_text, raw_audio_path, edge_json_path)
    logger.info("Raw TTS audio saved to: %s", raw_audio_path)
    trim_silence_for_caption_sync(raw_audio_path, final_audio_path)
    duration = audio_duration_seconds(final_audio_path) or 0.0
    logger.info("Final narration audio duration: %.2fs", duration)
    return duration


def _probe_video_duration(path: str) -> float:
    try:
        from moviepy.editor import VideoFileClip
        clip = VideoFileClip(path)
        duration = float(clip.duration)
        clip.close()
        return duration
    except Exception as exc:
        raise RuntimeError(f"Could not probe final MP4 duration: {exc}") from exc


def _write_metadata(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def process_topic(topic: str, upload: bool = True):
    logger.info("=== Starting Pipeline for Topic: '%s' ===", topic)
    os.makedirs("output", exist_ok=True)
    temp_files: List[str] = []
    metadata = {
        "topic": topic,
        "upload_attempted": False,
        "youtube_url": None,
        "upload_status": "not_started",
        "quality_gate_passed": False,
    }

    try:
        script_data = generate_script(topic)
        title = script_data.get("title", "YouTube Short")
        description = script_data.get("description", "#Shorts")
        tags = script_data.get("tags", [])
        scenes = script_data.get("scenes", [])

        full_text = _extend_narration(topic, _narration_text(script_data), TARGET_NARRATION_WORDS)
        script_data["script"] = full_text
        narration_words = _word_count(full_text)
        if narration_words < MIN_NARRATION_WORDS:
            raise RuntimeError(f"Narration still too short after extension: {narration_words} words")
        logger.info("Safe narration word count: %s", narration_words)

        from src.visuals import generate_visuals
        visual_data = generate_visuals(scenes, output_dir="temp_images", topic=topic)
        temp_files.extend([v["path"] for v in visual_data if os.path.exists(v.get("path", ""))])
        logger.info("Generated %s visuals.", len(visual_data))
        if not visual_data:
            raise RuntimeError("No visual scenes generated.")

        raw_audio_path = "temp_audio_raw.mp3"
        final_audio_path = "output/narration_final.mp3"
        edge_json_path = "temp_edge_captions.json"
        synced_json_path = "output/captions.json"

        duration = _generate_final_narration_audio(full_text, raw_audio_path, final_audio_path, edge_json_path)
        temp_files.extend([raw_audio_path, edge_json_path])

        if duration < MIN_DURATION_SECONDS:
            logger.warning(
                "Narration duration %.2fs is under %.2fs; extending narration and regenerating TTS once.",
                duration, MIN_DURATION_SECONDS,
            )
            full_text = _extend_narration(topic, full_text, TARGET_NARRATION_WORDS + 25)
            script_data["script"] = full_text
            narration_words = _word_count(full_text)
            duration = _generate_final_narration_audio(full_text, raw_audio_path, final_audio_path, edge_json_path)

        logger.info("Final narration audio used for BOTH Whisper and video: %s", final_audio_path)
        logger.info("Narration word count: %s", narration_words)
        logger.info("Trimmed narration duration: %.2fs", duration)

        if duration < MIN_DURATION_SECONDS:
            raise RuntimeError(
                f"Narration too short after retry: {duration:.2f}s. Refusing to assemble/upload."
            )
        if duration > MAX_DURATION_SECONDS + 2:
            logger.warning("Narration %.2fs is long; video assembly will cap at %.2fs.", duration, MAX_DURATION_SECONDS)

        logger.info("Generating synced subtitles from the FINAL narration audio using Whisper/faster-whisper...")
        word_timings = transcribe_audio_with_whisper(
            audio_path=final_audio_path,
            output_json_path=synced_json_path,
            reference_text=full_text,
        )
        logger.info("Synced captions saved to: %s (%s words)", synced_json_path, len(word_timings))
        if len(word_timings) < max(8, int(narration_words * 0.35)):
            logger.warning("Caption word count is low; fallback timings may have been used.")

        output_video_path = "output/final_short.mp4"
        assemble_video(final_audio_path, word_timings, visual_data, output_video_path)
        if not os.path.exists(output_video_path):
            raise FileNotFoundError(f"Video assembly failed to produce {output_video_path}")

        final_duration = _probe_video_duration(output_video_path)
        file_size_mb = os.path.getsize(output_video_path) / (1024 * 1024)

        # Debug contact sheet for artifact review.
        try:
            image_paths = [v["path"] for v in visual_data if v.get("type") == "image" and os.path.exists(v.get("path", ""))]
            if image_paths:
                from PIL import Image
                thumb_w, thumb_h = 270, 480
                contact = Image.new("RGB", (thumb_w * len(image_paths), thumb_h), (10, 10, 15))
                for idx, imp in enumerate(image_paths):
                    im = Image.open(imp).convert("RGB").resize((thumb_w, thumb_h))
                    contact.paste(im, (idx * thumb_w, 0))
                contact.save("output/debug_contact_sheet.jpg")
        except Exception as exc:
            logger.warning("Failed to create debug contact sheet: %s", exc)

        quality_gate_passed = True
        error_msg = ""
        if final_duration < MIN_DURATION_SECONDS:
            quality_gate_passed = False
            error_msg = f"Video is too short ({final_duration:.2f}s)."
        elif final_duration > MAX_DURATION_SECONDS:
            quality_gate_passed = False
            error_msg = f"Video is too long ({final_duration:.2f}s)."
        elif not word_timings:
            quality_gate_passed = False
            error_msg = "Synced captions JSON has zero words."
        elif file_size_mb < 0.5:
            quality_gate_passed = False
            error_msg = "Output MP4 is suspiciously small."
        elif abs(final_duration - min(duration, MAX_DURATION_SECONDS)) > 1.25:
            quality_gate_passed = False
            error_msg = f"Video duration ({final_duration:.2f}s) differs from narration basis ({min(duration, MAX_DURATION_SECONDS):.2f}s)."

        metadata.update({
            "title": title,
            "description": description,
            "tags": tags,
            "narration_word_count": narration_words,
            "narration_duration": duration,
            "final_video_duration": final_duration,
            "scene_count": len(visual_data),
            "scene_duration": final_duration / max(1, len(visual_data)),
            "media_sources": [{"source": v.get("source"), "type": v.get("type"), "author": v.get("author")} for v in visual_data],
            "caption_word_count": len(word_timings),
            "quality_gate_passed": quality_gate_passed,
            "quality_error": error_msg,
            "upload_attempted": bool(upload and quality_gate_passed),
            "output_video_path": output_video_path,
            "output_file_size_mb": round(file_size_mb, 3),
        })
        _write_metadata("output/metadata.json", metadata)

        logger.info("--- Pipeline Summary ---")
        logger.info("Topic: %s", topic)
        logger.info("Scenes Generated: %s", len(visual_data))
        logger.info("Narration Words: %s", narration_words)
        logger.info("Synced Caption Words: %s", len(word_timings))
        logger.info("Video Duration: %.2fs", final_duration)
        logger.info("Output MP4: %s (%.2f MB)", output_video_path, file_size_mb)
        logger.info("Quality gate passed: %s", str(quality_gate_passed).lower())

        if not quality_gate_passed:
            raise RuntimeError(f"Quality Gate Failed: {error_msg}")

        if upload:
            metadata["upload_attempted"] = True
            metadata["upload_status"] = "attempting"
            _write_metadata("output/metadata.json", metadata)
            try:
                video_url = upload_video(output_video_path, title, description, tags)
            except YouTubeAuthenticationError as exc:
                metadata["upload_status"] = "authentication_failed"
                metadata["upload_error"] = str(exc)
                metadata["recovery_artifact_available"] = os.path.exists(output_video_path)
                _write_metadata("output/metadata.json", metadata)
                logger.error(
                    "YouTube authentication failed. The rendered MP4 is preserved for the recovery artifact: %s",
                    output_video_path,
                )
                raise
            except Exception as exc:
                metadata["upload_status"] = "failed"
                metadata["upload_error"] = str(exc)
                metadata["recovery_artifact_available"] = os.path.exists(output_video_path)
                _write_metadata("output/metadata.json", metadata)
                raise
            metadata["youtube_url"] = video_url
            metadata["upload_status"] = "uploaded"
            _write_metadata("output/metadata.json", metadata)
            try:
                topic_engine.record_uploaded_video(video_url, title, topic)
            except Exception as exc:
                logger.warning("Could not record upload history: %s", exc)
            try:
                if visual_data and visual_data[0].get("type") == "image":
                    from src.uploader import upload_thumbnail
                    video_id = video_url.split("/")[-1]
                    upload_thumbnail(video_id, visual_data[0]["path"])
            except Exception as exc:
                logger.warning("Thumbnail upload skipped/failed: %s", exc)
            logger.info("=== Pipeline Completed Successfully! URL: %s ===", video_url)
        else:
            metadata["upload_status"] = "skipped"
            _write_metadata("output/metadata.json", metadata)
            logger.info("=== Pipeline Completed Successfully! Video saved to %s (Upload skipped) ===", output_video_path)
    except Exception as e:
        metadata["pipeline_error"] = str(e)
        _write_metadata("output/metadata.json", metadata)
        logger.error("Pipeline failed: %s", e, exc_info=True)
        raise
    finally:
        logger.info("Cleaning up temporary files...")
        for f in temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception as e:
                    logger.warning("Could not remove %s: %s", f, e)
        if os.path.exists("temp_images"):
            try:
                for p in os.listdir("temp_images"):
                    pp = os.path.join("temp_images", p)
                    if os.path.isfile(pp):
                        os.remove(pp)
                os.rmdir("temp_images")
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="AutoShorts: End-to-End YouTube Shorts Automation Pipeline")
    parser.add_argument("--sync-analytics", action="store_true", help="Best-effort sync of matured YouTube stats")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--topic", type=str, help="Single topic to generate a short for")
    group.add_argument("--topics-file", type=str, help="Topic queue file. First topic is used, replacement is appended.")
    parser.add_argument("--no-upload", action="store_true", help="Skip YouTube upload and save artifact locally")
    args = parser.parse_args()

    if args.sync_analytics:
        topic_engine.sync_analytics_if_possible()
        if not args.topic and not args.topics_file:
            return

    if args.topic:
        topic = args.topic.strip()
    elif args.topics_file:
        topic = topic_engine.pop_topic_and_refresh_queue(args.topics_file)
    else:
        parser.error("Either --topic, --topics-file, or --sync-analytics must be provided.")
    process_topic(topic, upload=not args.no_upload)


if __name__ == "__main__":
    main()
