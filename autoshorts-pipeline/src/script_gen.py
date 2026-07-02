import os
import json
import logging
import requests
import google.generativeai as genai

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """
You are an expert YouTube Shorts creator. Create a script for a 45-60 second YouTube Short about "{topic}".
Your response MUST be a valid JSON object. Do not include any markdown formatting like ```json or anything else, just the raw JSON.
Ensure the JSON has the exact following structure:
{{
  "title": "Punchy YouTube Short Title",
  "description": "Engaging description with #Shorts and relevant hashtags",
  "tags": ["tag1", "tag2", "tag3"],
  "beats": [
    {{
      "text": "The exact narration for this beat. Strong 3-second hook for the first beat.",
      "image_prompt": "A highly detailed, visual description for an AI image generator to create the scene for this beat. No text in images."
    }}
  ]
}}
Generate between 5 to 8 beats total. Make the text punchy, engaging, and end with a soft CTA to follow/subscribe in the final beat.
"""

def generate_script_gemini(topic):
    """Generates the script using Gemini API."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')

    prompt = PROMPT_TEMPLATE.format(topic=topic)

    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
        )
    )

    return json.loads(response.text)

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

        text_resp = response.text.strip()

        # Clean up possible markdown code blocks
        if text_resp.startswith("```json"):
            text_resp = text_resp[7:]
        if text_resp.startswith("```"):
            text_resp = text_resp[3:]
        if text_resp.endswith("```"):
            text_resp = text_resp[:-3]

        return json.loads(text_resp.strip())
    except Exception as e:
        logger.error(f"Fallback generation failed: {e}")
        raise

def generate_script(topic):
    """Main function to generate a script with fallback."""
    try:
        logger.info(f"Attempting to generate script for topic: '{topic}' using Gemini...")
        return generate_script_gemini(topic)
    except Exception as e:
        logger.warning(f"Gemini generation failed: {e}. Falling back to Pollinations AI.")
        return generate_script_fallback(topic)

if __name__ == "__main__":
    # Test script if executed directly
    print("Testing generate_script with fallback...")
    # Using fallback explicitly for testing since API key won't be present
    try:
        res = generate_script_fallback("Fascinating facts about Black Holes")
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Failed: {e}")
