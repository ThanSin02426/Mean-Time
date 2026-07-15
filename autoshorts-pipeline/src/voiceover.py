from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .audio_utils import trim_to_canonical_wav

logger = logging.getLogger(__name__)


async def _synthesize(text: str, path: Path, voice: str, rate: str) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts is required for narration generation") from exc
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(str(path))


def generate_canonical_narration(
    text: str,
    raw_path: str | Path,
    canonical_path: str | Path,
    voice: str = "en-US-AriaNeural",
    rate: str = "+0%",
) -> float:
    raw = Path(raw_path)
    canonical = Path(canonical_path)
    raw.parent.mkdir(parents=True, exist_ok=True)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        raise ValueError("Narration text is empty")
    logger.info("Synthesizing narration with %s at rate %s", voice, rate)
    asyncio.run(_synthesize(text, raw, voice, rate))
    if not raw.exists() or raw.stat().st_size < 1024:
        raise RuntimeError("TTS did not produce a valid audio file")
    duration = trim_to_canonical_wav(raw, canonical)
    logger.info("Canonical narration saved: %s (%.2fs)", canonical, duration)
    return duration
