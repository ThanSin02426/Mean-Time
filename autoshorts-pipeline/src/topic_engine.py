from __future__ import annotations

import argparse
import hashlib
import logging
import random
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .atomic_io import atomic_write_json, atomic_write_text, read_json
from .models import utc_now

logger = logging.getLogger(__name__)

# The channel is intentionally locked to one public niche. Diversity is enforced
# with internal space themes rather than by rotating into unrelated subjects.
NICHES = ("space",)
SPACE_THEME_COOLDOWN = 6
TOPIC_COOLDOWN = 40
EXPLORATION_RATE = 0.20

SPACE_TOPIC_LIBRARY: dict[str, list[tuple[str, bool]]] = {
    'black_holes': [
        ('How a black hole bends light into a glowing ring', True),
        ('Why time runs slower near a black hole', True),
        ('What the event horizon of a black hole actually means', True),
        ('How astronomers photographed a black hole shadow', True),
        ('Why black holes can launch enormous jets', True),
        ('How merging black holes create gravitational waves', True),
        ('Could a black hole wander through the Milky Way', False),
        ('What an astronaut would see while approaching a black hole', False),
    ],
    'stars': [
        ('Why neutron stars are so incredibly dense', True),
        ('How pulsars act like cosmic clocks', True),
        ('How stars turn hydrogen into light', True),
        ('Why red giant stars swell to enormous sizes', True),
        ('What causes a massive star to explode as a supernova', True),
        ('Why Sun-like stars end as white dwarfs', True),
        ('How magnetars create extreme magnetic fields', False),
        ('What brown dwarfs reveal about failed stars', False),
    ],
    'solar_system': [
        ('Why Venus is hotter than Mercury', True),
        ('Why Uranus rotates on its side', True),
        ('How Jupiters Great Red Spot survives for centuries', True),
        ('Why Saturns rings are incredibly thin', True),
        ('How Jupiters moon Io became so volcanic', True),
        ('Why Europa may hide a global ocean', True),
        ('How Pluto developed a blue atmospheric haze', True),
        ('Why Mercury has ice inside permanently shadowed craters', False),
    ],
    'mars': [
        ('Why sunsets on Mars appear blue', True),
        ('How dust storms can cover nearly all of Mars', True),
        ('Why Mars lost most of its ancient atmosphere', True),
        ('How Mars rovers navigate without GPS', True),
        ('Why Olympus Mons became the tallest volcano in the solar system', True),
        ('How seasons work differently on Mars', True),
        ('How Perseverance stores samples for a future return mission', False),
        ('What ancient Martian river deltas reveal about past water', False),
    ],
    'moon': [
        ('Why the Moon always shows Earth the same face', True),
        ('How moonquakes happen on a geologically quiet world', True),
        ('Why lunar dust is dangerous to astronauts', True),
        ('How the Moon helps stabilize Earths tilt', True),
        ('What created the dark lunar maria', True),
        ('Why permanently shadowed lunar craters may contain ice', True),
        ('How Apollo astronauts moved in low gravity', False),
        ('Why the Moon is slowly moving away from Earth', False),
    ],
    'exoplanets': [
        ('How astronomers detect planets by watching stars dim', True),
        ('What makes a hot Jupiter so extreme', True),
        ('How scientists study the atmospheres of distant planets', True),
        ('What makes an exoplanet potentially habitable', True),
        ('Why rogue planets travel through space without stars', True),
        ('How tidal locking changes an alien world', True),
        ('What super Earths may be made of', False),
        ('Could some exoplanets rain glass sideways', False),
    ],
    'galaxies': [
        ('How the Milky Ways spiral arms are formed', True),
        ('What really happens when two galaxies collide', True),
        ('Why galaxies appear to sit inside dark matter halos', True),
        ('How the Andromeda galaxy is approaching the Milky Way', True),
        ('Why some galaxies suddenly stop forming stars', True),
        ('How quasars can outshine entire galaxies', True),
        ('What lies at the center of the Milky Way', True),
        ('How astronomers map gas that human eyes cannot see', False),
    ],
    'cosmology': [
        ('How astronomers measure the expansion of the universe', True),
        ('What the cosmic microwave background actually is', True),
        ('Why distant galaxies appear redshifted', True),
        ('What scientists mean by dark energy', True),
        ('How the early universe formed its first atoms', True),
        ('Why the observable universe has a horizon', True),
        ('What cosmic inflation tries to explain', False),
        ('How scientists estimate the age of the universe', False),
    ],
    'asteroids_comets': [
        ('What makes an asteroid different from a comet', True),
        ('How NASAs DART mission changed an asteroids orbit', True),
        ('Why comets grow bright tails near the Sun', True),
        ('How meteorites reveal the history of the solar system', True),
        ('What caused the Chelyabinsk meteor shock wave', True),
        ('Why samples from asteroid Bennu matter', True),
        ('How astronomers track near Earth objects', False),
        ('What metal rich asteroids could reveal about planet formation', False),
    ],
    'spaceflight': [
        ('How reusable rockets land after launch', True),
        ('Why rockets use multiple stages', True),
        ('How spacecraft steal speed using gravity assists', True),
        ('How astronauts dock two spacecraft in orbit', True),
        ('Why interplanetary launch windows matter', True),
        ('How heat shields survive atmospheric reentry', True),
        ('How ion thrusters accelerate spacecraft for years', True),
        ('Why satellites need regular course corrections', False),
    ],
    'telescopes': [
        ('How the James Webb Space Telescope sees infrared light', True),
        ('Why the Hubble Space Telescope orbits above the atmosphere', True),
        ('How radio telescopes see an invisible universe', True),
        ('How adaptive optics removes the twinkle of stars', True),
        ('Why giant telescope mirrors are built from segments', True),
        ('How gravitational lensing works like a cosmic telescope', True),
        ('How telescope interferometry creates a sharper image', False),
        ('How space telescopes detect chemicals in alien atmospheres', False),
    ],
    'human_spaceflight': [
        ('How astronauts sleep in microgravity', True),
        ('Why muscles weaken during long space missions', True),
        ('How the International Space Station recycles water', True),
        ('How spacesuits keep astronauts alive in a vacuum', True),
        ('Why astronauts sometimes see flashes with closed eyes', True),
        ('Why food can taste different in orbit', True),
        ('How astronauts exercise without normal gravity', False),
        ('What months in space do to human bones', False),
    ],
    'space_weather': [
        ('How solar flares can affect technology on Earth', True),
        ('What creates the northern and southern lights', True),
        ('How coronal mass ejections travel through space', True),
        ('Why satellites are vulnerable to space weather', True),
        ('How the solar wind shapes Earths magnetosphere', True),
        ('What happened during the Carrington Event', True),
        ('How scientists forecast dangerous space weather', False),
        ('Why Mars has little protection from the solar wind', False),
    ],
}

TOPIC_LIBRARY: dict[str, list[tuple[str, bool]]] = {
    "space": [row for rows in SPACE_TOPIC_LIBRARY.values() for row in rows]
}
TOPIC_THEMES = {topic: theme for theme, rows in SPACE_TOPIC_LIBRARY.items() for topic, _ in rows}

DEFAULT_SPACE_QUEUE = [
    'How a black hole bends light into a glowing ring',
    'Why neutron stars are so incredibly dense',
    'Why Venus is hotter than Mercury',
    'Why sunsets on Mars appear blue',
    'Why the Moon always shows Earth the same face',
    'How astronomers detect planets by watching stars dim',
    'How the Milky Ways spiral arms are formed',
    'How astronomers measure the expansion of the universe',
    'What makes an asteroid different from a comet',
    'How reusable rockets land after launch',
    'How the James Webb Space Telescope sees infrared light',
    'How astronauts sleep in microgravity',
    'Why time runs slower near a black hole',
    'How pulsars act like cosmic clocks',
    'Why Uranus rotates on its side',
    'How dust storms can cover nearly all of Mars',
    'How moonquakes happen on a geologically quiet world',
    'What makes a hot Jupiter so extreme',
    'What really happens when two galaxies collide',
    'What the cosmic microwave background actually is',
    'How NASAs DART mission changed an asteroids orbit',
    'Why rockets use multiple stages',
    'Why the Hubble Space Telescope orbits above the atmosphere',
    'Why muscles weaken during long space missions'
]

SPACE_KEYWORDS = (
    "space", "astronomy", "cosmos", "cosmic", "universe", "galaxy", "milky way",
    "black hole", "event horizon", "star", "supernova", "neutron", "pulsar", "magnetar",
    "planet", "exoplanet", "moon", "lunar", "mars", "venus", "mercury", "jupiter",
    "saturn", "uranus", "neptune", "pluto", "asteroid", "comet", "meteor", "solar",
    "sun", "rocket", "spacecraft", "satellite", "astronaut", "orbit", "telescope",
    "hubble", "james webb", "jwst", "nasa", "esa", "spaceflight", "microgravity",
    "aurora", "magnetosphere", "space weather", "gravity assist", "reentry",
)


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
    def is_space_topic(topic: str) -> bool:
        normalized = topic.strip().casefold()
        if normalized in {candidate.casefold() for candidate in TOPIC_THEMES}:
            return True
        text = re.sub(r"[^a-z0-9]+", " ", normalized)
        return any(keyword in text for keyword in SPACE_KEYWORDS)

    @staticmethod
    def categorize(topic: str) -> str:
        return "space" if QueueManager.is_space_topic(topic) else "non_space"

    @staticmethod
    def theme(topic: str) -> str:
        exact = TOPIC_THEMES.get(topic)
        if exact:
            return exact
        text = topic.casefold()
        rules = (
            ("black_holes", ("black hole", "event horizon")),
            ("stars", ("star", "supernova", "pulsar", "neutron", "magnetar", "brown dwarf")),
            ("mars", ("mars", "martian")),
            ("moon", ("moon", "lunar", "apollo")),
            ("exoplanets", ("exoplanet", "alien world", "hot jupiter", "super earth", "rogue planet")),
            ("galaxies", ("galaxy", "galaxies", "milky way", "andromeda", "quasar")),
            ("cosmology", ("universe", "cosmic microwave", "dark energy", "redshift", "inflation")),
            ("asteroids_comets", ("asteroid", "comet", "meteor", "bennu", "dart")),
            ("spaceflight", ("rocket", "spacecraft", "launch", "reentry", "thruster", "satellite")),
            ("telescopes", ("telescope", "hubble", "james webb", "jwst", "interferometry")),
            ("human_spaceflight", ("astronaut", "spacesuit", "microgravity", "space station", "iss")),
            ("space_weather", ("solar flare", "aurora", "solar wind", "magnetosphere", "space weather")),
            ("solar_system", ("venus", "mercury", "jupiter", "saturn", "uranus", "neptune", "pluto", "europa", "io")),
        )
        for theme, keywords in rules:
            if any(keyword in text for keyword in keywords):
                return theme
        return "general_space"

    def read_queue(self) -> list[str]:
        if not self.queue_path.exists():
            return []
        return [line.strip() for line in self.queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _write_queue(self, topics: Iterable[str]) -> None:
        atomic_write_text(self.queue_path, "\n".join(topics) + "\n")

    def reserve(self, manual_topic: str | None = None) -> QueueReservation:
        if manual_topic and manual_topic.strip():
            topic = manual_topic.strip()
            if not self.is_space_topic(topic):
                raise ValueError(
                    "This channel is locked to space content. Use an astronomy, cosmology, "
                    "planetary-science, spaceflight, or space-technology topic."
                )
            reservation = QueueReservation(uuid4().hex, "manual", topic, "space", utc_now())
            logger.info("Manual space-topic mode; queue will not be changed: %s", topic)
            return reservation
        queue = self.read_queue()
        if not queue:
            raise RuntimeError(f"Topic queue is empty: {self.queue_path}")
        topic = queue[0]
        if not self.is_space_topic(topic):
            raise RuntimeError(
                f"Non-space topic found at queue head: {topic!r}. "
                "Run the space-only migration or replace topics.txt before publishing."
            )
        reservation = QueueReservation(uuid4().hex, "queue", topic, "space", utc_now())
        atomic_write_json(self.tx_path, asdict(reservation))
        logger.info("QUEUE BEFORE: %s", queue)
        logger.info("RESERVED TOPIC: %s", topic)
        logger.info("RESERVED NICHE: space")
        logger.info("RESERVED SPACE THEME: %s", self.theme(topic))
        return reservation

    def _all_candidates(self, proven: bool | None = None) -> list[tuple[str, str, bool]]:
        rows: list[tuple[str, str, bool]] = []
        for topic, is_proven in TOPIC_LIBRARY["space"]:
            if proven is None or is_proven == proven:
                rows.append((topic, "space", is_proven))
        return rows

    def _choose_replacement(self, reservation: QueueReservation, queue_after_pop: list[str], state: dict) -> tuple[str, str]:
        tail_themes = [self.theme(topic) for topic in queue_after_pop[-SPACE_THEME_COOLDOWN:]]
        recent_themes = list(state.get("recent_space_themes", []))[-SPACE_THEME_COOLDOWN:]
        recent_topics = list(state.get("used_topics", []))[-TOPIC_COOLDOWN:]
        seed = int(hashlib.sha256(reservation.transaction_id.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        exploratory = rng.random() < EXPLORATION_RATE
        candidates = self._all_candidates(proven=not exploratory)
        rng.shuffle(candidates)

        def allowed(row: tuple[str, str, bool], *, avoid_tail: bool, avoid_recent_theme: bool) -> bool:
            topic, _, _ = row
            theme = self.theme(topic)
            return (
                topic != reservation.topic
                and topic not in queue_after_pop
                and topic not in recent_topics
                and (not avoid_tail or theme not in tail_themes)
                and (not avoid_recent_theme or theme not in recent_themes)
            )

        for avoid_tail, avoid_recent in ((True, True), (False, True), (True, False), (False, False)):
            for row in candidates:
                if allowed(row, avoid_tail=avoid_tail, avoid_recent_theme=avoid_recent):
                    return row[0], "space"
        for row in self._all_candidates(None):
            if row[0] not in queue_after_pop and row[0] != reservation.topic:
                return row[0], "space"
        raise RuntimeError("No unique space replacement topic is available")

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
        if any(not self.is_space_topic(topic) for topic in queue):
            raise RuntimeError("Queue contains a non-space topic; refusing to finalize a space-only channel transaction")

        state = read_json(self.state_path, {"recent_niches": [], "used_topics": [], "events": []})
        queue_after = queue[1:]
        replacement, niche = self._choose_replacement(reservation, queue_after, state)
        queue_after.append(replacement)
        selected_theme = self.theme(reservation.topic)
        replacement_theme = self.theme(replacement)

        state.setdefault("recent_niches", []).append("space")
        state["recent_niches"] = state["recent_niches"][-SPACE_THEME_COOLDOWN:]
        state.setdefault("recent_replacement_niches", []).append("space")
        state["recent_replacement_niches"] = state["recent_replacement_niches"][-SPACE_THEME_COOLDOWN:]
        state.setdefault("recent_space_themes", []).append(replacement_theme)
        state["recent_space_themes"] = state["recent_space_themes"][-SPACE_THEME_COOLDOWN:]
        state.setdefault("used_topics", []).append(reservation.topic)
        state["used_topics"] = state["used_topics"][-TOPIC_COOLDOWN:]
        event = {
            "transaction_id": reservation.transaction_id,
            "selected_topic": reservation.topic,
            "selected_niche": "space",
            "selected_theme": selected_theme,
            "replacement_topic": replacement,
            "replacement_niche": "space",
            "replacement_theme": replacement_theme,
            "finalized_at": utc_now(),
        }
        state.setdefault("events", []).append(event)
        state["events"] = state["events"][-200:]

        bank = read_json(self.bank_path, [])
        known = {row.get("topic") for row in bank if isinstance(row, dict)}
        for theme, rows in SPACE_TOPIC_LIBRARY.items():
            for topic, proven in rows:
                if topic not in known:
                    bank.append({"topic": topic, "niche": "space", "theme": theme, "proven": proven})

        finalized = asdict(reservation)
        finalized.update({
            "status": "finalized", "finalized_at": utc_now(), "queue_changed": True,
            "replacement_topic": replacement, "replacement_niche": niche,
            "selected_theme": selected_theme, "replacement_theme": replacement_theme,
            "queue_before": queue, "queue_after": queue_after,
        })
        self._write_queue(queue_after)
        atomic_write_json(self.state_path, state)
        atomic_write_json(self.bank_path, bank)
        atomic_write_json(self.tx_path, finalized)
        logger.info("RECENT SPACE THEMES: %s", state["recent_space_themes"])
        logger.info("REPLACEMENT TOPIC: %s", replacement)
        logger.info("REPLACEMENT NICHE: space")
        logger.info("REPLACEMENT SPACE THEME: %s", replacement_theme)
        logger.info("QUEUE AFTER: %s", queue_after)
        logger.info("QUEUE COMMIT STATUS: finalized locally")
        return finalized


def default_space_queue() -> list[str]:
    return list(DEFAULT_SPACE_QUEUE)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manager = QueueManager(tmp)
        initial = default_space_queue()
        manager._write_queue(initial)
        atomic_write_json(manager.state_path, {"recent_niches": [], "recent_space_themes": [], "used_topics": [], "events": []})
        previous_len = len(initial)
        replacement_themes: list[str] = []
        for _ in range(50):
            before = manager.read_queue()
            reservation = manager.reserve()
            assert reservation.niche == "space"
            result = manager.finalize(reservation, True)
            after = manager.read_queue()
            replacement_themes.append(result["replacement_theme"])
            assert result["status"] == "finalized"
            assert len(after) == previous_len
            assert before[1:] == after[:-1]
            assert len(after) == len(set(after))
            assert all(manager.is_space_topic(topic) for topic in after)
            again = manager.finalize(reservation, True)
            assert again["status"] == "finalized"
            assert manager.read_queue() == after
        for index, theme in enumerate(replacement_themes):
            recent = replacement_themes[max(0, index - SPACE_THEME_COOLDOWN):index]
            assert theme not in recent
        before = manager.read_queue()
        manual = manager.reserve("How black holes bend light")
        manager.finalize(manual, True)
        assert manager.read_queue() == before
        try:
            manager.reserve("Why unfinished tasks stay in your mind")
        except ValueError:
            pass
        else:
            raise AssertionError("Non-space manual topic was not rejected")
        failed = manager.reserve()
        manager.finalize(failed, False)
        assert manager.read_queue() == before
    print("topic_engine self-test passed: space-only queue, 50 rotations, theme diversity, rollback, manual guard, idempotency")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()


if __name__ == "__main__":
    main()
