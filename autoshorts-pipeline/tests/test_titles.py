import json

from src.script_gen import title_is_unique, title_similarity


def test_duplicate_title_detection(tmp_path):
    history = tmp_path / "history.json"
    history.write_text(json.dumps([{"title": "Why Skyscrapers Are Designed to Sway"}]), encoding="utf-8")
    assert not title_is_unique("Why Skyscrapers Are Designed To Sway!", history)
    assert title_is_unique("How Frost Flowers Grow on Sea Ice", history)
    assert title_similarity("Same Title", "Same title!") > 0.95


def _valid_space_script(niche: str = "psychology"):
    scene = {
        "narration": "Astronomers use precise measurements to explain this space phenomenon while keeping every claim tied to evidence from reliable scientific observations and repeatable experiments.",
        "visual_subject": "deep space observation",
        "visual_query": "deep space telescope",
        "visual_negative_terms": ["text", "logo"],
        "preferred_media_type": "video",
        "scene_keywords": ["space", "telescope", "astronomy"],
        "claim": "Astronomers use observations to study space phenomena.",
        "source_note": "An authoritative astronomy source is required.",
        "sources": [],
    }
    return {
        "title": "How Telescopes Reveal the Invisible Universe",
        "description": "A space science explanation.",
        "tags": ["space", "astronomy"],
        "niche": niche,
        "scenes": [dict(scene) for _ in range(4)],
    }


def test_script_niche_is_forced_to_space(tmp_path):
    from src.script_gen import validate_script

    history = tmp_path / "history.json"
    history.write_text("[]", encoding="utf-8")
    result = validate_script(_valid_space_script(), "How radio telescopes see an invisible universe", history)
    assert result["niche"] == "space"


def test_non_space_script_topic_is_rejected(tmp_path):
    import pytest
    from src.script_gen import validate_script

    history = tmp_path / "history.json"
    history.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="only space-oriented topics"):
        validate_script(_valid_space_script(), "Why unfinished tasks stay stuck in your mind", history)
