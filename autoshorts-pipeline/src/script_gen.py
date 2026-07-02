import os
import json
import logging
import requests
import re
import google.generativeai as genai

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """
You are an expert YouTube Shorts creator. Create a script for a 45-60 second YouTube Short about "{topic}".
Your response MUST be a valid JSON object. Do not include any markdown formatting like ```json or anything else, just the raw JSON.
Ensure the JSON has the exact following structure:
{{
  "script": "The full exact narration text for the entire short.",
  "title": "Punchy YouTube Short Title",
  "description": "Engaging description with #Shorts and relevant hashtags",
  "tags": ["tag1", "tag2", "tag3"],
  "scenes": [
    {{
      "text": "The exact narration for this beat. Strong 3-second hook for the first beat.",
      "image_prompt": "A highly detailed, visual description for an AI image generator to create the scene for this beat. No text in images."
    }}
  ]
}}
Generate between 5 to 8 scenes total. Make the text punchy, engaging, and end with a soft CTA to follow/subscribe in the final scene.
"""

def extract_json(text):
    """Extracts and parses JSON from a string, handling markdown fences."""
    try:
        # First, try to parse the entire text as JSON
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # If that fails, try to find JSON block using regex
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try just extracting anything between the first { and last }
    match = re.search(r'(\{.*\})', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract valid JSON from response. Raw response preview: {text[:200]}...")

def validate_script_data(data):
    """Ensures the extracted JSON has the required fields."""
    required = ["title", "description", "tags", "scenes"]
    for req in required:
        if req not in data:
            raise ValueError(f"Missing required field in JSON: {req}")
    if not isinstance(data["scenes"], list) or not data["scenes"]:
        raise ValueError("'scenes' must be a non-empty list.")
    return data

def get_best_gemini_model():
    """Dynamically finds a working Gemini model."""
    # 1. Check environment variable
    env_model = os.environ.get("GEMINI_MODEL")
    if env_model:
        logger.info(f"Using GEMINI_MODEL from environment: {env_model}")
        return env_model

    # 2. Dynamically list models and find a Flash model supporting generateContent
    try:
        models = [m for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]

        # Prioritize flash models
        flash_models = [m.name for m in models if 'flash' in m.name.lower()]
        if flash_models:
            # Sort to prefer newer versions if they follow naming conventions
            flash_models.sort(reverse=True)
            logger.info(f"Auto-detected Gemini Flash model: {flash_models[0]}")
            return flash_models[0]

        if models:
            logger.info(f"Auto-detected fallback Gemini model: {models[0].name}")
            return models[0].name
    except Exception as e:
        logger.warning(f"Failed to auto-detect Gemini models: {e}")

    # 3. Fallback candidates if listing fails
    return None

def generate_script_gemini(topic):
    """Generates the script using Gemini API."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set.")

    genai.configure(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(topic=topic)

    # Get best model or fallback to candidates
    best_model = get_best_gemini_model()
    candidates = [best_model] if best_model else ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]

    last_err = None
    for candidate in candidates:
        if not candidate: continue
        # Format name properly if it doesn't have models/ prefix
        model_name = candidate if candidate.startswith('models/') else f"models/{candidate}"
        logger.info(f"Attempting generation with Gemini model: {model_name}")

        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                )
            )

            data = extract_json(response.text)
            return validate_script_data(data)
        except Exception as e:
            logger.warning(f"Gemini model {model_name} failed: {e}")
            last_err = e

    raise RuntimeError(f"All Gemini candidates failed. Last error: {last_err}")

def get_local_fallback(topic):
    """Returns a deterministic, hardcoded fallback script."""
    logger.info("Using local python deterministic fallback...")
    return {
        "script": f"Did you know these crazy facts about {topic}? Fact one: it's incredibly fascinating. Fact two: scientists are still baffled. Fact three: you probably interact with it every day. Like and subscribe for more amazing facts!",
        "title": f"Mind-Blowing Facts About {topic}! #Shorts",
        "description": f"Discover the most amazing secrets about {topic} in under 60 seconds! 🤯 #Shorts #Facts #Trending",
        "tags": ["Shorts", "Facts", "Education", "Trending"],
        "scenes": [
            {
                "text": f"Did you know these crazy facts about {topic}?",
                "image_prompt": f"A highly detailed, cinematic 3D render of {topic}, mysterious lighting, 8k resolution, vertical orientation"
            },
            {
                "text": "Fact one: it's incredibly fascinating.",
                "image_prompt": f"A close-up, macro photography shot related to {topic}, vibrant colors, glowing details, dramatic shadow"
            },
            {
                "text": "Fact two: scientists are still baffled.",
                "image_prompt": f"A futuristic laboratory studying {topic}, glowing holographic displays, neon lights, high tech, highly detailed"
            },
            {
                "text": "Fact three: you probably interact with it every day.",
                "image_prompt": f"An everyday life scene subtly incorporating {topic}, warm sunlight, beautiful composition, realistic rendering"
            },
            {
                "text": "Like and subscribe for more amazing facts!",
                "image_prompt": "A glowing like and subscribe button floating in a starry galaxy, magical atmosphere, bright neon colors, 3d icon style"
            }
        ]
    }

def generate_script_fallback(topic):
    """Generates the script using Pollinations AI free text endpoint as fallback."""
    logger.info("Using Pollinations AI fallback for script generation...")
    prompt = PROMPT_TEMPLATE.format(topic=topic)

    try:
        # Pollinations supports POST with OpenAI-like payload for better prompt handling
        url = "https://text.pollinations.ai/"
        payload = {
            "messages": [
                {"role": "system", "content": "You are a helpful JSON-generating AI."},
                {"role": "user", "content": prompt}
            ],
            "jsonMode": True
        }
        response = requests.post(url, json=payload, timeout=45)
        response.raise_for_status()

        data = extract_json(response.text)
        return validate_script_data(data)
    except Exception as e:
        logger.error(f"Pollinations Fallback generation failed: {e}")
        return get_local_fallback(topic)

def generate_script(topic):
    """Main function to generate a script with multiple layers of fallback."""
    script_data = None

    try:
        logger.info(f"Attempting to generate script for topic: '{topic}' using Gemini...")
        script_data = generate_script_gemini(topic)
        logger.info("Gemini script generation succeeded.")
    except Exception as e:
        logger.warning(f"Gemini generation failed: {e}. Falling back to Pollinations AI.")
        script_data = generate_script_fallback(topic)

    if not script_data:
        # This acts as the absolute final net if the earlier functions somehow returned None
        script_data = get_local_fallback(topic)

    logger.info(f"Final script title: {script_data.get('title')}")
    logger.info(f"Total scenes generated: {len(script_data.get('scenes', []))}")
    return script_data

if __name__ == "__main__":
    # Test script if executed directly
    print("Testing generate_script with fallback...")
    # Using fallback explicitly for testing since API key won't be present
    try:
        res = generate_script_fallback("Fascinating facts about Black Holes")
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Failed: {e}")
