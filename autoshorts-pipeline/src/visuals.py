import os
import time
import random
import requests
import urllib.parse
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

def create_local_fallback_image(text, output_path, width=1080, height=1920):
    """Creates a professional fallback slide with centered text if API fails."""
    logger.info(f"Creating local fallback image at {output_path}...")

    # 1. Create a dark cinematic gradient background
    img = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(img)

    # Choose a random dark color theme
    colors = [
        ((15, 20, 30), (5, 5, 10)),   # Deep blue
        ((30, 15, 20), (10, 5, 5)),   # Deep red
        ((20, 30, 15), (5, 10, 5)),   # Deep green
        ((25, 10, 30), (10, 5, 15)),  # Deep purple
    ]
    color_top, color_bottom = random.choice(colors)

    for y in range(height):
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * (y / height))
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * (y / height))
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * (y / height))
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Add simple subtle noise texture
    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))

    # 2. Setup fonts
    try:
        # Try to use a nice bold font if available in system
        # DejaVuSans-Bold is often available on Ubuntu actions
        font_large = ImageFont.truetype("DejaVuSans-Bold.ttf", 90)
    except IOError:
        try:
            # Fallback to standard Arial
            font_large = ImageFont.truetype("Arial.ttf", 90)
        except IOError:
            font_large = ImageFont.load_default()

    # 3. Add decorative elements (simple lines/boxes)
    draw.rectangle([(width//2 - 200, 300), (width//2 + 200, 310)], fill=(255, 255, 255, 150))
    draw.rectangle([(width//2 - 200, height - 300), (width//2 + 200, height - 290)], fill=(255, 255, 255, 150))

    # 4. Wrap text beautifully
    words = text.split()
    lines = []
    current_line = []
    for w in words:
        current_line.append(w)
        if len(' '.join(current_line)) > 15: # tight wrap for large font
            lines.append(' '.join(current_line))
            current_line = []
    if current_line:
        lines.append(' '.join(current_line))

    y_text = height // 2 - (len(lines) * 110) // 2
    for line in lines:
        # Draw stroke/shadow for readability
        stroke_color = (0, 0, 0)
        stroke_width = 4
        x = width // 2
        for dx in range(-stroke_width, stroke_width + 1):
            for dy in range(-stroke_width, stroke_width + 1):
                draw.text((x + dx, y_text + dy), line, font=font_large, fill=stroke_color, anchor="mm")

        # Draw actual text
        draw.text((x, y_text), line, font=font_large, fill=(255, 255, 255), anchor="mm")
        y_text += 120

    img.save(output_path)
    return output_path

def generate_images(beats, output_dir="assets/images", image_provider_mode="hybrid"):
    """
    Downloads images from Pollinations AI for each beat in the script.
    Saves them sequentially as scene_0.jpg, scene_1.jpg, etc.
    """
    os.makedirs(output_dir, exist_ok=True)
    image_paths = []

    logger.info(f"Generating {len(beats)} images using mode: {image_provider_mode}")

    for i, beat in enumerate(beats):
        output_path = os.path.join(output_dir, f"scene_{i}.jpg")

        # If local_only, skip API entirely
        if image_provider_mode == "local_only":
            scene_text = beat.get("text", f"Scene {i}")
            fallback_path = create_local_fallback_image(scene_text, output_path)
            image_paths.append(fallback_path)
            continue

        prompt = beat.get("image_prompt", "")
        if not prompt:
            logger.warning(f"Beat {i} missing 'image_prompt'. Using 'text' instead.")
            prompt = beat.get("text", f"Scene {i}")

        # Add constraints for Shorts (vertical format, no text)
        full_prompt = f"{prompt}, highly detailed, cinematic lighting, vertical 9:16 aspect ratio"
        encoded_prompt = urllib.parse.quote(full_prompt)

        # Pollinations image generation endpoint with parameters
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&seed={i}"

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
