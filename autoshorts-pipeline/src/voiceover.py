import asyncio
import json
import logging
import os
from typing import Dict, List

import edge_tts

logger = logging.getLogger(__name__)


def _fallback_boundaries(text: str, audio_duration: float | None = None) -> List[Dict]:
    words = [w.strip() for w in str(text or "").split() if w.strip()]
    if not words:
        return []
    duration = float(audio_duration or max(2.0, len(words) * 0.34))
    step = duration / max(1, len(words))
    return [
        {
            "word": word,
            "start": round(i * step, 3),
            "end": round(min(duration, i * step + max(0.12, step * 0.85)), 3),
            "source": "edge_tts_fallback_even",
        }
        for i, word in enumerate(words)
    ]


def _audio_duration(path: str) -> float | None:
    try:
        from moviepy.editor import AudioFileClip
        clip = AudioFileClip(path)
        duration = float(clip.duration)
        clip.close()
        return duration
    except Exception:
        return None


def generate_audio(text, output_mp3_path, output_json_path, voice=None):
    """
    Generate narration audio with edge-tts.

    The returned WordBoundary timings are saved only as backup/debug timings.
    Final burned captions should be generated from the actual saved audio using
    faster-whisper in subtitles.py.
    """
    voice = voice or os.environ.get("EDGE_TTS_VOICE", "en-US-ChristopherNeural")
    logger.info(f"Generating TTS audio using voice {voice}...")
    os.makedirs(os.path.dirname(output_mp3_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(output_json_path) or ".", exist_ok=True)

    word_boundaries: List[Dict] = []

    async def _generate():
        communicate = edge_tts.Communicate(str(text), voice)
        with open(output_mp3_path, "wb") as file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start_sec = float(chunk.get("offset", 0)) / 1e7
                    duration_sec = float(chunk.get("duration", 0)) / 1e7
                    end_sec = start_sec + max(duration_sec, 0.12)
                    word_boundaries.append({
                        "word": str(chunk.get("text", "")).strip(),
                        "start": round(start_sec, 3),
                        "end": round(end_sec, 3),
                        "source": "edge_tts_wordboundary",
                    })

    asyncio.run(_generate())

    if not os.path.exists(output_mp3_path) or os.path.getsize(output_mp3_path) < 1024:
        raise RuntimeError(f"edge-tts did not create a valid audio file at {output_mp3_path}")

    if not word_boundaries:
        logger.warning("No edge-tts WordBoundary events returned; saving even backup timings.")
        word_boundaries = _fallback_boundaries(text, _audio_duration(output_mp3_path))

    with open(output_json_path, "w", encoding="utf-8") as file:
        json.dump(word_boundaries, file, indent=2, ensure_ascii=False)

    logger.info(f"Successfully generated audio at {output_mp3_path} and backup timing JSON at {output_json_path}")
    return word_boundaries


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generate_audio("This is a test to verify audio and timing generation.", "test_audio.mp3", "test_captions.json")
