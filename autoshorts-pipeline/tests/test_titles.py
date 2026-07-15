import json

from src.script_gen import title_is_unique, title_similarity


def test_duplicate_title_detection(tmp_path):
    history = tmp_path / "history.json"
    history.write_text(json.dumps([{"title": "Why Skyscrapers Are Designed to Sway"}]), encoding="utf-8")
    assert not title_is_unique("Why Skyscrapers Are Designed To Sway!", history)
    assert title_is_unique("How Frost Flowers Grow on Sea Ice", history)
    assert title_similarity("Same Title", "Same title!") > 0.95
