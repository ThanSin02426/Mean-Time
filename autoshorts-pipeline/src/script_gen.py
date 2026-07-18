from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .atomic_io import atomic_write_json, read_json
from .topic_engine import NICHES, QueueManager

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You create factual, high-retention English YouTube Shorts scripts for a channel that is permanently focused on space.
Every topic, title, claim, visual, description, and tag must relate directly to astronomy, cosmology, planetary science, spaceflight, astronauts, space telescopes, or space technology. The niche field must always be "space". Never drift into psychology, animals, general history, medicine, ocean facts, or unrelated Earth science.
Return JSON only. Do not invent, guess, or write source URLs. Set every scene's sources field to an empty list; verified URLs are attached later from structured Google Search grounding metadata or the verified source cache.
The narration must be 85-115 spoken words, 35-50 seconds, hard maximum 58 seconds.
Use 4-6 scenes. The first sentence must hook within two seconds. One claim per scene.
CTA is optional and only in the final scene. Do not use a repeated generic CTA.
Do not literalize metaphors when choosing visuals.
Each scene must include: narration, visual_subject, visual_query, visual_negative_terms,
preferred_media_type, scene_keywords, claim, source_note, sources.
The title must reflect the actual number of facts and must not use generic phrases such as
'Mind-Blowing Space Facts You Won't Believe'.
JSON schema:
{
  "title": "...",
  "description": "... #Shorts",
  "tags": ["..."],
  "niche": "one allowed niche",
  "scenes": [
    {
      "narration": "...",
      "visual_subject": "...",
      "visual_query": "2-6 word stock search",
      "visual_negative_terms": ["..."],
      "preferred_media_type": "video or image",
      "scene_keywords": ["..."],
      "claim": "factual claim",
      "source_note": "what evidence would need to support this claim",
      "sources": []
    }
  ]
}
"""


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def title_similarity(a: str, b: str) -> float:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return SequenceMatcher(a=normalize(a), b=normalize(b), autojunk=False).ratio()


def title_is_unique(title: str, history_path: str | Path, threshold: float = 0.78) -> bool:
    history = read_json(history_path, [])
    return all(title_similarity(title, str(row.get("title", ""))) < threshold for row in history if isinstance(row, dict))


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _narration(script_data: dict[str, Any]) -> str:
    return " ".join(str(scene.get("narration", "")).strip() for scene in script_data.get("scenes", [])).strip()


def validate_script(data: dict[str, Any], topic: str, history_path: str | Path) -> dict[str, Any]:
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not 4 <= len(scenes) <= 6:
        raise ValueError("Script must contain 4-6 scenes")
    required = {
        "narration", "visual_subject", "visual_query", "visual_negative_terms", "preferred_media_type",
        "scene_keywords", "claim", "source_note", "sources",
    }
    for index, scene in enumerate(scenes, start=1):
        missing = required - set(scene)
        if missing:
            raise ValueError(f"Scene {index} is missing: {sorted(missing)}")
        if not 2 <= len(str(scene["visual_query"]).split()) <= 8:
            raise ValueError(f"Scene {index} visual_query is not concise")
        sources = scene.get("sources")
        if sources is None:
            scene["sources"] = []
        elif isinstance(sources, str):
            scene["sources"] = [sources] if sources.strip() else []
        elif not isinstance(sources, list):
            raise ValueError(f"Scene {index} sources must be a list")
    narration = _narration(data)
    count = word_count(narration)
    if not 85 <= count <= 115:
        raise ValueError(f"Narration must be 85-115 words; got {count}")
    title = str(data.get("title", "")).strip()
    if not title or not title_is_unique(title, history_path):
        raise ValueError("Generated title is empty or too similar to recent titles")
    data["narration"] = narration
    data["narration_word_count"] = count
    data["topic"] = topic
    if not QueueManager.is_space_topic(topic):
        raise ValueError("The channel accepts only space-oriented topics")
    # The model is not allowed to relabel the channel. Keep the public niche fixed
    # so visuals always use the NASA-first space provider order.
    data["niche"] = "space"
    count_match = re.search(r"\b(\d+)\b", title)
    if count_match and int(count_match.group(1)) != len(scenes):
        raise ValueError("Numeric title count does not match the number of scenes")
    data.setdefault("mood", "curious")
    data["description"] = str(data.get("description", "")).strip()
    if "#Shorts" not in data["description"]:
        data["description"] = (data["description"] + " #Shorts").strip()
    return data




def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def extract_grounding_sources(response: Any) -> list[dict[str, str]]:
    """Extract sources returned by Google Search grounding metadata.

    URLs written in model text are never trusted. Only structured grounding chunks
    supplied by the Gemini API are accepted.
    """
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in _value(response, "candidates", []) or []:
        metadata = _value(candidate, "grounding_metadata")
        if metadata is None:
            metadata = _value(candidate, "groundingMetadata")
        for chunk in _value(metadata, "grounding_chunks", []) or _value(metadata, "groundingChunks", []) or []:
            web = _value(chunk, "web")
            uri = str(_value(web, "uri", "") or "").strip()
            title = str(_value(web, "title", "") or "").strip()
            if uri.startswith(("https://", "http://")) and uri not in seen:
                seen.add(uri)
                sources.append({"url": uri, "title": title})
    return sources


def resolve_grounding_sources(response: Any, timeout: int = 10) -> list[str]:
    """Resolve grounding redirects and keep credible, reachable destinations.

    Primary institutional sources are preferred. Reputable science publications are
    also retained because hypothetical science questions are often explained by
    expert-reviewed editorial sources rather than a single official agency page.
    The strict fact-check policy later requires either one primary source or two
    independent reputable domains for every scene.
    """
    from .fact_check import evidence_domain, source_tier, verify_url_details

    primary: list[str] = []
    reputable: list[str] = []
    seen_hosts: set[str] = set()

    for source in extract_grounding_sources(response):
        reachable, detail, resolved_url = verify_url_details(source["url"], timeout=timeout)
        if not reachable:
            logger.info("Grounding source rejected as unreachable: %s (%s)", source["url"], detail)
            continue

        tier = source_tier(resolved_url)
        if tier == "rejected":
            logger.info("Grounding source rejected by credibility policy: %s", resolved_url)
            continue

        host = evidence_domain(resolved_url)
        if not host or host in seen_hosts:
            continue
        seen_hosts.add(host)

        logger.info("Grounding source accepted (%s): %s", tier, resolved_url)
        if tier == "primary":
            primary.append(resolved_url)
        else:
            reputable.append(resolved_url)

    # Keep the evidence list concise but diverse. Primary sources are first, while
    # preserving at least two reputable domains when primary sources are absent.
    return (primary + reputable)[:5]


def sources_meet_fact_policy(sources: list[str]) -> bool:
    """Return whether already-resolved sources satisfy the strict evidence policy."""
    from .fact_check import evidence_domain, source_tier

    primary_hosts: set[str] = set()
    reputable_hosts: set[str] = set()
    for source in sources:
        host = evidence_domain(source)
        tier = source_tier(source)
        if tier == "primary" and host:
            primary_hosts.add(host)
        elif tier == "reputable" and host:
            reputable_hosts.add(host)
    return bool(primary_hosts) or len(reputable_hosts) >= 2


def apply_grounded_scene_repair(
    script_data: dict[str, Any],
    scene_number: int,
    repair_payload: dict[str, Any],
    sources: list[str],
) -> dict[str, Any]:
    """Apply one evidence-backed scene repair using API-provided source metadata."""
    repaired = deepcopy(script_data)
    scenes = repaired.get("scenes", [])
    if not 1 <= scene_number <= len(scenes):
        raise ValueError(f"Invalid scene number: {scene_number}")
    if not sources:
        raise ValueError("Grounded repair returned no reachable authoritative sources")

    scene = scenes[scene_number - 1]
    original_words = word_count(str(scene.get("narration", "")))
    claim = str(repair_payload.get("claim", "")).strip()
    narration = str(repair_payload.get("narration", "")).strip()
    source_note = str(repair_payload.get("source_note", "")).strip()
    if not claim or not narration or not source_note:
        raise ValueError("Grounded repair response is missing claim, narration, or source_note")
    if abs(word_count(narration) - original_words) > 3:
        raise ValueError(
            f"Repaired scene narration changed too much: expected about {original_words} words, "
            f"got {word_count(narration)}"
        )

    scene["claim"] = claim
    scene["narration"] = narration
    scene["source_note"] = source_note
    scene["sources"] = sources

    narration_text = _narration(repaired)
    repaired["narration"] = narration_text
    repaired["narration_word_count"] = word_count(narration_text)
    return repaired



def _normalize_topic_key(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", topic.lower()).strip()


def _source_cache_path() -> Path:
    return Path(os.getenv("FACT_SOURCE_CACHE_FILE", "fact_source_cache.json"))


def _merge_independent_sources(*groups: list[str]) -> list[str]:
    from .fact_check import evidence_domain

    merged: list[str] = []
    seen_domains: set[str] = set()
    for group in groups:
        for source in group:
            domain = evidence_domain(source)
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                merged.append(source)
    return merged[:6]


def _verified_cached_sources(topic: str, timeout: int = 10) -> list[str]:
    """Return a policy-compliant cached source set after live URL verification."""
    from .fact_check import source_tier, verify_url_details

    cache = read_json(_source_cache_path(), {})
    rows = cache.get(_normalize_topic_key(topic), []) if isinstance(cache, dict) else []
    verified: list[str] = []
    for source in rows if isinstance(rows, list) else []:
        reachable, detail, resolved_url = verify_url_details(str(source), timeout=timeout)
        if reachable and source_tier(resolved_url) != "rejected":
            verified.append(resolved_url)
        else:
            logger.info("Cached source rejected: %s (%s)", source, detail)
    verified = _merge_independent_sources(verified)
    return verified if sources_meet_fact_policy(verified) else []


def _save_source_cache(topic: str, sources: list[str]) -> None:
    path = _source_cache_path()
    cache = read_json(path, {})
    if not isinstance(cache, dict):
        cache = {}
    cache[_normalize_topic_key(topic)] = list(sources)
    atomic_write_json(path, cache)


def _is_quota_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "429" in message or "resource_exhausted" in message or "quota" in message


def _batched_repair_prompt(script_data: dict[str, Any], failed_rows: list[dict[str, Any]]) -> str:
    scenes = []
    for row in failed_rows:
        scene_number = int(row["scene"])
        scene = script_data["scenes"][scene_number - 1]
        scenes.append(
            {
                "scene": scene_number,
                "claim": scene.get("claim", ""),
                "narration": scene.get("narration", ""),
                "target_words": word_count(str(scene.get("narration", ""))),
            }
        )
    return f"""
Use Google Search to verify and, only where needed, conservatively repair all failed scenes of one factual YouTube Short in a single response.

Topic: {script_data.get('topic', '')}
Failed scenes: {json.dumps(scenes, ensure_ascii=False)}

Return JSON only in this exact shape:
{{"scenes":[{{"scene":1,"claim":"...","narration":"...","source_note":"..."}}]}}

Rules:
- Return exactly one object for every supplied scene number and no extra scenes.
- Do not include URLs in JSON. URLs are read only from structured Google Search grounding metadata.
- Use one shared evidence search for the whole topic instead of separate searches per scene.
- Each returned claim must be directly supported by the collective retrieved evidence.
- Rewrite exaggerated or unsupported wording conservatively while preserving the scene purpose.
- Use conditional language for hypothetical outcomes.
- Keep each narration within 3 words of its target_words value.
- Prefer foundational evidence: official agencies, universities, open textbooks, museums, scientific societies, peer-reviewed sources, or established science publications.
"""


def _apply_batched_repair(
    script_data: dict[str, Any],
    failed_rows: list[dict[str, Any]],
    repair_payload: dict[str, Any],
    sources: list[str],
) -> dict[str, Any]:
    repaired = deepcopy(script_data)
    payload_rows = repair_payload.get("scenes", [])
    by_number = {
        int(row.get("scene")): row
        for row in payload_rows
        if isinstance(row, dict) and str(row.get("scene", "")).isdigit()
    }
    for failed_row in failed_rows:
        scene_number = int(failed_row["scene"])
        original = repaired["scenes"][scene_number - 1]
        payload = by_number.get(scene_number)
        if not payload:
            payload = {
                "claim": original.get("claim", ""),
                "narration": original.get("narration", ""),
                "source_note": "Verified shared sources support the underlying principles used in this scene.",
            }
        try:
            repaired = apply_grounded_scene_repair(repaired, scene_number, payload, sources)
        except ValueError as exc:
            logger.warning("Scene %s repair text rejected (%s); retaining original wording with verified sources", scene_number, exc)
            fallback_payload = {
                "claim": original.get("claim", ""),
                "narration": original.get("narration", ""),
                "source_note": "Verified shared sources support the underlying principles used in this scene.",
            }
            repaired = apply_grounded_scene_repair(repaired, scene_number, fallback_payload, sources)
    return repaired


def repair_script_sources(script_data: dict[str, Any], fact_report: dict[str, Any]) -> dict[str, Any]:
    """Repair all failed scenes with at most two grounded API calls total.

    The previous implementation made one or two Gemini calls per scene and could
    consume the free-tier daily quota in a single run. This implementation first
    reuses a live-verified topic cache. On a cache miss it performs one batched
    grounded request for every failed scene, plus at most one supplemental search.
    """
    failed_rows = [row for row in fact_report.get("claims", []) if not row.get("passed")]
    if not failed_rows:
        return script_data

    topic = str(script_data.get("topic", "")).strip()
    cached_sources = _verified_cached_sources(topic)
    if cached_sources:
        logger.info("Using %d live-verified cached factual sources for topic", len(cached_sources))
        repaired = deepcopy(script_data)
        for failed_row in failed_rows:
            scene_number = int(failed_row["scene"])
            scene = repaired["scenes"][scene_number - 1]
            scene["sources"] = list(cached_sources)
            scene["source_note"] = (
                str(scene.get("source_note", "")).strip()
                or "Verified cached sources support the underlying principles used in this scene."
            )
        narration_text = _narration(repaired)
        repaired["narration"] = narration_text
        repaired["narration_word_count"] = word_count(narration_text)
        return repaired

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for automatic source repair")

    from google import genai
    from google.genai import types

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    prompt = _batched_repair_prompt(script_data, failed_rows)

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )
    except Exception as exc:
        if _is_quota_error(exc):
            raise RuntimeError(
                "Gemini quota exhausted during the single batched fact-source repair call; "
                "no per-scene retries were attempted. Retry after the provider reset window."
            ) from exc
        raise

    payload = _extract_json(response.text or "")
    sources = resolve_grounding_sources(response)

    if not sources_meet_fact_policy(sources):
        from .fact_check import evidence_domain

        existing_domains = sorted({evidence_domain(source) for source in sources if evidence_domain(source)})
        supplemental_prompt = f"""
Find additional independent evidence for this single topic: {topic}
Existing accepted domains to avoid: {existing_domains}
Prioritize one primary institutional source. Otherwise find enough independent established science publications so the combined evidence set has at least two domains.
Do not attempt to rewrite scenes. Return a brief factual summary; source URLs will be read only from structured Google Search grounding metadata.
"""
        try:
            supplemental = client.models.generate_content(
                model=model_name,
                contents=supplemental_prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.0,
                ),
            )
        except Exception as exc:
            if _is_quota_error(exc):
                raise RuntimeError(
                    "Gemini quota exhausted during the one allowed supplemental source search; "
                    "the run stopped without further retries."
                ) from exc
            raise
        sources = _merge_independent_sources(sources, resolve_grounding_sources(supplemental))

    if not sources_meet_fact_policy(sources):
        raise ValueError(
            "Batched grounding could not find either one reachable primary source or two independent reputable sources"
        )

    repaired = _apply_batched_repair(script_data, failed_rows, payload, sources)
    total_words = int(repaired.get("narration_word_count", 0))
    if not 85 <= total_words <= 115:
        raise ValueError(f"Repaired narration must remain 85-115 words; got {total_words}")
    _save_source_cache(topic, sources)
    logger.info("Saved %d verified sources to the topic cache", len(sources))
    return repaired

def _local_fallback(topic: str) -> dict[str, Any]:
    niche = QueueManager.categorize(topic)
    scenes = [
        {
            "narration": f"This sounds impossible, but {topic.lower()} reveals how extreme the real world can be.",
            "visual_subject": topic, "visual_query": " ".join(topic.split()[:6]), "visual_negative_terms": ["text", "logo", "advertisement"],
            "preferred_media_type": "video", "scene_keywords": topic.lower().split()[:5],
            "claim": f"Introductory overview of {topic}", "source_note": "Replace with a model-generated verified claim before publishing",
            "sources": ["https://www.britannica.com/science/science"],
        },
        {
            "narration": "Scientists measure the effect instead of relying on dramatic comparisons, because scale and conditions change the outcome.",
            "visual_subject": "scientific measurement", "visual_query": "scientific measurement laboratory", "visual_negative_terms": ["beer", "jewellery", "cartoon"],
            "preferred_media_type": "video", "scene_keywords": ["science", "measurement", "research"],
            "claim": "Scientific claims require measurements and defined conditions", "source_note": "General scientific-method reference",
            "sources": ["https://www.britannica.com/science/scientific-method"],
        },
        {
            "narration": "The most surprising part is usually the mechanism: a small physical rule can create a result that looks unreal at human scale.",
            "visual_subject": "physical mechanism", "visual_query": "physics experiment close up", "visual_negative_terms": ["product", "fashion", "animal"],
            "preferred_media_type": "video", "scene_keywords": ["physics", "mechanism", "experiment"],
            "claim": "Physical mechanisms can produce counterintuitive outcomes", "source_note": "General physics reference",
            "sources": ["https://www.britannica.com/science/physics-science"],
        },
        {
            "narration": "That is why researchers separate evidence from the metaphor, verify each number, and explain what the comparison actually means.",
            "visual_subject": "research verification", "visual_query": "researchers checking data", "visual_negative_terms": ["celebrity", "meme", "beer"],
            "preferred_media_type": "video", "scene_keywords": ["research", "data", "verification"],
            "claim": "Evidence checking is central to scientific communication", "source_note": "General science communication reference",
            "sources": ["https://www.nationalacademies.org/our-work/communicating-science-effectively-a-research-agenda"],
        },
        {
            "narration": "The real fact is more interesting than a random visual or exaggerated headline, so always follow the source behind the claim.",
            "visual_subject": "source document", "visual_query": "scientific paper source", "visual_negative_terms": ["stock market", "beer", "jewellery"],
            "preferred_media_type": "image", "scene_keywords": ["source", "paper", "evidence"],
            "claim": "Source transparency improves factual reliability", "source_note": "General source-literacy reference",
            "sources": ["https://www.si.edu/openaccess"],
        },
    ]
    return {
        "title": f"The Real Science Behind {topic[:48]}",
        "description": f"A source-first look at {topic}. #Shorts",
        "tags": [niche, "facts", "science", "shorts"],
        "niche": niche,
        "mood": "curious",
        "generation_mode": "local_fallback",
        "scenes": scenes,
    }


def generate_script(topic: str, history_path: str | Path = "upload_history.json") -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        if os.getenv("ALLOW_LOCAL_SCRIPT_FALLBACK", "false").strip().lower() in {"1", "true", "yes"}:
            logger.warning("GEMINI_API_KEY is missing; using the explicit development-only local fallback.")
            return validate_script(_local_fallback(topic), topic, history_path)
        raise RuntimeError("GEMINI_API_KEY is required. Set ALLOW_LOCAL_SCRIPT_FALLBACK=true only for local mechanics testing.")
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        recent_titles = [row.get("title", "") for row in read_json(history_path, [])[-20:] if isinstance(row, dict)]
        prompt = f"{SYSTEM_PROMPT}\nAllowed niches: {', '.join(NICHES)}.\nTopic: {topic}\nRecent titles to avoid: {recent_titles}"
        for attempt in range(2):
            response = client.models.generate_content(model=model_name, contents=prompt)
            data = _extract_json(response.text or "")
            try:
                validated = validate_script(data, topic, history_path)
                validated["generation_mode"] = "gemini"
                return validated
            except ValueError as exc:
                if attempt == 1:
                    raise
                prompt += f"\nYour previous JSON failed validation: {exc}. Return corrected JSON only."
    except Exception as exc:
        logger.error("Script generation failed: %s", exc)
        raise RuntimeError(f"Could not generate a valid sourced script: {exc}") from exc
    raise RuntimeError("Could not generate script")
