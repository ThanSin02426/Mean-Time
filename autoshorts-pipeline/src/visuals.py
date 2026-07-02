import os
import time
import requests
import urllib.parse
import logging

logger = logging.getLogger(__name__)

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
        retries = 3

        for attempt in range(retries):
            try:
                logger.info(f"Downloading image {i+1}/{len(beats)}: {output_path} (Attempt {attempt+1}/{retries})")
                response = requests.get(url, timeout=30)
                response.raise_for_status()

                with open(output_path, 'wb') as f:
                    f.write(response.content)

                image_paths.append(output_path)
                success = True
                logger.info(f"Successfully saved {output_path}")
                break # Break out of retry loop on success
            except Exception as e:
                logger.error(f"Error downloading image {i}: {e}")
                time.sleep(2) # Small delay before retry

        if not success:
            logger.error(f"Failed to generate image for beat {i} after {retries} attempts.")
            raise RuntimeError(f"Failed to generate image for beat {i}")

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
