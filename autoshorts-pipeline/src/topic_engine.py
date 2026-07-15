from __future__ import annotations

import argparse
import hashlib
import logging
import random
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .atomic_io import atomic_write_json, atomic_write_text, read_json
from .models import utc_now

logger = logging.getLogger(__name__)

NICHES = (
    "space", "ocean", "animals", "ancient_history", "human_body", "psychology",
    "strange_science", "mysteries", "extreme_places", "technology_future",
    "engineering", "rare_natural_phenomena",
)
NICHE_COOLDOWN = 8
TOPIC_COOLDOWN = 40
EXPLORATION_RATE = 0.20

TOPIC_LIBRARY: dict[str, list[tuple[str, bool]]] = {
    "space": [
        ("What would happen if Earth stopped spinning for one second", True),
        ("Why neutron stars are the densest visible objects", True),
        ("The coldest known place in the universe", True),
        ("How astronauts sleep without falling down", True),
        ("The strange weather found on distant planets", False),
        ("Why space can smell like hot metal", False),
        ("How black holes bend the path of light", True),
        ("Why sunsets on Mars appear blue", True),
    ],
    "ocean": [
        ("How deep sea animals survive crushing pressure", True),
        ("Why the ocean has underwater lakes", True),
        ("The loudest animal sound in the ocean", True),
        ("How hydrothermal vents support life without sunlight", True),
        ("The mysterious daily migration beneath the ocean surface", False),
        ("Why some ocean waves glow blue at night", False),
        ("How whales communicate across huge distances", True),
        ("Why coral reefs support so many species", True),
    ],
    "animals": [
        ("Animals that can survive being frozen", True),
        ("How octopuses solve complex problems", True),
        ("Why crows can remember human faces", True),
        ("The fastest biological movement in nature", True),
        ("How tiny animals navigate using Earths magnetic field", False),
        ("The animal that can rebuild most of its body", False),
        ("How geckos walk across ceilings", True),
        ("Why owls can fly almost silently", True),
    ],
    "ancient_history": [
        ("How ancient Romans made concrete that heals itself", True),
        ("The engineering secrets of the Great Pyramid", True),
        ("How ancient cities moved water without electricity", True),
        ("The oldest known written customer complaint", True),
        ("Lost pigments used by ancient artists", False),
        ("How ancient navigators crossed oceans without maps", False),
        ("How the Inca built roads across mountains", True),
        ("Why ancient glass can survive for centuries", True),
    ],
    "human_body": [
        ("Why your stomach does not digest itself", True),
        ("How the human body repairs broken bones", True),
        ("Why fingerprints improve grip", True),
        ("What causes the feeling of pins and needles", True),
        ("How the brain predicts what you will see next", False),
        ("Why humans produce different kinds of tears", False),
        ("How your inner ear keeps you balanced", True),
        ("Why muscles shake during intense effort", True),
    ],
    "psychology": [
        ("Why unfinished tasks stay stuck in your mind", True),
        ("How expectation changes what food tastes like", True),
        ("Why time feels faster as routines repeat", True),
        ("How crowds change individual decisions", True),
        ("The psychology behind false familiarity", False),
        ("Why silence can feel longer than it is", False),
        ("Why choices feel harder when options multiply", True),
        ("How sleep strengthens new memories", True),
    ],
    "strange_science": [
        ("Materials that get thicker when hit", True),
        ("How water can boil and freeze at the same time", True),
        ("Why some metals remember their original shape", True),
        ("The experiment that makes sound visible", True),
        ("How light can push microscopic objects", False),
        ("Why hot water can sometimes freeze first", False),
        ("How magnetic levitation can suspend objects", True),
        ("Why soap makes water spread differently", True),
    ],
    "mysteries": [
        ("The science behind unexplained humming sounds", True),
        ("Why some ancient maps seem unusually accurate", True),
        ("The mystery of disappearing desert lakes", True),
        ("How investigators identify unknown shipwrecks", True),
        ("The coded messages hidden in historic monuments", False),
        ("Why abandoned places can preserve sound clues", False),
        ("How scientists trace the origin of meteorites", True),
        ("Why some radio signals remain unidentified", True),
    ],
    "extreme_places": [
        ("How people live in the coldest inhabited town", True),
        ("The hottest naturally occurring ground temperatures", True),
        ("Why high altitude deserts are used to test Mars equipment", True),
        ("Life beside the worlds most active volcanoes", True),
        ("The isolated caves with their own ecosystems", False),
        ("Why some deserts suddenly fill with flowers", False),
        ("How life survives beneath Antarctic ice", True),
        ("Why salt flats become giant natural mirrors", True),
    ],
    "technology_future": [
        ("How quantum sensors can detect tiny changes", True),
        ("Why solid state batteries could change electric vehicles", True),
        ("How robots learn delicate hand movements", True),
        ("The technology behind reusable rockets", True),
        ("How digital twins predict machine failures", False),
        ("The future of computing with light instead of electricity", False),
        ("How heat pumps move more heat than their electricity input", True),
        ("Why satellite internet needs moving constellations", True),
    ],
    "engineering": [
        ("Why skyscrapers are designed to sway", True),
        ("How suspension bridges survive strong winds", True),
        ("The engineering that keeps tunnels dry", True),
        ("How aircraft wings bend without breaking", True),
        ("Why some buildings use giant moving weights", False),
        ("How engineers move entire historic structures", False),
        ("How earthquake isolators protect buildings", True),
        ("Why submarine hulls use rounded shapes", True),
    ],
    "rare_natural_phenomena": [
        ("How fire rainbows form without fire", True),
        ("Why ball lightning remains difficult to explain", True),
        ("How frost flowers grow on sea ice", True),
        ("Why volcanic lightning appears inside ash clouds", True),
        ("The conditions that create moonbows", False),
        ("How singing sand dunes produce deep notes", False),
        ("Why ice circles rotate in slow rivers", True),
        ("How lenticular clouds form over mountains", True),
    ],
}

KEYWORDS = {
    "space": ("space", "planet", "star", "universe", "astronaut", "rocket", "neutron"),
    "ocean": ("ocean", "sea", "marine", "underwater", "hydrothermal"),
    "animals": ("animal", "octopus", "crow", "bird", "wildlife"),
    "ancient_history": ("ancient", "roman", "pyramid", "historic"),
    "human_body": ("body", "stomach", "bone", "fingerprint", "tears"),
    "psychology": ("psychology", "mind", "decision", "familiarity", "routine"),
    "strange_science": ("science", "water", "metal", "material", "light"),
    "mysteries": ("mystery", "unexplained", "coded", "unknown", "disappearing"),
    "extreme_places": ("coldest", "hottest", "desert", "volcano", "cave"),
    "technology_future": ("quantum", "battery", "robot", "technology", "computing"),
    "engineering": ("engineering", "bridge", "skyscraper", "tunnel", "aircraft"),
    "rare_natural_phenomena": ("rainbow", "lightning", "frost", "moonbow", "dune"),
}


@dataclass(slots=True)
class QueueReservation:
    transaction_id: str
    source: str
    topic: str
    niche: str
    reserved_at: str
    status: str = "reserved"
    replacement_topic: str = ""
    replacement_niche: str = ""


class QueueManager:
    def __init__(self, base_dir: str | Path = ".", queue_name: str = "topics.txt") -> None:
        self.base = Path(base_dir)
        self.queue_path = self.base / queue_name
        self.state_path = self.base / "topic_state.json"
        self.bank_path = self.base / "topic_bank.json"
        self.tx_path = self.base / "queue_transaction.json"
        self.base.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def categorize(topic: str) -> str:
        normalized_topic = topic.strip().casefold()
        for niche, rows in TOPIC_LIBRARY.items():
            if any(candidate.casefold() == normalized_topic for candidate, _ in rows):
                return niche
        text = topic.lower()
        scores = {n: sum(1 for key in KEYWORDS[n] if key in text) for n in NICHES}
        best = max(scores, key=scores.get)
        return best if scores[best] else "strange_science"

    def read_queue(self) -> list[str]:
        if not self.queue_path.exists():
            return []
        return [line.strip() for line in self.queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _write_queue(self, topics: Iterable[str]) -> None:
        atomic_write_text(self.queue_path, "\n".join(topics) + "\n")

    def reserve(self, manual_topic: str | None = None) -> QueueReservation:
        if manual_topic and manual_topic.strip():
            topic = manual_topic.strip()
            reservation = QueueReservation(uuid4().hex, "manual", topic, self.categorize(topic), utc_now())
            logger.info("Manual topic mode; queue will not be changed: %s", topic)
            return reservation
        queue = self.read_queue()
        if not queue:
            raise RuntimeError(f"Topic queue is empty: {self.queue_path}")
        topic = queue[0]
        reservation = QueueReservation(uuid4().hex, "queue", topic, self.categorize(topic), utc_now())
        atomic_write_json(self.tx_path, asdict(reservation))
        logger.info("QUEUE BEFORE: %s", queue)
        logger.info("RESERVED TOPIC: %s", topic)
        logger.info("RESERVED NICHE: %s", reservation.niche)
        return reservation

    def _all_candidates(self, proven: bool | None = None) -> list[tuple[str, str, bool]]:
        rows: list[tuple[str, str, bool]] = []
        for niche, topics in TOPIC_LIBRARY.items():
            for topic, is_proven in topics:
                if proven is None or is_proven == proven:
                    rows.append((topic, niche, is_proven))
        return rows

    def _choose_replacement(self, reservation: QueueReservation, queue_after_pop: list[str], state: dict) -> tuple[str, str]:
        # The replacement is appended to the queue tail, so compare its niche with
        # the eight topics that will immediately precede it when it is selected.
        tail_niches = [self.categorize(topic) for topic in queue_after_pop[-NICHE_COOLDOWN:]]
        generated_niches = list(state.get("recent_replacement_niches", []))[-NICHE_COOLDOWN:]
        recent_topics = list(state.get("used_topics", []))[-TOPIC_COOLDOWN:]
        seed = int(hashlib.sha256(reservation.transaction_id.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        exploratory = rng.random() < EXPLORATION_RATE
        candidates = self._all_candidates(proven=not exploratory)
        rng.shuffle(candidates)

        def allowed(row: tuple[str, str, bool], *, enforce_tail: bool, enforce_generated: bool = True) -> bool:
            topic, niche, _ = row
            return (
                topic != reservation.topic
                and topic not in queue_after_pop
                and topic not in recent_topics
                and (not enforce_tail or niche not in tail_niches)
                and (not enforce_generated or niche not in generated_niches)
            )

        # Keep the generated replacement sequence diverse even when the existing
        # queue tail makes the stricter condition temporarily impossible.
        for enforce_tail in (True, False):
            for row in candidates:
                if allowed(row, enforce_tail=enforce_tail):
                    return row[0], row[1]
        for row in candidates:
            if allowed(row, enforce_tail=False, enforce_generated=False):
                return row[0], row[1]
        for row in self._all_candidates(None):
            if row[0] not in queue_after_pop and row[0] != reservation.topic:
                return row[0], row[1]
        raise RuntimeError("No unique replacement topic is available")

    def finalize(self, reservation: QueueReservation, success: bool) -> dict:
        if reservation.source == "manual":
            return {"status": "not_applicable", "source": "manual", "queue_changed": False}
        existing = read_json(self.tx_path, {})
        if existing.get("transaction_id") == reservation.transaction_id and existing.get("status") == "finalized":
            logger.info("Queue transaction already finalized; leaving queue unchanged.")
            return existing
        if not success:
            failed = asdict(reservation)
            failed.update({"status": "failed", "failed_at": utc_now(), "queue_changed": False})
            atomic_write_json(self.tx_path, failed)
            logger.info("Pipeline failed; queue remains unchanged.")
            return failed

        queue = self.read_queue()
        if not queue or queue[0] != reservation.topic:
            raise RuntimeError("Queue head changed after reservation; refusing non-atomic finalization")
        state = read_json(self.state_path, {"recent_niches": [], "used_topics": [], "events": []})
        queue_after = queue[1:]
        replacement, niche = self._choose_replacement(reservation, queue_after, state)
        queue_after.append(replacement)

        state.setdefault("recent_niches", []).append(reservation.niche)
        state["recent_niches"] = state["recent_niches"][-NICHE_COOLDOWN:]
        state.setdefault("used_topics", []).append(reservation.topic)
        state["used_topics"] = state["used_topics"][-TOPIC_COOLDOWN:]
        state.setdefault("recent_replacement_niches", []).append(niche)
        state["recent_replacement_niches"] = state["recent_replacement_niches"][-NICHE_COOLDOWN:]
        event = {
            "transaction_id": reservation.transaction_id,
            "selected_topic": reservation.topic,
            "selected_niche": reservation.niche,
            "replacement_topic": replacement,
            "replacement_niche": niche,
            "finalized_at": utc_now(),
        }
        state.setdefault("events", []).append(event)
        state["events"] = state["events"][-200:]
        bank = read_json(self.bank_path, [])
        known = {row.get("topic") for row in bank if isinstance(row, dict)}
        for topic, topic_niche, proven in self._all_candidates(None):
            if topic not in known:
                bank.append({"topic": topic, "niche": topic_niche, "proven": proven})

        finalized = asdict(reservation)
        finalized.update({
            "status": "finalized", "finalized_at": utc_now(), "queue_changed": True,
            "replacement_topic": replacement, "replacement_niche": niche,
            "queue_before": queue, "queue_after": queue_after,
        })
        self._write_queue(queue_after)
        atomic_write_json(self.state_path, state)
        atomic_write_json(self.bank_path, bank)
        atomic_write_json(self.tx_path, finalized)
        logger.info("RECENT NICHES: %s", state["recent_niches"])
        logger.info("REPLACEMENT TOPIC: %s", replacement)
        logger.info("REPLACEMENT NICHE: %s", niche)
        logger.info("QUEUE AFTER: %s", queue_after)
        logger.info("QUEUE COMMIT STATUS: finalized locally")
        return finalized


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = QueueManager(tmp)
        initial = [TOPIC_LIBRARY[niche][round_index][0] for round_index in range(2) for niche in NICHES]
        manager._write_queue(initial)
        atomic_write_json(manager.state_path, {"recent_niches": [], "used_topics": [], "events": []})
        previous_len = len(initial)
        for _ in range(50):
            before = manager.read_queue()
            reservation = manager.reserve()
            result = manager.finalize(reservation, True)
            after = manager.read_queue()
            assert result["status"] == "finalized"
            assert len(after) == previous_len
            assert before[1:] == after[:-1]
            assert len(after) == len(set(after))
            again = manager.finalize(reservation, True)
            assert again["status"] == "finalized"
            assert manager.read_queue() == after
        before = manager.read_queue()
        manual = manager.reserve("A manual test topic")
        manager.finalize(manual, True)
        assert manager.read_queue() == before
        failed = manager.reserve()
        manager.finalize(failed, False)
        assert manager.read_queue() == before
    print("topic_engine self-test passed: 50 rotations, rollback, manual mode, idempotency")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()


if __name__ == "__main__":
    main()
