import os
import math
import logging
import random
from typing import List, Dict

from moviepy.editor import (
    AudioFileClip, ImageClip, CompositeVideoClip, CompositeAudioClip,
    concatenate_audioclips
)
from PIL import Image, ImageDraw, ImageFont

# MoviePy 1.0.3 still references PIL.Image.ANTIALIAS, removed in Pillow 10.
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

logger = logging.getLogger(__name__)

W, H = 1080, 1920


def _font(size: int, bold: bool = True):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def resize_image_for_video(image_path, target_size=(W, H)):
    """Resize/crop to vertical Shorts format."""
    img = Image.open(image_path).convert("RGB")
    img_ratio = img.width / img.height
    target_ratio = target_size[0] / target_size[1]

    if img_ratio > target_ratio:
        new_width = int(img.height * target_ratio)
        offset = (img.width - new_width) // 2
        img = img.crop((offset, 0, offset + new_width, img.height))
    else:
        new_height = int(img.width / target_ratio)
        offset = (img.height - new_height) // 2
        img = img.crop((0, offset, img.width, offset + new_height))

    resample = getattr(Image, "Resampling", Image).LANCZOS
    img = img.resize(target_size, resample)
    temp_path = f"{image_path}_resized.jpg"
    img.save(temp_path, quality=95)
    return temp_path


def apply_ken_burns(image_clip, scene_index=0):
    """Stable deterministic slow zoom/pan."""
    import numpy as np

    directions = [(-1, -1), (1, -1), (-1, 1), (1, 1), (0, 0)]
    pan_x, pan_y = directions[scene_index % len(directions)]

    def resize_frame(get_frame, t):
        frame = get_frame(t)
        duration = max(image_clip.duration, 0.001)
        progress = min(max(t / duration, 0.0), 1.0)
        zoom = 1.03 + 0.07 * progress

        h, w = frame.shape[:2]
        new_w, new_h = int(w * zoom), int(h * zoom)
        img = Image.fromarray(frame)
        resample = getattr(Image, "Resampling", Image).LANCZOS
        resized = img.resize((new_w, new_h), resample)

        max_x = new_w - w
        max_y = new_h - h
        center_x = max_x // 2
        center_y = max_y // 2
        left = int(center_x + pan_x * max_x * 0.18 * (progress - 0.5))
        top = int(center_y + pan_y * max_y * 0.18 * (progress - 0.5))
        left = max(0, min(left, max_x))
        top = max(0, min(top, max_y))
        return np.array(resized.crop((left, top, left + w, top + h)))

    return image_clip.fl(resize_frame)


def _text_size(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font, stroke_width=0)
    return box[2] - box[0], box[3] - box[1]


def _wrap_words(words: List[str], font, max_width: int, max_lines: int = 2):
    dummy = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(dummy)
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        tw, _ = _text_size(draw, trial, font)
        if tw <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines[:max_lines]


def _make_caption_png(chunk: Dict, out_path: str, active_word_index: int = -1):
    """Create a transparent caption overlay PNG using PIL. No ImageMagick/TextClip dependency."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font = _font(84, True)
    small_font = _font(76, True)
    words = [w["word"].strip() for w in chunk["words"] if w.get("word", "").strip()]
    words = [w for w in words if w]
    if not words:
        img.save(out_path)
        return out_path

    # Use a smaller font if needed.
    lines = _wrap_words(words, font, 900, 2)
    if len(" ".join(lines)) < len(" ".join(words)) - 3:
        font = small_font
        lines = _wrap_words(words, font, 920, 2)

    line_h = 100
    pad_x, pad_y = 54, 34
    block_h = len(lines) * line_h + pad_y * 2
    y0 = 1335  # safe bottom third
    max_line_w = max((_text_size(draw, line, font)[0] for line in lines), default=0)
    box_w = min(W - 110, max_line_w + pad_x * 2)
    x0 = (W - box_w) // 2

    draw.rounded_rectangle((x0, y0, x0 + box_w, y0 + block_h), radius=42, fill=(0, 0, 0, 180), outline=(255, 215, 70, 160), width=3)

    # Draw by word so we can highlight current word approximately.
    word_counter = 0
    y = y0 + pad_y + 45
    for line in lines:
        line_words = line.split()
        total_w = sum(_text_size(draw, w, font)[0] for w in line_words) + (len(line_words) - 1) * 24
        x = (W - total_w) // 2
        for word in line_words:
            is_active = (word_counter == active_word_index) if active_word_index >= 0 else False
            fill = (255, 221, 66, 255) if is_active else (255, 255, 255, 255)
            draw.text((x, y), word, font=font, fill=fill, anchor="lm", stroke_width=6, stroke_fill=(0, 0, 0, 245))
            x += _text_size(draw, word, font)[0] + 24
            word_counter += 1
        y += line_h

    img.save(out_path)
    return out_path


def _group_caption_chunks(word_timings: List[Dict], max_words=4, total_duration: float = None):
    """
    Build phrase captions from ACTUAL STT/Whisper timings.

    Sync rule:
    - chunk start = first spoken word start, with tiny early lead
    - chunk end = next chunk start, so captions remain present through pauses
    - final chunk extends to the final video/audio duration

    This keeps subtitles visible through the whole short while still changing only
    when the spoken audio reaches the next phrase.
    """
    cleaned = []
    for w in word_timings:
        word = str(w.get("word", "")).strip()
        if not word:
            continue
        try:
            start = float(w.get("start", 0.0))
            end = float(w.get("end", start + 0.25))
        except Exception:
            continue
        if end <= start:
            end = start + 0.18
        if total_duration is not None:
            start = max(0.0, min(start, float(total_duration)))
            end = max(start + 0.08, min(end, float(total_duration)))
        cleaned.append({"word": word, "start": start, "end": end})

    if not cleaned:
        return []

    cleaned.sort(key=lambda x: (x["start"], x["end"]))
    raw_chunks, cur = [], []
    for i, w in enumerate(cleaned):
        cur.append(w)
        is_last = i == len(cleaned) - 1
        next_start = cleaned[i + 1]["start"] if not is_last else None
        gap_after_word = (next_start - w["end"]) if next_start is not None else 0.0
        punctuation_break = w["word"].endswith((".", "?", "!", ":", ";"))
        should_break = len(cur) >= max_words or punctuation_break or gap_after_word > 0.9 or is_last
        if should_break:
            raw_chunks.append({"words": cur[:], "start": cur[0]["start"], "natural_end": cur[-1]["end"]})
            cur = []

    if not raw_chunks:
        return []

    td = float(total_duration) if total_duration is not None else None
    for i, chunk in enumerate(raw_chunks):
        # Small lead makes reading feel natural, but keep order anchored to STT audio.
        if i == 0:
            chunk["start"] = 0.0
        else:
            chunk["start"] = max(0.0, chunk["start"] - 0.04)

        if i < len(raw_chunks) - 1:
            next_start = max(chunk["start"] + 0.55, raw_chunks[i + 1]["start"] - 0.04)
            chunk["end"] = next_start
        else:
            chunk["end"] = max(chunk["natural_end"] + 0.35, td if td is not None else chunk["natural_end"] + 0.35)

        if td is not None:
            chunk["start"] = min(chunk["start"], max(0.0, td - 0.35))
            chunk["end"] = min(max(chunk["end"], chunk["start"] + 0.45), td)
        if chunk["end"] <= chunk["start"]:
            chunk["end"] = chunk["start"] + 0.45

    return raw_chunks


def _caption_overlay_clips(word_timings: List[Dict], total_duration: float, output_dir="output/captions"):
    chunks = _group_caption_chunks(word_timings, max_words=4, total_duration=total_duration)
    clips = []
    os.makedirs(output_dir, exist_ok=True)

    for idx, chunk in enumerate(chunks):
        png_path = os.path.join(output_dir, f"caption_{idx:03d}.png")
        # Phrase captions are synced by chunk timing. Use stable highlight on the first word.
        _make_caption_png(chunk, png_path, active_word_index=0)
        start = max(0.0, float(chunk["start"]))
        end = min(float(chunk["end"]), float(total_duration))
        duration = max(0.35, end - start)
        clips.append(ImageClip(png_path).set_start(start).set_duration(duration))

        if idx < 5:
            sample_path = os.path.join("output", f"caption_sample_{idx:02d}.png")
            try:
                _make_caption_png(chunk, sample_path, active_word_index=0)
            except Exception as exc:
                logger.warning(f"Could not save caption sample {idx}: {exc}")

    if chunks:
        max_gap = 0.0
        last_end = 0.0
        for chunk in chunks:
            max_gap = max(max_gap, max(0.0, float(chunk["start"]) - last_end))
            last_end = max(last_end, float(chunk["end"]))
        tail_gap = max(0.0, float(total_duration) - last_end)
        max_gap = max(max_gap, tail_gap)
        logger.info(f"Caption coverage: {len(chunks)} chunks, max gap {max_gap:.2f}s, end {last_end:.2f}/{total_duration:.2f}s")
        if max_gap > 0.75:
            raise RuntimeError(f"Caption coverage failed: max caption gap is {max_gap:.2f}s")

    logger.info(f"Created {len(clips)} caption overlay clips from {len(word_timings)} synced word timings")
    return clips, chunks


def fetch_pixabay_music():
    """Searches Pixabay for a random background track and downloads it."""
    import requests
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return None

    try:
        url = f"https://pixabay.com/api/audio/?key={api_key}&q=cinematic&per_page=10"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("hits"):
                track = random.choice(data["hits"])
                download_url = track.get("audio")
                if download_url:
                    output_path = "output/temp_bg_music.mp3"
                    os.makedirs("output", exist_ok=True)
                    audio_resp = requests.get(download_url, timeout=20)
                    with open(output_path, "wb") as f:
                        f.write(audio_resp.content)
                    return {"path": output_path, "source": "pixabay", "author": track.get("user")}
    except Exception as e:
        logger.warning(f"Pixabay music search failed: {e}")
    return None

def _add_music(final_audio, total_duration, music_dir):
    music_path = None
    music_meta = None

    # 1. Try local music
    if os.path.exists(music_dir) and os.path.isdir(music_dir):
        music_files = [f for f in os.listdir(music_dir) if f.lower().endswith((".mp3", ".wav", ".m4a"))]
        if music_files:
            music_file = random.choice(music_files)
            music_path = os.path.join(music_dir, music_file)
            music_meta = {"source": "local", "file": music_file}

    # 2. Try Pixabay music fallback
    if not music_path:
        pixabay_music = fetch_pixabay_music()
        if pixabay_music:
            music_path = pixabay_music["path"]
            music_meta = {"source": pixabay_music["source"], "author": pixabay_music["author"]}

    # 3. Mix
    if music_path:
        logger.info(f"Adding background music: {music_path}")
        try:
            bg = AudioFileClip(music_path)
            if bg.duration < total_duration:
                repeats = int(total_duration // bg.duration) + 1
                bg = concatenate_audioclips([bg] * repeats)
            bg = bg.subclip(0, total_duration).volumex(0.10)
            return CompositeAudioClip([final_audio, bg]), music_meta
        except Exception as e:
            logger.warning(f"Failed to mix background music: {e}")

    logger.info("No music available. Proceeding with narration-only audio.")
    return final_audio, None

def crop_video_to_vertical(clip):
    """Crops a landscape video clip to 9:16 vertical."""
    w, h = clip.size
    target_ratio = 1080 / 1920.0
    current_ratio = w / h

    if current_ratio > target_ratio:
        # Crop sides
        new_w = int(h * target_ratio)
        x_center = w / 2
        clip = clip.crop(x1=x_center - new_w/2, y1=0, x2=x_center + new_w/2, y2=h)
    else:
        # Crop top/bottom
        new_h = int(w / target_ratio)
        y_center = h / 2
        clip = clip.crop(x1=0, y1=y_center - new_h/2, x2=w, y2=y_center + new_h/2)

    return clip.resize((1080, 1920))

def assemble_video(audio_path, word_timings, visual_data, output_path, music_dir="assets/music"):
    logger.info("Starting deterministic video assembly...")
    if not visual_data:
        raise ValueError("No visuals provided for assembly.")
    if not word_timings:
        raise RuntimeError("No captions/word timings found; refusing to create uncaptained short.")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    audio = AudioFileClip(audio_path)
    source_duration = float(audio.duration)
    max_duration = 58.0
    total_duration = min(source_duration, max_duration)
    if source_duration > max_duration:
        logger.warning(f"Audio/video duration {source_duration:.2f}s exceeds {max_duration:.0f}s; trimming final output.")
        audio = audio.subclip(0, total_duration)
        word_timings = [w for w in word_timings if float(w.get("start", 0)) <= total_duration]

    padding = 0.25
    n = len(visual_data)
    scene_duration = (total_duration + (n - 1) * padding) / n
    logger.info(f"Video duration: {total_duration:.2f}s | Scenes: {n} | Scene duration: {scene_duration:.2f}s | Crossfade: {padding}s")

    visual_clips = []
    temp_resized = []

    for i, item in enumerate(visual_data):
        path = item["path"]
        mtype = item["type"]

        if mtype == "video":
            from moviepy.editor import VideoFileClip
            clip = VideoFileClip(path).without_audio()
            if clip.duration < scene_duration:
                # Import required here since it was missing at top level
                from moviepy.editor import concatenate_videoclips
                repeats = int(scene_duration // clip.duration) + 1
                clip = concatenate_videoclips([clip] * repeats)
            clip = clip.subclip(0, scene_duration)
            clip = crop_video_to_vertical(clip)
        else:
            resized_path = resize_image_for_video(path)
            temp_resized.append(resized_path)
            clip = ImageClip(resized_path).set_duration(scene_duration)
            clip = apply_ken_burns(clip, scene_index=i)

        if i > 0:
            clip = clip.set_start(visual_clips[-1].end - padding).crossfadein(padding)
        visual_clips.append(clip)

    base = CompositeVideoClip(visual_clips, size=(W, H)).set_duration(total_duration)
    caption_clips, chunks = _caption_overlay_clips(word_timings, total_duration=total_duration)
    if not caption_clips:
        raise RuntimeError("Caption overlay generation produced zero clips.")

    final_video = CompositeVideoClip([base] + caption_clips, size=(W, H)).set_duration(total_duration)
    final_audio, music_meta = _add_music(audio, total_duration, music_dir)
    final_video = final_video.set_audio(final_audio)

    logger.info(f"Exporting final video to {output_path}")
    final_video.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="6500k",
        preset="medium",
        threads=4,
    )

    for p in temp_resized:
        try:
            os.remove(p)
        except Exception:
            pass

    # Save Metadata JSON
    import json
    metadata = {
        "visuals": [{"source": v["source"], "author": v["author"]} for v in visual_data],
        "music": music_meta
    }
    with open("output/metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(f"Video assembly complete: {output_path}")
    return output_path
