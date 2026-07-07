import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def audio_duration_seconds(audio_path: str) -> Optional[float]:
    """Return audio duration using ffprobe, falling back to MoviePy."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.strip())
    except Exception as exc:
        logger.warning(f"ffprobe duration failed for {audio_path}: {exc}")
    try:
        from moviepy.editor import AudioFileClip
        clip = AudioFileClip(audio_path)
        duration = float(clip.duration)
        clip.close()
        return duration
    except Exception as exc:
        logger.warning(f"MoviePy duration failed for {audio_path}: {exc}")
        return None


def trim_silence_for_caption_sync(input_audio: str, output_audio: str) -> str:
    """Trim leading/trailing silence so Whisper captions align to the actual final narration.

    The final video must use this output file, and Whisper must transcribe this same file.
    If trimming fails, the function copies the input to the output rather than breaking the run.
    """
    os.makedirs(os.path.dirname(output_audio) or ".", exist_ok=True)
    original_duration = audio_duration_seconds(input_audio)
    threshold = os.environ.get("SILENCE_THRESHOLD_DB", "-50dB")
    start_dur = os.environ.get("SILENCE_START_DURATION", "0.12")
    stop_dur = os.environ.get("SILENCE_STOP_DURATION", "0.18")

    # Keep MP3 output for compatibility with MoviePy/ffmpeg in the existing pipeline.
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_audio,
        "-af",
        (
            f"silenceremove="
            f"start_periods=1:start_duration={start_dur}:start_threshold={threshold}:"
            f"stop_periods=1:stop_duration={stop_dur}:stop_threshold={threshold}"
        ),
        "-ac", "1", "-ar", "44100", "-codec:a", "libmp3lame", "-q:a", "3",
        output_audio,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=90)
        trimmed_duration = audio_duration_seconds(output_audio)
        if not trimmed_duration or trimmed_duration < 1.0:
            raise RuntimeError(f"trimmed audio duration invalid: {trimmed_duration}")
        logger.info(
            "Narration silence trim: original %.2fs -> final %.2fs (%s)",
            float(original_duration or 0.0), float(trimmed_duration), output_audio,
        )
    except Exception as exc:
        logger.warning(f"Silence trimming failed, copying original narration instead: {exc}")
        shutil.copyfile(input_audio, output_audio)
        trimmed_duration = audio_duration_seconds(output_audio)

    report_path = Path("output") / "audio_timing_report.json"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({
            "input_audio": input_audio,
            "output_audio": output_audio,
            "original_duration_seconds": original_duration,
            "final_duration_seconds": trimmed_duration,
            "silence_threshold_db": threshold,
        }, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Could not write audio timing report: {exc}")

    return output_audio
