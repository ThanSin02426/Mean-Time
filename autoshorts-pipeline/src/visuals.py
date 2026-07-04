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


def search_pexels(query):
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        return None

    headers = {"Authorization": api_key}

    # Try video first
    try:
        url = f"https://api.pexels.com/videos/search?query={urllib.parse.quote(query)}&orientation=portrait&per_page=5"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("videos"):
                video = random.choice(data["videos"])
                video_files = video.get("video_files", [])
                # Filter for HD vertical
                hd_files = [f for f in video_files if f.get("width", 0) >= 720 and f.get("link")]
                if hd_files:
                    best_file = sorted(hd_files, key=lambda x: x.get("width", 0), reverse=True)[0]
                    return {"url": best_file["link"], "type": "video", "source": "pexels", "author": video.get("user", {}).get("name")}
    except Exception as e:
        logger.warning(f"Pexels video search failed: {e}")

    # Try photo
    try:
        url = f"https://api.pexels.com/v1/search?query={urllib.parse.quote(query)}&orientation=portrait&per_page=5"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("photos"):
                photo = random.choice(data["photos"])
                return {"url": photo["src"]["large2x"], "type": "image", "source": "pexels", "author": photo.get("photographer")}
    except Exception as e:
        logger.warning(f"Pexels photo search failed: {e}")

    return None

def search_pixabay(query):
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return None

    # Try video first
    try:
        url = f"https://pixabay.com/api/videos/?key={api_key}&q={urllib.parse.quote(query)}&safesearch=true&per_page=5"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("hits"):
                video = random.choice(data["hits"])
                videos = video.get("videos", {})
                if videos.get("large", {}).get("url"):
                    return {"url": videos["large"]["url"], "type": "video", "source": "pixabay", "author": video.get("user")}
                elif videos.get("medium", {}).get("url"):
                    return {"url": videos["medium"]["url"], "type": "video", "source": "pixabay", "author": video.get("user")}
    except Exception as e:
        logger.warning(f"Pixabay video search failed: {e}")

    # Try photo
    try:
        url = f"https://pixabay.com/api/?key={api_key}&q={urllib.parse.quote(query)}&image_type=photo&orientation=vertical&safesearch=true&per_page=5"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("hits"):
                photo = random.choice(data["hits"])
                return {"url": photo["largeImageURL"], "type": "image", "source": "pixabay", "author": photo.get("user")}
    except Exception as e:
        logger.warning(f"Pixabay photo search failed: {e}")

    return None

def search_nasa(query):
    try:
        url = f"https://images-api.nasa.gov/search?q={urllib.parse.quote(query)}&media_type=image"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("collection", {}).get("items", [])
            if items:
                # Get links
                links = items[0].get("links", [])
                for link in links:
                    if link.get("render") == "image":
                        return {"url": link["href"], "type": "image", "source": "nasa", "author": "NASA"}
    except Exception as e:
        logger.warning(f"NASA search failed: {e}")
    return None

def download_media(url, output_path):
    try:
        resp = requests.get(url, stream=True, timeout=20)
        resp.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        logger.error(f"Failed to download media from {url}: {e}")
        return False

def generate_visuals(scenes, output_dir="assets/visuals", topic=""):
    """
    Downloads stock media or generates fallback slides for each scene.
    Returns a list of dictionaries with 'path' and 'metadata'.
    """
    os.makedirs(output_dir, exist_ok=True)
    visual_data = []

    is_space_topic = any(kw in topic.lower() for kw in ["space", "planet", "galaxy", "black hole", "nasa", "moon", "mars", "asteroid", "universe", "star", "solar system"])

    for i, scene in enumerate(scenes):
        query = scene.get("search_query", scene.get("text", f"scene {i}"))
        logger.info(f"Fetching visual for scene {i}: '{query}'")

        media_info = None

        # 1. Pexels First
        media_info = search_pexels(query)

        # 2. Pixabay Fallback
        if not media_info:
            media_info = search_pixabay(query)

        # 3. NASA Fallback
        if not media_info and is_space_topic:
            media_info = search_nasa(query)

        # 4. Download and configure
        if media_info:
            ext = ".mp4" if media_info["type"] == "video" else ".jpg"
            output_path = os.path.join(output_dir, f"scene_{i}{ext}")

            logger.info(f"Downloading from {media_info['source']}...")
            if download_media(media_info["url"], output_path):
                visual_data.append({
                    "path": output_path,
                    "type": media_info["type"],
                    "source": media_info["source"],
                    "author": media_info.get("author", "Unknown")
                })
                continue

        # 5. Local Designed Fallback
        logger.warning(f"No stock media found for scene {i}. Using local designed fallback.")
        output_path = os.path.join(output_dir, f"scene_{i}.jpg")
        fallback_path = create_local_fallback_image(scene.get("text", ""), output_path, scene_index=i)
        visual_data.append({
            "path": fallback_path,
            "type": "image",
            "source": "local_fallback",
            "author": "AutoShorts"
        })

    return visual_data
