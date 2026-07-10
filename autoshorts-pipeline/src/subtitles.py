import json
import logging
import os
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _clean_word(word: str) -> str:
    word = re.sub(r"\s+", " ", str(word or "")).strip()
    # Whisper often returns leading spaces. Keep punctuation; remove only weird whitespace.
    return word


def _load_audio_duration(audio_path: str) -> Optional[float]:
    try:
        from moviepy.editor import AudioFileClip
        clip = AudioFileClip(audio_path)
        duration = float(clip.duration)
        clip.close()
        return duration
    except Exception as exc:
        logger.warning(f"Could not determine audio duration for subtitle validation: {exc}")
        return None


def _fallback_even_timings(reference_text: str, duration: Optional[float]) -> List[Dict]:
    """Emergency fallback only. Uses evenly distributed timings across actual audio duration."""
    words = [_clean_word(w) for w in str(reference_text or "").split() if _clean_word(w)]
    if not words:
        return []
    duration = float(duration or max(2.5, len(words) * 0.32))
    step = duration / max(1, len(words))
    timings = []
    for i, word in enumerate(words):
        start = i * step
        end = min(duration, start + max(0.12, step * 0.85))
        timings.append({"word": word, "start": round(start, 3), "end": round(end, 3), "source": "fallback_even"})
    return timings


def transcribe_audio_with_whisper(
    audio_path: str,
    output_json_path: str,
    reference_text: str = "",
    model_size: Optional[str] = None,
) -> List[Dict]:
    """
    Create caption timings from the ACTUAL synthesized audio, not from predicted TTS events.

    This follows the more reliable MoneyPrinter-style architecture:
    1. generate the narration audio
    2. transcribe that actual audio with an STT model
    3. use the returned timestamps for burnt-in captions

    The previous edge-tts WordBoundary timings can drift after encoding/trimming/rendering.
    Whisper/faster-whisper aligns captions to the real audio file, which is what viewers hear.
    """
    os.makedirs(os.path.dirname(output_json_path) or ".", exist_ok=True)
    duration = _load_audio_duration(audio_path)
    model_size = model_size or os.environ.get("WHISPER_MODEL", "tiny.en")
    device = os.environ.get("WHISPER_DEVICE", "cpu")
    compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

    words: List[Dict] = []
    try:
        from faster_whisper import WhisperModel

        logger.info(
            f"Transcribing actual narration audio with faster-whisper model={model_size}, "
            f"device={device}, compute_type={compute_type}"
        )
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            audio_path,
            language="en",
            word_timestamps=True,
            vad_filter=False,
            beam_size=1,
            condition_on_previous_text=False,
            initial_prompt=(reference_text[:220] if reference_text else None),
        )

        for segment in segments:
            for w in getattr(segment, "words", None) or []:
                word = _clean_word(getattr(w, "word", ""))
                if not word:
                    continue
                start = float(getattr(w, "start", 0.0) or 0.0)
                end = float(getattr(w, "end", start + 0.25) or start + 0.25)
                if duration is not None:
                    start = max(0.0, min(start, duration))
                    end = max(start + 0.08, min(end, duration))
                elif end <= start:
                    end = start + 0.25
                words.append({
                    "word": word,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "source": "faster_whisper",
                })

        # Sort and remove obviously broken timestamps.
        words = sorted(words, key=lambda x: (x["start"], x["end"]))
        cleaned: List[Dict] = []
        last_start = -1.0
        for w in words:
            if w["start"] < last_start - 0.25:
                continue
            if w["end"] <= w["start"]:
                w["end"] = w["start"] + 0.18
            cleaned.append(w)
            last_start = w["start"]
        words = cleaned

        logger.info(f"Whisper detected {len(words)} timed words from actual audio.")
    except Exception as exc:
        logger.warning(f"Whisper subtitle transcription failed; using even fallback timings. Error: {exc}", exc_info=True)
        words = _fallback_even_timings(reference_text, duration)

    if not words:
        logger.warning("Whisper returned zero words; using even fallback timings.")
        words = _fallback_even_timings(reference_text, duration)

    # Do NOT force the first word to 0.0 or the last word to the full duration.
    # Captions must be speech-aware: no stale first caption during leading silence, and
    # no final caption held after narration ends.
    if duration is not None and words:
        first_word_start = float(words[0]["start"])
        last_word_end = float(words[-1]["end"])
        logger.info(
            "Whisper timing report: audio %.2fs | first word %.2fs | last word %.2fs",
            float(duration), first_word_start, last_word_end,
        )
        try:
            report_path = os.path.join(os.path.dirname(output_json_path) or ".", "caption_timing_report.json")
            with open(report_path, "w", encoding="utf-8") as rf:
                json.dump({
                    "audio_path": audio_path,
                    "audio_duration_seconds": duration,
                    "first_word_start_seconds": first_word_start,
                    "last_word_end_seconds": last_word_end,
                    "word_count": len(words),
                    "source": words[0].get("source"),
                }, rf, indent=2)
        except Exception as exc:
            logger.warning(f"Could not write caption timing report: {exc}")

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved synced caption timings to {output_json_path}")
    return words


def load_caption_timings(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Caption timing JSON must be a list: {path}")
    return data
