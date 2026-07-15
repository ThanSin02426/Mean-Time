from pathlib import Path
from types import SimpleNamespace

from src.fact_check import build_fact_check, is_authoritative
from src.script_gen import (
    apply_grounded_scene_repair,
    extract_grounding_sources,
    resolve_grounding_sources,
)


def _grounding_response(*rows: tuple[str, str]):
    chunks = [SimpleNamespace(web=SimpleNamespace(uri=url, title=title)) for url, title in rows]
    metadata = SimpleNamespace(grounding_chunks=chunks)
    return SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=metadata)])


def test_authoritative_domain_matching_is_not_any_dot_com():
    assert is_authoritative("https://science.nasa.gov/example")
    assert is_authoritative("https://example.edu/research")
    assert is_authoritative("https://openstax.org/books/physics/pages/1-introduction")
    assert not is_authoritative("https://random-shop.com/science")


def test_scene_passes_when_grounding_redirect_resolves_to_authoritative_source(monkeypatch, tmp_path: Path):
    def fake_verify(url: str, timeout: int = 8):
        return True, "HTTP 200", "https://science.nasa.gov/earth/facts/"

    monkeypatch.setattr("src.fact_check.verify_url_details", fake_verify)
    script = {
        "scenes": [
            {
                "claim": "Earth rotates.",
                "source_note": "NASA reference",
                "sources": ["https://vertexaisearch.cloud.google.com/grounding-api-redirect/example"],
            }
            for _ in range(4)
        ]
    }
    report = build_fact_check(script, tmp_path / "fact_check.json")
    assert report["passed"] is True
    assert report["claims"][0]["sources"][0]["resolved_url"] == "https://science.nasa.gov/earth/facts/"
    assert report["claims"][0]["sources"][0]["authoritative"] is True


def test_extract_grounding_sources_uses_structured_metadata_only():
    response = _grounding_response(
        ("https://vertexaisearch.cloud.google.com/a", "NASA"),
        ("https://vertexaisearch.cloud.google.com/a", "duplicate"),
        ("https://vertexaisearch.cloud.google.com/b", "USGS"),
    )
    sources = extract_grounding_sources(response)
    assert sources == [
        {"url": "https://vertexaisearch.cloud.google.com/a", "title": "NASA"},
        {"url": "https://vertexaisearch.cloud.google.com/b", "title": "USGS"},
    ]


def test_resolve_grounding_sources_keeps_only_reachable_authoritative_destinations(monkeypatch):
    response = _grounding_response(
        ("https://vertexaisearch.cloud.google.com/a", "NASA"),
        ("https://vertexaisearch.cloud.google.com/b", "shop"),
        ("https://vertexaisearch.cloud.google.com/c", "dead"),
    )

    def fake_verify(url: str, timeout: int = 10):
        if url.endswith("/a"):
            return True, "HTTP 200", "https://science.nasa.gov/earth/facts/"
        if url.endswith("/b"):
            return True, "HTTP 200", "https://random-shop.com/article"
        return False, "HTTP 404", url

    monkeypatch.setattr("src.fact_check.verify_url_details", fake_verify)
    assert resolve_grounding_sources(response) == ["https://science.nasa.gov/earth/facts/"]


def test_grounded_scene_repair_updates_claim_narration_and_api_sources_only():
    script = {
        "narration": "",
        "narration_word_count": 0,
        "scenes": [
            {
                "narration": "Air and oceans would keep moving because inertia does not disappear when rotation stops.",
                "claim": "Unsupported dramatic claim",
                "source_note": "old",
                "sources": ["https://nasa.gov/dead"],
            },
            {"narration": "This second scene stays exactly as originally written for the finished short.", "claim": "two", "source_note": "keep", "sources": ["https://usgs.gov/valid"]},
            {"narration": "This third scene also remains unchanged while the failed first scene is repaired.", "claim": "three", "source_note": "keep", "sources": ["https://noaa.gov/valid"]},
            {"narration": "This fourth scene remains unchanged and completes the structured factual video narration.", "claim": "four", "source_note": "keep", "sources": ["https://nih.gov/valid"]},
        ],
    }
    payload = {
        "claim": "Moving air and water retain momentum unless forces change their motion.",
        "narration": "Air and oceans would keep moving because inertia does not vanish when rotation stops.",
        "source_note": "Grounded physics sources support inertia and continuing motion.",
        "sources": ["https://model-invented.example/ignored"],
    }
    repaired = apply_grounded_scene_repair(
        script,
        1,
        payload,
        ["https://openstax.org/books/physics/pages/4-2-newtons-first-law-of-motion-inertia"],
    )
    assert repaired["scenes"][0]["claim"].startswith("Moving air")
    assert repaired["scenes"][0]["sources"] == [
        "https://openstax.org/books/physics/pages/4-2-newtons-first-law-of-motion-inertia"
    ]
    assert repaired["scenes"][1] == script["scenes"][1]
    assert script["scenes"][0]["sources"] == ["https://nasa.gov/dead"]


def test_full_repair_never_uses_urls_written_in_model_text(monkeypatch):
    import json
    import sys
    from types import ModuleType

    from src.script_gen import repair_script_sources, word_count

    narration = "Air and oceans retain their motion through inertia when a rotating system changes suddenly, so effects depend on forces and stopping time."
    assert word_count(narration) == 22
    script = {
        "topic": "What if Earth stopped spinning",
        "narration": " ".join([narration] * 4),
        "narration_word_count": 88,
        "scenes": [
            {
                "narration": narration,
                "claim": f"claim {index}",
                "source_note": "old",
                "sources": ["https://nasa.gov/dead"],
            }
            for index in range(1, 5)
        ],
    }
    fact_report = {
        "passed": False,
        "claims": [{"scene": 1, "passed": False, "sources": []}],
    }
    response = _grounding_response(("https://vertexaisearch.cloud.google.com/a", "OpenStax"))
    response.text = json.dumps(
        {
            "claim": "Objects retain motion unless acted upon by a net external force.",
            "narration": narration,
            "source_note": "The grounded source explains Newton's first law and inertia.",
            "sources": ["https://invented.example/never-use-this"],
        }
    )

    class FakeModels:
        def generate_content(self, **kwargs):
            assert kwargs["config"]["tools"]
            return response

    genai_module = ModuleType("google.genai")
    genai_module.Client = lambda api_key: SimpleNamespace(models=FakeModels())
    genai_module.types = SimpleNamespace(
        GenerateContentConfig=lambda **kwargs: kwargs,
        Tool=lambda **kwargs: kwargs,
        GoogleSearch=lambda: {},
    )
    google_module = ModuleType("google")
    google_module.genai = genai_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.script_gen.resolve_grounding_sources",
        lambda response: ["https://openstax.org/books/physics/pages/4-2-newtons-first-law-of-motion-inertia"],
    )

    repaired = repair_script_sources(script, fact_report)
    assert repaired["scenes"][0]["sources"] == [
        "https://openstax.org/books/physics/pages/4-2-newtons-first-law-of-motion-inertia"
    ]
    assert "invented.example" not in json.dumps(repaired)
    assert repaired["narration_word_count"] == 88
