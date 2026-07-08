import os
import json
import logging
import requests
import re
import google.generativeai as genai

logger = logging.getLogger(__name__)

MIN_WORDS = int(os.environ.get("MIN_NARRATION_WORDS", "95"))
TARGET_MIN_WORDS = int(os.environ.get("TARGET_MIN_NARRATION_WORDS", "100"))
TARGET_MAX_WORDS = int(os.environ.get("TARGET_MAX_NARRATION_WORDS", "125"))
SCENE_COUNT = int(os.environ.get("SHORT_SCENE_COUNT", "5"))

PROMPT_TEMPLATE = """
You are a retention-focused YouTube Shorts writer.
Create a complete short about: "{topic}".

Return STRICT JSON only. No markdown. No comments.

Hard requirements:
- Exactly {scene_count} scenes.
- Total narration MUST be {min_words}-{max_words} spoken words.
- Target length is 35-50 seconds when read by TTS.
- First sentence must be a strong 2-second hook.
- Short punchy sentences only.
- If the topic says 3 facts, do not mention 10 facts.
- CTA only once near the end.
- Generate short stock-media search queries, not long image prompts.

JSON shape:
{{
  "script": "Full narration here, {min_words}-{max_words} words.",
  "title": "Punchy YouTube Short Title #Shorts",
  "description": "Short description with #Shorts",
  "tags": ["tag1", "tag2", "tag3"],
  "scenes": [
    {{
      "text": "2-6 word visual headline",
      "support": "short supporting line",
      "search_query": "2 to 5 words for stock media search"
    }}
  ]
}}
"""

CATEGORY_KEYWORDS = {
    "space": ["space", "planet", "galaxy", "black hole", "nasa", "moon", "mars", "asteroid", "universe", "star", "solar"],
    "ocean": ["ocean", "sea", "deep sea", "marine", "shark", "whale"],
    "animals": ["animal", "animals", "wildlife", "creature", "predator", "birds", "insects"],
    "history": ["history", "ancient", "civilization", "empire", "war", "king", "queen"],
    "psychology": ["psychology", "brain", "mind", "habit", "human behavior"],
    "ai": ["artificial intelligence", "ai", "robot", "technology", "future tech"],
}


def _category(topic: str) -> str:
    low = str(topic or "").lower()
    for cat, keys in CATEGORY_KEYWORDS.items():
        if any(k in low for k in keys):
            return cat
    return "general"


def extract_json(text):
    if not text:
        raise ValueError("empty model response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract valid JSON from response. Raw response preview: {text[:240]}...")


def _word_count(text):
    return len([w for w in str(text or "").split() if w.strip()])


def _trim_words(text, max_words):
    words = [w.strip() for w in str(text or "").split() if w.strip()]
    return " ".join(words[:max_words])


def _short_phrase(text, max_words=5):
    cleaned = str(text or "").replace("#Shorts", "").strip(" .,!?:;")
    return _trim_words(cleaned, max_words) or "Watch this"


def get_local_fallback(topic):
    cat = _category(topic)
    if cat == "space":
        script = (
            f"These space facts sound fake, but they are real. First, space has no normal sound, because there is almost no air to carry vibrations. "
            "So every giant explosion out there would be silent to your ears. Second, one spoonful of neutron star material could weigh more than a mountain, because matter is crushed insanely tight. "
            "Third, some planets may rain glass sideways or hide diamond-like material deep inside. And the scary part is this: Earth is tiny compared with what the universe is hiding. Follow for more facts like this."
        )
        scenes = [
            {"text": "SPACE IS SILENT", "support": "Explosions need air for sound.", "search_query": "silent space stars"},
            {"text": "NEUTRON STAR WEIGHT", "support": "One spoon can weigh mountains.", "search_query": "neutron star space"},
            {"text": "WEIRD PLANET WEATHER", "support": "Some worlds are brutally strange.", "search_query": "alien planet storm"},
            {"text": "EARTH FEELS TINY", "support": "The scale is hard to imagine.", "search_query": "earth from space"},
            {"text": "FINAL FACT", "support": "The universe is still hiding more.", "search_query": "deep space galaxy"},
        ]
    elif cat == "ocean":
        script = (
            f"These ocean facts sound fake, but they are real. First, we have mapped Mars better than parts of Earth’s deep ocean. "
            "Second, some animals live in darkness so deep that sunlight never reaches them, yet they still hunt, glow, and survive. "
            "Third, pressure in the deepest trenches can crush normal machines like paper. That means the ocean is not just water; it is an alien world on our own planet. Follow for more wild facts."
        )
        scenes = [
            {"text": "DEEP OCEAN MYSTERY", "support": "Much of it is still unexplored.", "search_query": "deep ocean"},
            {"text": "GLOWING CREATURES", "support": "Life survives without sunlight.", "search_query": "bioluminescent ocean"},
            {"text": "CRUSHING PRESSURE", "support": "The deep can destroy machines.", "search_query": "ocean trench"},
            {"text": "ALIEN WORLD", "support": "It is here on Earth.", "search_query": "underwater cave"},
            {"text": "FINAL REVEAL", "support": "The ocean still has secrets.", "search_query": "dark ocean"},
        ]
    else:
        script = (
            f"These facts about {topic} sound fake, but they are real. First, the detail most people miss changes the whole story. "
            "Second, the scale is bigger than it looks, and that is why experts still study it. Third, one small comparison makes it easier to understand, but also much stranger. "
            "Fourth, the truth is usually more surprising than the myth. And the final fact is the one people remember, because it completely changes how you look at the topic. Follow for more quick facts."
        )
        scenes = [
            {"text": "SOUNDS FAKE", "support": "But this is real.", "search_query": topic[:50]},
            {"text": "HIDDEN DETAIL", "support": "Most people miss this part.", "search_query": topic[:50]},
            {"text": "BIGGER SCALE", "support": "It changes the story.", "search_query": topic[:50]},
            {"text": "STRANGE TRUTH", "support": "The myth is weaker.", "search_query": topic[:50]},
            {"text": "FINAL FACT", "support": "This is the one to remember.", "search_query": topic[:50]},
        ]
    return {
        "script": script,
        "title": f"{_short_phrase(topic, 7).title()} Facts That Sound Fake #Shorts",
        "description": f"Fast facts about {topic}. #Shorts #Facts #Education",
        "tags": ["Shorts", "Facts", "Education", cat.title()],
        "scenes": scenes,
    }


def validate_script_data(data):
    if not isinstance(data, dict):
        raise ValueError("script data must be a JSON object")
    required = ["title", "description", "tags", "scenes"]
    for req in required:
        if req not in data:
            raise ValueError(f"Missing required field in JSON: {req}")
    if not isinstance(data["scenes"], list) or not data["scenes"]:
        raise ValueError("'scenes' must be a non-empty list.")
    if not data.get("script"):
        data["script"] = " ".join(str(s.get("text", "")) for s in data["scenes"])
    return data


def normalize_script_data(data, topic):
    """Enforce length/scene rules so upload never becomes a 3-second Short."""
    try:
        data = validate_script_data(data)
    except Exception as exc:
        logger.warning(f"Invalid script JSON, using local fallback: {exc}")
        data = get_local_fallback(topic)

    wc = _word_count(data.get("script", ""))
    if wc < MIN_WORDS or wc > TARGET_MAX_WORDS + 25:
        logger.warning(
            "Script word count %s outside safe range (%s-%s); using deterministic fallback.",
            wc, MIN_WORDS, TARGET_MAX_WORDS + 25,
        )
        data = get_local_fallback(topic)

    scenes = list(data.get("scenes", []))[:SCENE_COUNT]
    fallback_scenes = get_local_fallback(topic)["scenes"]
    while len(scenes) < SCENE_COUNT:
        scenes.append(fallback_scenes[len(scenes) % len(fallback_scenes)])

    clean_scenes = []
    for idx, s in enumerate(scenes):
        text = str(s.get("text") or s.get("headline") or f"Fact {idx+1}").strip()
        support = str(s.get("support") or s.get("supporting_text") or "This sounds fake, but it is real.").strip()
        query = str(s.get("search_query") or s.get("image_prompt") or text).strip()
        clean_scenes.append({
            "text": _short_phrase(text, 6).upper(),
            "support": _trim_words(support, 10),
            "search_query": _trim_words(query, 5)[:70],
        })
    data["scenes"] = clean_scenes
    data["title"] = str(data.get("title") or f"{_short_phrase(topic, 7).title()} #Shorts")
    if "#shorts" not in data["title"].lower():
        data["title"] += " #Shorts"
    data["description"] = str(data.get("description") or f"Fast facts about {topic}. #Shorts")
    if "#shorts" not in data["description"].lower():
        data["description"] += " #Shorts"
    if not isinstance(data.get("tags"), list):
        data["tags"] = ["Shorts", "Facts", "Education"]
    return data


def generate_script_gemini(topic, scene_count, min_words, max_words):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set.")
    genai.configure(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(topic=topic, scene_count=scene_count, min_words=min_words, max_words=max_words)
    env_model = os.environ.get("GEMINI_MODEL")
    candidates = [env_model] if env_model else ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
    last_err = None
    for candidate in [c for c in candidates if c]:
        model_name = candidate if str(candidate).startswith("models/") else f"models/{candidate}"
        logger.info(f"Attempting generation with Gemini model: {model_name}")
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(response_mime_type="application/json"),
            )
            return validate_script_data(extract_json(response.text))
        except Exception as e:
            logger.warning(f"Gemini model {model_name} failed: {e}")
            last_err = e
    raise RuntimeError(f"All Gemini candidates failed. Last error: {last_err}")


def generate_script_fallback(topic, scene_count, min_words, max_words):
    logger.info("Using Pollinations/local fallback for script generation...")
    prompt = PROMPT_TEMPLATE.format(topic=topic, scene_count=scene_count, min_words=min_words, max_words=max_words)
    try:
        response = requests.post(
            "https://text.pollinations.ai/",
            json={
                "messages": [
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "jsonMode": True,
            },
            timeout=20,
        )
        response.raise_for_status()
        return validate_script_data(extract_json(response.text))
    except Exception as e:
        logger.warning(f"Pollinations fallback failed, using deterministic fallback: {e}")
        return get_local_fallback(topic)


def generate_script(topic):
    try:
        logger.info(f"Attempting to generate safe-length script for topic: '{topic}' using Gemini...")
        script_data = generate_script_gemini(topic, SCENE_COUNT, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
        logger.info("Gemini script generation succeeded.")
    except Exception as e:
        logger.warning(f"Gemini generation failed: {e}. Falling back.")
        script_data = generate_script_fallback(topic, SCENE_COUNT, TARGET_MIN_WORDS, TARGET_MAX_WORDS)
    script_data = normalize_script_data(script_data, topic)
    logger.info(f"Final script title: {script_data.get('title')}")
    logger.info(f"Narration word count: {_word_count(script_data.get('script', ''))}")
    logger.info(f"Total scenes generated: {len(script_data.get('scenes', []))}")
    return script_data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(generate_script("3 terrifying space facts that sound fake"), indent=2))
