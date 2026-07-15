from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    project_dir: Path = Path(__file__).resolve().parents[1]
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", "output"))
    work_dir: Path = Path(os.getenv("WORK_DIR", ".work"))
    queue_file: Path = Path(os.getenv("TOPICS_FILE", "topics.txt"))
    topic_state_file: Path = Path("topic_state.json")
    topic_bank_file: Path = Path("topic_bank.json")
    queue_transaction_file: Path = Path("queue_transaction.json")
    upload_history_file: Path = Path("upload_history.json")
    whisper_primary: str = os.getenv("WHISPER_MODEL", "base.en")
    whisper_fallback: str = os.getenv("WHISPER_FALLBACK_MODEL", "small.en")
    whisper_device: str = os.getenv("WHISPER_DEVICE", "cpu")
    whisper_compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    voice: str = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")
    voice_rate: str = os.getenv("EDGE_TTS_RATE", "+0%")
    min_duration: float = float(os.getenv("MIN_FINAL_DURATION_SECONDS", "30"))
    max_duration: float = float(os.getenv("MAX_FINAL_DURATION_SECONDS", "58"))
    min_words: int = int(os.getenv("MIN_NARRATION_WORDS", "85"))
    max_words: int = int(os.getenv("MAX_NARRATION_WORDS", "115"))
    min_visual_score: float = float(os.getenv("MIN_VISUAL_RELEVANCE_SCORE", "2.0"))
    analytics_enabled: bool = env_bool("ENABLE_ANALYTICS", False)

    def prepare(self, clean: bool = True) -> None:
        if clean:
            shutil.rmtree(self.output_dir, ignore_errors=True)
            shutil.rmtree(self.work_dir, ignore_errors=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
