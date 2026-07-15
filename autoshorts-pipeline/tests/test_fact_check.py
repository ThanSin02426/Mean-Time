from pathlib import Path

from src.fact_check import build_fact_check, is_authoritative
from src.script_gen import apply_source_repairs


def test_authoritative_domain_matching_is_not_any_dot_com():
    assert is_authoritative("https://science.nasa.gov/example")
    assert is_authoritative("https://example.edu/research")
    assert not is_authoritative("https://random-shop.com/science")


def test_scene_passes_when_at_least_one_authoritative_source_is_reachable(monkeypatch, tmp_path: Path):
    def fake_verify(url: str, timeout: int = 8):
        return (not url.endswith("dead"), "HTTP 200" if not url.endswith("dead") else "HTTP 404")

    monkeypatch.setattr("src.fact_check.verify_url", fake_verify)
    script = {
        "scenes": [
            {
                "claim": "Earth rotates.",
                "source_note": "NASA reference",
                "sources": ["https://science.nasa.gov/dead", "https://science.nasa.gov/earth/facts/"],
            }
            for _ in range(4)
        ]
    }
    report = build_fact_check(script, tmp_path / "fact_check.json")
    assert report["passed"] is True


def test_source_repairs_only_change_failed_scene_sources():
    script = {
        "scenes": [
            {"narration": "one", "claim": "claim one", "source_note": "old", "sources": ["https://nasa.gov/dead"]},
            {"narration": "two", "claim": "claim two", "source_note": "keep", "sources": ["https://usgs.gov/valid"]},
        ]
    }
    payload = {
        "repairs": [
            {
                "scene": 1,
                "source_note": "live NASA sources",
                "sources": ["https://science.nasa.gov/earth/facts/", "https://science.nasa.gov/earth/facts/"],
            },
            {"scene": 2, "sources": ["https://example.com/must-not-change"]},
        ]
    }
    repaired = apply_source_repairs(script, payload, {1})
    assert repaired["scenes"][0]["sources"] == ["https://science.nasa.gov/earth/facts/"]
    assert repaired["scenes"][0]["source_note"] == "live NASA sources"
    assert repaired["scenes"][1]["sources"] == ["https://usgs.gov/valid"]
    assert script["scenes"][0]["sources"] == ["https://nasa.gov/dead"]
