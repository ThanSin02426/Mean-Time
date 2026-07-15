from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .atomic_io import read_json
from .topic_engine import NICHES, QueueManager

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You create factual, high-retention English YouTube Shorts scripts.
Return JSON only. Never invent a source URL. Use authoritative primary or institutional sources.
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
      "source_note": "why the source supports the claim",
      "sources": ["https://..."]
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
        if not scene.get("sources"):
            raise ValueError(f"Scene {index} has no factual source")
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
    data["niche"] = str(data.get("niche") or QueueManager.categorize(topic))
    if data["niche"] not in NICHES:
        raise ValueError(f"Unknown niche: {data['niche']}")
    count_match = re.search(r"\b(\d+)\b", title)
    if count_match and int(count_match.group(1)) != len(scenes):
        raise ValueError("Numeric title count does not match the number of scenes")
    data.setdefault("mood", "curious")
    data["description"] = str(data.get("description", "")).strip()
    if "#Shorts" not in data["description"]:
        data["description"] = (data["description"] + " #Shorts").strip()
    return data



def apply_source_repairs(
    script_data: dict[str, Any],
    repair_payload: dict[str, Any],
    failed_scene_numbers: set[int],
) -> dict[str, Any]:
    """Apply source-only repairs without changing narration, claims, or scene order."""
    repaired = deepcopy(script_data)
    scenes = repaired.get("scenes", [])
    applied = 0
    for row in repair_payload.get("repairs", []):
        try:
            scene_number = int(row.get("scene"))
        except (TypeError, ValueError):
            continue
        if scene_number not in failed_scene_numbers or not 1 <= scene_number <= len(scenes):
            continue
        sources = row.get("sources") or []
        if isinstance(sources, str):
            sources = [sources]
        cleaned: list[str] = []
        for source in sources:
            value = str(source).strip()
            if value.startswith(("https://", "http://")) and value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            continue
        scene = scenes[scene_number - 1]
        scene["sources"] = cleaned
        note = str(row.get("source_note", "")).strip()
        if note:
            scene["source_note"] = note
        applied += 1
    if applied == 0:
        raise ValueError("Source repair response did not contain usable replacements for failed scenes")
    return repaired


def repair_script_sources(script_data: dict[str, Any], fact_report: dict[str, Any]) -> dict[str, Any]:
    """Use grounded Google Search once to replace only dead/non-authoritative URLs."""
    failed_rows = [row for row in fact_report.get("claims", []) if not row.get("passed")]
    failed_scene_numbers = {int(row["scene"]) for row in failed_rows if row.get("scene")}
    if not failed_scene_numbers:
        return script_data

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for automatic source repair")

    from google import genai
    from google.genai import types

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    failed_context = []
    for row in failed_rows:
        scene_number = int(row["scene"])
        scene = script_data["scenes"][scene_number - 1]
        failed_context.append({
            "scene": scene_number,
            "claim": scene.get("claim", ""),
            "source_note": scene.get("source_note", ""),
            "failed_sources": row.get("sources", []),
        })

    prompt = f"""
Search the live web and repair ONLY the source URLs for the failed factual scenes below.
Do not rewrite narration, claims, title, topic, or scene order.
For each failed scene, return 2 or 3 direct, currently reachable URLs from authoritative primary or institutional sources that support the exact claim.
Prefer NASA, NOAA, USGS, NIH, WHO, universities, peer-reviewed journals, museums, national academies, ESA, or CERN.
Do not guess URLs. Do not use dead legacy paths, guessed /wp-content/uploads paths, search-result pages, or generic homepages when a claim-specific page exists.
Return JSON only in this exact schema:
{{"repairs":[{{"scene":1,"source_note":"why these sources support the claim","sources":["https://...","https://..."]}}]}}

Topic: {script_data.get('topic', '')}
Failed scenes: {json.dumps(failed_context, ensure_ascii=False)}
"""
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.1,
        ),
    )
    payload = _extract_json(response.text or "")
    return apply_source_repairs(script_data, payload, failed_scene_numbers)

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
