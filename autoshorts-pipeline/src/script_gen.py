import os
import json
import logging
import requests
import re
import google.generativeai as genai

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TARGET_SCENE_COUNT = 5
MIN_WORDS = int(os.environ.get("MIN_SCRIPT_WORDS", "95") or 95)
MAX_WORDS = int(os.environ.get("MAX_SCRIPT_WORDS", "130") or 130)

PROMPT_TEMPLATE = """
You are a retention-focused YouTube Shorts writer.
Create a short about: "{topic}".

Hard rules:
- Return STRICT JSON only. No markdown. No comments.
- Exactly {scene_count} scenes.
- Total narration must be between {min_words} and {max_words} words.
- Target spoken duration: 35 to 50 seconds.
- Use short spoken sentences.
- First sentence must be a strong 2-second hook.
- Avoid generic intros like "Did you know".
- If the topic says 3 facts, do not say 10 facts.
- Scene text must be short visual phrases, not paragraphs.
- Generate a 'search_query' for stock video/photo APIs instead of an image prompt.
- The 'search_query' MUST be very short (2-5 words), highly relevant, and visually descriptive.
- Final CTA only once, near the end.

JSON shape:
{{
  "script": "Full narration, {min_words}-{max_words} words.",
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


def extract_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r'(\{.*\})', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract valid JSON from response. Raw response preview: {text[:200]}...")


def _word_count(text):
    return len([w for w in str(text or "").split() if w.strip()])


def _trim_words(text, max_words):
    words = [w.strip() for w in str(text or "").split() if w.strip()]
    return " ".join(words[:max_words])


def _short_phrase(text, max_words=5):
    return _trim_words(str(text or "").replace("#Shorts", "").strip(" .,!?:;"), max_words) or "Watch this"


def _topic_label(topic):
    return _short_phrase(topic, 6).title()


def get_local_fallback(topic):
    logger.info("Using deterministic local fallback script with safe 35-50s length...")
    topic_clean = str(topic or "this topic").strip()
    script = (
        f"This sounds fake, but it is real. {topic_clean} has facts that feel impossible at first. "
        "Fact one: the scale is much bigger than your brain expects, and normal comparisons almost stop working. "
        "Fact two: one tiny detail can change the entire story, from danger to discovery. "
        "Fact three: scientists are still finding new clues, so the mystery is not finished yet. "
        "The strangest part is that every answer creates another question. Save this if you want more facts that sound unreal."
    )
    scenes = [
        {"text": "SOUNDS FAKE", "support": "But it is real.", "search_query": f"mysterious {topic_clean}"},
        {"text": "HUGE SCALE", "support": "The size feels impossible.", "search_query": f"huge scale {topic_clean}"},
        {"text": "TINY DETAIL", "support": "One detail changes everything.", "search_query": f"detail {topic_clean}"},
        {"text": "STILL UNSOLVED", "support": "Scientists keep finding clues.", "search_query": f"science mystery {topic_clean}"},
        {"text": "SAVE THIS", "support": "More strange facts soon.", "search_query": f"amazing {topic_clean}"},
    ]

    return {
        "script": script,
        "title": f"{_topic_label(topic_clean)} Facts That Sound Fake #Shorts",
        "description": f"Fast facts about {topic_clean}. #Shorts #Facts #Science",
        "tags": ["Shorts", "Facts", "Science", "Education"],
        "scenes": scenes,
    }


def validate_script_data(data):
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
    """Enforce production constraints instead of trusting the LLM."""
    data = validate_script_data(data)
    word_count = _word_count(data.get("script", ""))

    if word_count < MIN_WORDS:
        logger.warning(
            "LLM script too short (%s words). Using deterministic fallback to avoid 3-second/too-short Shorts.",
            word_count,
        )
        data = get_local_fallback(topic)
        word_count = _word_count(data.get("script", ""))

    if word_count > MAX_WORDS:
        logger.warning("LLM script exceeded word limit; trimming to keep under Shorts duration cap.")
        data["script"] = _trim_words(data.get("script", ""), MAX_WORDS)

    scenes = list(data.get("scenes", []))[:TARGET_SCENE_COUNT]
    fallback_scenes = get_local_fallback(topic)["scenes"]
    while len(scenes) < TARGET_SCENE_COUNT:
        scenes.append(fallback_scenes[len(scenes) % len(fallback_scenes)])

    clean_scenes = []
    for s in scenes:
        text = str(s.get("text", "")).strip() or "Watch this"
        support = str(s.get("support", "")).strip() or _trim_words(text, 8)
        query = str(s.get("search_query", text)).strip()[:60]
        clean_scenes.append({
            "text": _short_phrase(text, 6),
            "support": _trim_words(support, 10),
            "search_query": query or _short_phrase(text, 5),
        })
    data["scenes"] = clean_scenes
    return data


def generate_script_gemini(topic, scene_count, min_words, max_words):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set.")

    genai.configure(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(
        topic=topic,
        scene_count=scene_count,
        min_words=min_words,
        max_words=max_words,
    )

    env_model = os.environ.get("GEMINI_MODEL")
    candidates = [env_model] if env_model else ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
    last_err = None
    for candidate in candidates:
        if not candidate:
            continue
        model_name = candidate if str(candidate).startswith("models/") else f"models/{candidate}"
        logger.info(f"Attempting generation with Gemini model: {model_name}")
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(response_mime_type="application/json"),
            )
            data = extract_json(response.text)
            return validate_script_data(data)
        except Exception as e:
            logger.warning(f"Gemini model {model_name} failed: {e}")
            last_err = e
    raise RuntimeError(f"All Gemini candidates failed. Last error: {last_err}")


def generate_script_fallback(topic, scene_count, min_words, max_words):
    logger.info("Using Pollinations AI fallback for script generation...")
    prompt = PROMPT_TEMPLATE.format(topic=topic, scene_count=scene_count, min_words=min_words, max_words=max_words)
    try:
        url = "https://text.pollinations.ai/"
        payload = {
            "messages": [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "jsonMode": True,
        }
        response = requests.post(url, json=payload, timeout=25)
        response.raise_for_status()
        data = extract_json(response.text)
        return validate_script_data(data)
    except Exception as e:
        logger.error(f"Pollinations fallback failed: {e}")
        return get_local_fallback(topic)


def generate_script(topic):
    scene_count = TARGET_SCENE_COUNT
    try:
        logger.info(f"Attempting to generate script for topic: '{topic}' using Gemini...")
        script_data = generate_script_gemini(topic, scene_count, MIN_WORDS, MAX_WORDS)
        logger.info("Gemini script generation succeeded.")
    except Exception as e:
        logger.warning(f"Gemini generation failed: {e}. Falling back to Pollinations/local.")
        script_data = generate_script_fallback(topic, scene_count, MIN_WORDS, MAX_WORDS)

    script_data = normalize_script_data(script_data, topic)
    logger.info(f"Final script title: {script_data.get('title')}")
    logger.info(f"Narration word count: {_word_count(script_data.get('script', ''))}")
    logger.info(f"Total scenes generated: {len(script_data.get('scenes', []))}")
    return script_data


if __name__ == "__main__":
    print(json.dumps(generate_script("3 terrifying space facts that sound fake"), indent=2))
