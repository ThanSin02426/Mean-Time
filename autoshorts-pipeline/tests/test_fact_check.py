from pathlib import Path
from types import SimpleNamespace

from src.fact_check import (
    build_fact_check,
    is_authoritative,
    is_reputable_science,
    source_tier,
)
from src.script_gen import (
    apply_grounded_scene_repair,
    extract_grounding_sources,
    resolve_grounding_sources,
)


def _grounding_response(*rows: tuple[str, str]):
    chunks = [SimpleNamespace(web=SimpleNamespace(uri=url, title=title)) for url, title in rows]
    metadata = SimpleNamespace(grounding_chunks=chunks)
    return SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=metadata)])


def _script_with_sources(sources: list[str]) -> dict:
    return {
        "scenes": [
            {
                "claim": f"claim {index}",
                "source_note": "evidence",
                "sources": list(sources),
            }
            for index in range(1, 5)
        ]
    }


def test_source_tiers_do_not_accept_random_or_forum_domains():
    assert is_authoritative("https://science.nasa.gov/example")
    assert is_authoritative("https://example.edu/research")
    assert is_authoritative("https://openstax.org/books/physics/pages/1-introduction")
    assert is_reputable_science("https://www.space.com/example")
    assert is_reputable_science("https://www.smithsonianmag.com/example")
    assert source_tier("https://www.astronomy.com/example") == "reputable"
    assert source_tier("https://random-shop.com/science") == "rejected"
    assert source_tier("https://www.quora.com/question") == "rejected"


def test_scene_passes_with_one_reachable_primary_source(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "src.fact_check.verify_url_details",
        lambda url, timeout=10: (True, "HTTP 200", "https://science.nasa.gov/earth/facts/"),
    )
    report = build_fact_check(
        _script_with_sources(["https://vertexaisearch.cloud.google.com/redirect"]),
        tmp_path / "fact_check.json",
    )
    assert report["passed"] is True
    assert report["claims"][0]["evidence_reason"] == "one_or_more_reachable_primary_sources"
    assert report["claims"][0]["sources"][0]["tier"] == "primary"


def test_scene_passes_with_two_independent_reputable_sources(monkeypatch, tmp_path: Path):
    def fake_verify(url: str, timeout: int = 10):
        if url.endswith("/space"):
            return True, "HTTP 200", "https://www.space.com/what-would-happen-if-earth-stopped-spinning"
        return True, "HTTP 200", "https://www.astronomy.com/science/what-happens-if-earth-stops-rotating/"

    monkeypatch.setattr("src.fact_check.verify_url_details", fake_verify)
    report = build_fact_check(
        _script_with_sources(["https://redirect/space", "https://redirect/astronomy"]),
        tmp_path / "fact_check.json",
    )
    assert report["passed"] is True
    assert report["claims"][0]["evidence_reason"] == "two_or_more_independent_reputable_sources"


def test_one_reputable_source_without_primary_is_not_enough(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "src.fact_check.verify_url_details",
        lambda url, timeout=10: (
            True,
            "HTTP 200",
            "https://www.space.com/what-would-happen-if-earth-stopped-spinning",
        ),
    )
    report = build_fact_check(
        _script_with_sources(["https://redirect/one"]),
        tmp_path / "fact_check.json",
    )
    assert report["passed"] is False
    assert report["claims"][0]["evidence_reason"] == "only_one_reputable_source_and_no_primary_source"


def test_duplicate_reputable_domain_does_not_count_twice(monkeypatch, tmp_path: Path):
    def fake_verify(url: str, timeout: int = 10):
        suffix = "a" if url.endswith("/a") else "b"
        return True, "HTTP 200", f"https://www.space.com/article-{suffix}"

    monkeypatch.setattr("src.fact_check.verify_url_details", fake_verify)
    report = build_fact_check(
        _script_with_sources(["https://redirect/a", "https://redirect/b"]),
        tmp_path / "fact_check.json",
    )
    assert report["passed"] is False


def test_extract_grounding_sources_uses_structured_metadata_only():
    response = _grounding_response(
        ("https://vertexaisearch.cloud.google.com/a", "NASA"),
        ("https://vertexaisearch.cloud.google.com/a", "duplicate"),
        ("https://vertexaisearch.cloud.google.com/b", "USGS"),
    )
    assert extract_grounding_sources(response) == [
        {"url": "https://vertexaisearch.cloud.google.com/a", "title": "NASA"},
        {"url": "https://vertexaisearch.cloud.google.com/b", "title": "USGS"},
    ]


def test_resolve_grounding_sources_keeps_primary_and_reputable_but_rejects_low_quality(monkeypatch):
    response = _grounding_response(
        ("https://vertexaisearch.cloud.google.com/a", "NASA"),
        ("https://vertexaisearch.cloud.google.com/b", "Space"),
        ("https://vertexaisearch.cloud.google.com/c", "Astronomy"),
        ("https://vertexaisearch.cloud.google.com/d", "Quora"),
        ("https://vertexaisearch.cloud.google.com/e", "dead"),
    )

    def fake_verify(url: str, timeout: int = 10):
        if url.endswith("/a"):
            return True, "HTTP 200", "https://science.nasa.gov/earth/facts/"
        if url.endswith("/b"):
            return True, "HTTP 200", "https://www.space.com/what-would-happen-if-earth-stopped-spinning"
        if url.endswith("/c"):
            return True, "HTTP 200", "https://www.astronomy.com/science/what-happens-if-earth-stops-rotating/"
        if url.endswith("/d"):
            return True, "HTTP 200", "https://www.quora.com/question"
        return False, "HTTP 404", url

    monkeypatch.setattr("src.fact_check.verify_url_details", fake_verify)
    assert resolve_grounding_sources(response) == [
        "https://science.nasa.gov/earth/facts/",
        "https://www.space.com/what-would-happen-if-earth-stopped-spinning",
        "https://www.astronomy.com/science/what-happens-if-earth-stops-rotating/",
    ]


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
    fact_report = {"passed": False, "claims": [{"scene": 1, "passed": False, "sources": []}]}
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


def test_logged_earth_stop_grounding_mix_is_accepted_without_ssl_bypass(monkeypatch, tmp_path: Path):
    """Regression for run #4: dead/SSL sources are ignored; two good science sources pass."""
    response = _grounding_response(
        ("https://redirect/ifa", "University of Hawaii"),
        ("https://redirect/space", "Space.com"),
        ("https://redirect/live", "Live Science"),
        ("https://redirect/quora", "Quora"),
    )

    def fake_verify(url: str, timeout: int = 10):
        if url.endswith("/ifa"):
            return False, "SSL certificate verify failed", "https://www.ifa.hawaii.edu/old-page"
        if url.endswith("/space"):
            return True, "HTTP 200", "https://www.space.com/what-would-happen-if-earth-stopped-spinning"
        if url.endswith("/live"):
            return True, "HTTP 200", "https://www.livescience.com/what-if-earth-stopped-spinning.html"
        return True, "HTTP 200", "https://www.quora.com/question"

    monkeypatch.setattr("src.fact_check.verify_url_details", fake_verify)
    resolved = resolve_grounding_sources(response)
    assert resolved == [
        "https://www.space.com/what-would-happen-if-earth-stopped-spinning",
        "https://www.livescience.com/what-if-earth-stopped-spinning.html",
    ]

    # The final verification uses the same resolved destinations.
    monkeypatch.setattr(
        "src.fact_check.verify_url_details",
        lambda url, timeout=10: (True, "HTTP 200", url),
    )
    report = build_fact_check(_script_with_sources(resolved), tmp_path / "fact_check.json")
    assert report["passed"] is True
    assert all(row["evidence_reason"] == "two_or_more_independent_reputable_sources" for row in report["claims"])


def test_sources_meet_fact_policy_requires_independence():
    from src.script_gen import sources_meet_fact_policy

    assert sources_meet_fact_policy(["https://science.nasa.gov/earth/facts/"])
    assert not sources_meet_fact_policy(["https://www.space.com/a"])
    assert not sources_meet_fact_policy(["https://www.space.com/a", "https://www.space.com/b"])
    assert sources_meet_fact_policy([
        "https://www.space.com/a",
        "https://www.astronomy.com/b",
    ])
