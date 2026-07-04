import os
import time
import random
import requests
import urllib.parse
import logging
import textwrap
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

W, H = 1080, 1920

THEMES = [
    {"top": (8, 12, 28), "bottom": (28, 6, 42), "accent": (255, 214, 80), "emoji": "✨"},
    {"top": (5, 18, 30), "bottom": (7, 45, 60), "accent": (91, 214, 255), "emoji": "🚀"},
    {"top": (22, 8, 18), "bottom": (55, 12, 28), "accent": (255, 96, 128), "emoji": "🔥"},
    {"top": (7, 20, 14), "bottom": (12, 46, 28), "accent": (142, 255, 167), "emoji": "⚡"},
]

EMOJI_BY_KEYWORD = {
    "space": "🚀", "planet": "🪐", "galaxy": "🌌", "star": "⭐", "black hole": "🕳️",
    "ocean": "🌊", "money": "💸", "brain": "🧠", "history": "🏛️", "ai": "🤖",
    "secret": "🔐", "terrifying": "😱", "fact": "🤯", "science": "🔬", "health": "💪",
}


def _font(size: int, bold: bool = True):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Arial Bold.ttf" if bold else "Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _pick_emoji(text: str, default: str = "🤯") -> str:
    low = text.lower()
    for key, emoji in EMOJI_BY_KEYWORD.items():
        if key in low:
            return emoji
    return default


def _shorten(text: str, max_words: int) -> str:
    words = [w.strip() for w in text.replace("\n", " ").split() if w.strip()]
    return " ".join(words[:max_words])


def _wrap_to_width(text: str, font, max_width: int, max_lines: int):
    words = text.split()
    lines, current = [], ""
    dummy = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(dummy)
    for word in words:
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = lines[-1].rstrip(".,;:") + "…"
    return lines


def _gradient_background(width=W, height=H, theme=None):
    theme = theme or random.choice(THEMES)
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    top, bottom = theme["top"], theme["bottom"]
    for y in range(height):
        ratio = y / float(height - 1)
        r = int(top[0] * (1 - ratio) + bottom[0] * ratio)
        g = int(top[1] * (1 - ratio) + bottom[1] * ratio)
        b = int(top[2] * (1 - ratio) + bottom[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # soft radial glow
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    accent = theme["accent"]
    for radius, alpha in [(520, 30), (360, 45), (220, 60)]:
        x, y = width // 2, int(height * 0.36)
        od.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*accent, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)

    # particles/stars
    draw = ImageDraw.Draw(img)
    random.seed(42)
    for _ in range(170):
        x = random.randint(30, width - 30)
        y = random.randint(40, height - 40)
        size = random.choice([1, 1, 2, 2, 3])
        alpha = random.randint(45, 150)
        draw.ellipse((x, y, x + size, y + size), fill=(255, 255, 255, alpha))

    return img


def create_local_fallback_image(text, output_path, width=W, height=H, scene_index=0):
    """Create a complete designed 9:16 slide that looks usable even with no AI image API."""
    logger.info(f"Creating designed local slide at {output_path}...")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    theme = THEMES[scene_index % len(THEMES)]
    img = _gradient_background(width, height, theme)
    draw = ImageDraw.Draw(img)

    accent = theme["accent"]
    emoji = _pick_emoji(text, theme.get("emoji", "🤯"))

    # Make scene text short and punchy for visual headline/support line.
    clean = " ".join(text.replace("\n", " ").split())
    headline = _shorten(clean, 5).upper()
    support = _shorten(clean, 12)
    if support.lower().startswith(headline.lower()):
        support = "Watch till the end."

    headline_font = _font(108, True)
    support_font = _font(48, False)
    emoji_font = _font(190, True)
    pill_font = _font(36, True)

    safe_x = 86
    max_text_width = width - safe_x * 2

    # top pill
    pill_text = f"FACT {scene_index + 1}" if scene_index else "WATCH THIS"
    pill_bbox = draw.textbbox((0, 0), pill_text, font=pill_font)
    pill_w = pill_bbox[2] - pill_bbox[0] + 64
    pill_h = 74
    pill_x = (width - pill_w) // 2
    pill_y = 168
    draw.rounded_rectangle((pill_x, pill_y, pill_x + pill_w, pill_y + pill_h), radius=36, fill=(*accent, 230))
    draw.text((width // 2, pill_y + pill_h // 2), pill_text, font=pill_font, fill=(10, 10, 15), anchor="mm")

    # emoji/icon
    draw.text((width // 2, 410), emoji, font=emoji_font, fill=(255, 255, 255), anchor="mm")

    # headline block
    headline_lines = _wrap_to_width(headline, headline_font, max_text_width, 3)
    y = 705
    line_gap = 118
    for line in headline_lines:
        # shadow/stroke
        draw.text((width // 2 + 4, y + 6), line, font=headline_font, fill=(0, 0, 0, 185), anchor="mm")
        draw.text((width // 2, y), line, font=headline_font, fill=(255, 255, 255, 255), anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0, 210))
        y += line_gap

    # accent underline
    draw.rounded_rectangle((width // 2 - 190, y + 8, width // 2 + 190, y + 24), radius=8, fill=(*accent, 245))

    # support card
    card_y = 1260
    card_h = 260
    draw.rounded_rectangle((72, card_y, width - 72, card_y + card_h), radius=46, fill=(0, 0, 0, 145), outline=(*accent, 160), width=3)
    support_lines = _wrap_to_width(support, support_font, max_text_width - 90, 3)
    sy = card_y + 82
    for line in support_lines:
        draw.text((width // 2, sy), line, font=support_font, fill=(235, 240, 255, 255), anchor="mm")
        sy += 60

    # bottom CTA/safe area marker
    cta = "FOLLOW FOR MORE"
    cta_font = _font(34, True)
    draw.text((width // 2, height - 210), cta, font=cta_font, fill=(*accent, 230), anchor="mm")

    img.convert("RGB").save(output_path, quality=95)
    return output_path


def generate_images(beats, output_dir="assets/images", image_provider_mode="local_only", quality_mode="preview"):
    """
    Generate scene images. In local_only mode this is fully deterministic and never touches the network.
    """
    os.makedirs(output_dir, exist_ok=True)
    image_paths = []
    logger.info(f"Generating {len(beats)} images using mode: {image_provider_mode}")

    for i, beat in enumerate(beats):
        output_path = os.path.join(output_dir, f"scene_{i}.jpg")
        scene_text = beat.get("text") or beat.get("image_prompt") or f"Scene {i + 1}"

        if image_provider_mode == "local_only":
            image_paths.append(create_local_fallback_image(scene_text, output_path, scene_index=i))
            continue

        prompt = (beat.get("image_prompt") or scene_text).strip()
        prompt = prompt[:300]
        full_prompt = f"{prompt}, cinematic, vertical 9:16, high contrast, no text, clean composition"
        encoded_prompt = urllib.parse.quote(full_prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&seed={i}"

        success = False
        retries = int(os.environ.get("IMAGE_RETRIES", "1" if quality_mode == "preview" else "2"))
        timeout = int(os.environ.get("IMAGE_TIMEOUT_SECONDS", "12" if quality_mode == "preview" else "20"))

        for attempt in range(retries):
            try:
                logger.info(f"Downloading image {i+1}/{len(beats)}: {output_path} (Attempt {attempt+1}/{retries})")
                response = requests.get(url, timeout=timeout)
                response.raise_for_status()
                with open(output_path, "wb") as f:
                    f.write(response.content)
                image_paths.append(output_path)
                success = True
                break
            except Exception as e:
                logger.warning(f"Image API failed for scene {i+1}: {e}")
                if attempt < retries - 1:
                    time.sleep(1)

        if not success:
            if image_provider_mode == "pollinations":
                raise RuntimeError(f"Image API failed for scene {i+1} and pollinations-only mode is enabled")
            logger.warning(f"Using designed local slide for scene {i+1}")
            image_paths.append(create_local_fallback_image(scene_text, output_path, scene_index=i))

    return image_paths
