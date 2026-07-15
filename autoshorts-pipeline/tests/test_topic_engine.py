from pathlib import Path

from src.atomic_io import atomic_write_json
from src.topic_engine import NICHES, TOPIC_LIBRARY, QueueManager


def balanced_topics():
    return [TOPIC_LIBRARY[niche][round_index][0] for round_index in range(2) for niche in NICHES]


def make_manager(tmp_path: Path) -> QueueManager:
    manager = QueueManager(tmp_path)
    manager._write_queue(balanced_topics())
    atomic_write_json(manager.state_path, {"recent_niches": [], "used_topics": [], "events": []})
    return manager


def test_fifty_transactional_rotations(tmp_path):
    manager = make_manager(tmp_path)
    queue_length = len(manager.read_queue())
    replacement_niches = []
    for _ in range(50):
        before = manager.read_queue()
        reservation = manager.reserve()
        result = manager.finalize(reservation, True)
        after = manager.read_queue()
        replacement_niches.append(result["replacement_niche"])
        assert len(after) == queue_length
        assert before[1:] == after[:-1]
        assert len(after) == len(set(after))
        assert result["replacement_topic"] not in before[1:]
    for index, niche in enumerate(replacement_niches):
        recent = replacement_niches[max(0, index - 8):index]
        assert niche not in recent or len(set(NICHES) - set(recent)) == 0


def test_queue_rollback_on_failure(tmp_path):
    manager = make_manager(tmp_path)
    before = manager.read_queue()
    reservation = manager.reserve()
    result = manager.finalize(reservation, False)
    assert result["status"] == "failed"
    assert manager.read_queue() == before


def test_manual_topic_never_mutates_queue(tmp_path):
    manager = make_manager(tmp_path)
    before = manager.read_queue()
    reservation = manager.reserve("A manual topic")
    result = manager.finalize(reservation, True)
    assert result["queue_changed"] is False
    assert manager.read_queue() == before


def test_successful_finalization_is_idempotent(tmp_path):
    manager = make_manager(tmp_path)
    reservation = manager.reserve()
    first = manager.finalize(reservation, True)
    queue_after = manager.read_queue()
    second = manager.finalize(reservation, True)
    assert first["transaction_id"] == second["transaction_id"]
    assert manager.read_queue() == queue_after
