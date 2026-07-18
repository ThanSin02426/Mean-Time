from pathlib import Path

import pytest

from src.atomic_io import atomic_write_json
from src.topic_engine import (
    DEFAULT_SPACE_QUEUE,
    NICHES,
    SPACE_THEME_COOLDOWN,
    SPACE_TOPIC_LIBRARY,
    TOPIC_LIBRARY,
    QueueManager,
)


def make_manager(tmp_path: Path) -> QueueManager:
    manager = QueueManager(tmp_path)
    manager._write_queue(DEFAULT_SPACE_QUEUE)
    atomic_write_json(
        manager.state_path,
        {"recent_niches": [], "recent_space_themes": [], "used_topics": [], "events": []},
    )
    return manager


def test_channel_has_only_space_niche_and_large_bank():
    assert NICHES == ("space",)
    assert set(TOPIC_LIBRARY) == {"space"}
    assert len(TOPIC_LIBRARY["space"]) >= 96
    assert len(SPACE_TOPIC_LIBRARY) >= 12
    manager = QueueManager()
    assert all(manager.is_space_topic(topic) for topic, _ in TOPIC_LIBRARY["space"])


def test_default_queue_is_balanced_space_only():
    manager = QueueManager()
    assert len(DEFAULT_SPACE_QUEUE) >= 24
    assert len(DEFAULT_SPACE_QUEUE) == len(set(DEFAULT_SPACE_QUEUE))
    assert all(manager.is_space_topic(topic) for topic in DEFAULT_SPACE_QUEUE)
    themes = [manager.theme(topic) for topic in DEFAULT_SPACE_QUEUE]
    assert len(set(themes)) >= 10


def test_fifty_transactional_rotations_stay_in_space(tmp_path):
    manager = make_manager(tmp_path)
    queue_length = len(manager.read_queue())
    replacement_themes = []
    for _ in range(50):
        before = manager.read_queue()
        reservation = manager.reserve()
        result = manager.finalize(reservation, True)
        after = manager.read_queue()
        replacement_themes.append(result["replacement_theme"])
        assert reservation.niche == "space"
        assert result["replacement_niche"] == "space"
        assert len(after) == queue_length
        assert before[1:] == after[:-1]
        assert len(after) == len(set(after))
        assert result["replacement_topic"] not in before[1:]
        assert all(manager.is_space_topic(topic) for topic in after)
    for index, theme in enumerate(replacement_themes):
        recent = replacement_themes[max(0, index - SPACE_THEME_COOLDOWN):index]
        assert theme not in recent


def test_queue_rollback_on_failure(tmp_path):
    manager = make_manager(tmp_path)
    before = manager.read_queue()
    reservation = manager.reserve()
    result = manager.finalize(reservation, False)
    assert result["status"] == "failed"
    assert manager.read_queue() == before


def test_manual_space_topic_never_mutates_queue(tmp_path):
    manager = make_manager(tmp_path)
    before = manager.read_queue()
    reservation = manager.reserve("How black holes bend light")
    result = manager.finalize(reservation, True)
    assert reservation.niche == "space"
    assert result["queue_changed"] is False
    assert manager.read_queue() == before


def test_manual_non_space_topic_is_rejected(tmp_path):
    manager = make_manager(tmp_path)
    with pytest.raises(ValueError, match="locked to space content"):
        manager.reserve("Why unfinished tasks stay stuck in your mind")


def test_non_space_queue_head_is_rejected(tmp_path):
    manager = make_manager(tmp_path)
    manager._write_queue(["Why unfinished tasks stay stuck in your mind", *DEFAULT_SPACE_QUEUE])
    with pytest.raises(RuntimeError, match="Non-space topic found at queue head"):
        manager.reserve()


def test_successful_finalization_is_idempotent(tmp_path):
    manager = make_manager(tmp_path)
    reservation = manager.reserve()
    first = manager.finalize(reservation, True)
    queue_after = manager.read_queue()
    second = manager.finalize(reservation, True)
    assert first["transaction_id"] == second["transaction_id"]
    assert manager.read_queue() == queue_after
