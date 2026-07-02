import os
import time
import requests
import urllib.parse
import logging
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

def create_local_fallback_image(text, output_path, width=1080, height=1920):
    """Creates a simple fallback image with centered text if API fails."""
    logger.info(f"Creating local fallback image at {output_path}...")
    img = Image.new('RGB', (width, height), color=(20, 20, 30))
    draw = ImageDraw.Draw(img)

    # Try to use a basic font, fallback to default if missing
    try:
        font = ImageFont.truetype("Arial.ttf", 60)
    except IOError:
        font = ImageFont.load_default()

    # Wrap text roughly
    words = text.split()
    lines = []
    current_line = []
    for w in words:
        current_line.append(w)
        if len(' '.join(current_line)) > 25: # simplistic wrap
            lines.append(' '.join(current_line))
            current_line = []
    if current_line:
        lines.append(' '.join(current_line))

    y_text = height // 2 - (len(lines) * 80) // 2
    for line in lines:
        draw.text((width//2, y_text), line, font=font, fill=(200, 200, 220), anchor="mm")
        y_text += 80

    img.save(output_path)
    return output_path

def generate_images(beats, output_dir="assets/images"):
    """
    Downloads images from Pollinations AI for each beat in the script.
    Saves them sequentially as scene_0.jpg, scene_1.jpg, etc.
    """
    os.makedirs(output_dir, exist_ok=True)
    image_paths = []

    logger.info(f"Generating {len(beats)} images using Pollinations AI (Flux model)...")

    for i, beat in enumerate(beats):
        prompt = beat.get("image_prompt", "")
        if not prompt:
            logger.warning(f"Beat {i} missing 'image_prompt'. Using 'text' instead.")
            prompt = beat.get("text", f"Scene {i}")

        # Add constraints for Shorts (vertical format, no text)
        full_prompt = f"{prompt}, highly detailed, cinematic lighting, vertical 9:16 aspect ratio"
        encoded_prompt = urllib.parse.quote(full_prompt)

        # Pollinations image generation endpoint with parameters
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&seed={i}"

        output_path = os.path.join(output_dir, f"scene_{i}.jpg")

        success = False
        retries = int(os.environ.get("IMAGE_RETRIES", "2"))
        timeout = int(os.environ.get("IMAGE_TIMEOUT_SECONDS", "20"))

        for attempt in range(retries):
            try:
                logger.info(f"Downloading image {i+1}/{len(beats)}: {output_path} (Attempt {attempt+1}/{retries})")
                response = requests.get(url, timeout=timeout)
                response.raise_for_status()

                with open(output_path, 'wb') as f:
                    f.write(response.content)

                image_paths.append(output_path)
                success = True
                logger.info(f"Successfully saved {output_path}")
                break # Break out of retry loop on success
            except Exception as e:
                logger.error(f"Error downloading image {i}: {e}")
                if attempt < retries - 1:
                    time.sleep(2) # Small delay before retry

        if not success:
            logger.error(f"Failed to generate image from API for scene {i}. Using local fallback image.")
            try:
                # Use scene text for the fallback image
                scene_text = beat.get("text", f"Scene {i}")
                fallback_path = create_local_fallback_image(scene_text, output_path)
                image_paths.append(fallback_path)
            except Exception as fallback_e:
                logger.error(f"Local fallback image generation also failed: {fallback_e}")
                raise RuntimeError(f"Completely failed to generate image for scene {i}")

    return image_paths

if __name__ == "__main__":
    # Test script directly
    logging.basicConfig(level=logging.INFO)

    test_beats = [
        {"image_prompt": "A swirling black vortex over a starry backdrop, illuminated by a faint glow."},
        {"image_prompt": "A dark hole with a glowing gradient, atomic structures approaching the edge."}
    ]

    paths = generate_images(test_beats, output_dir="test_images")
    print(f"Generated images: {paths}")
