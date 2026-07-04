import os
import random
import logging
from moviepy.editor import (
    AudioFileClip, ImageClip, TextClip, VideoClip, CompositeVideoClip, CompositeAudioClip,
    concatenate_videoclips, concatenate_audioclips, vfx
)
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Note: We need ImageMagick installed for TextClip to work properly on some systems.
# Setting ImageMagick binary path if needed could go here, but moviepy usually handles it in standard envs.

def resize_image_for_video(image_path, target_size=(1080, 1920)):
    """Ensure image is properly sized for vertical video."""
    img = Image.open(image_path)
    # Resize and crop to fill the target size (1080x1920)
    img_ratio = img.width / img.height
    target_ratio = target_size[0] / target_size[1]

    if img_ratio > target_ratio:
        # Image is wider, crop sides
        new_width = int(img.height * target_ratio)
        offset = (img.width - new_width) // 2
        img = img.crop((offset, 0, offset + new_width, img.height))
    else:
        # Image is taller, crop top/bottom
        new_height = int(img.width / target_ratio)
        offset = (img.height - new_height) // 2
        img = img.crop((0, offset, img.width, offset + new_height))

    try:
        resample_filter = Image.Resampling.LANCZOS
    except AttributeError:
        # Fallback for older Pillow versions
        resample_filter = Image.ANTIALIAS

    img = img.resize(target_size, resample_filter)
    temp_path = f"{image_path}_resized.jpg"
    img.save(temp_path)
    return temp_path

def apply_ken_burns(image_clip):
    """
    Applies a slow pan/zoom effect to an ImageClip.
    """
    import numpy as np

    # Randomize zoom direction per clip
    zoom_in = random.choice([True, False])
    pan_x = random.choice([-1, 0, 1])
    pan_y = random.choice([-1, 0, 1])

    def resize_frame(get_frame, t):
        frame = get_frame(t)
        progress = t / image_clip.duration

        # Calculate zoom factor (zoom in from 1x to 1.1x, or zoom out from 1.1x to 1x)
        if zoom_in:
            zoom = 1 + (0.1 * progress)
        else:
            zoom = 1.1 - (0.1 * progress)

        h, w = frame.shape[:2]
        new_h, new_w = int(h * zoom), int(w * zoom)

        # Center crop to original size
        img = Image.fromarray(frame)
        try:
            resample_filter = Image.Resampling.LANCZOS
        except AttributeError:
            resample_filter = Image.ANTIALIAS

        img_resized = img.resize((new_w, new_h), resample_filter)

        # Calculate panning offset
        max_pan_x = (new_w - w) // 2
        max_pan_y = (new_h - h) // 2

        # If panning right, start from left and move right
        offset_x = int(max_pan_x + (pan_x * max_pan_x * progress * 0.5))
        offset_y = int(max_pan_y + (pan_y * max_pan_y * progress * 0.5))

        # Clamp to bounds
        left = max(0, min(offset_x, new_w - w))
        top = max(0, min(offset_y, new_h - h))

        img_cropped = img_resized.crop((left, top, left + w, top + h))
        return np.array(img_cropped)

    return image_clip.fl(resize_frame)

def overlay_captions(video, vtt_data):
    """
    Generates word-by-word highlighted TextClips and overlays them directly onto the frames.
    Implements a TikTok/Shorts style chunked caption logic.
    """
    import numpy as np
    logger.info("Generating modern captions...")

    # 1. Group words into short chunks (max 3-5 words)
    chunks = []
    current_chunk = []
    chunk_start = 0.0

    for i, w in enumerate(vtt_data):
        if not current_chunk:
            chunk_start = w['start']

        current_chunk.append(w)

        # Determine if we should break the chunk
        is_end = (i == len(vtt_data) - 1)
        too_long = len(current_chunk) >= 4
        # If there's a significant pause after this word, break
        pause_after = False
        if not is_end and (vtt_data[i+1]['start'] - w['end'] > 0.4):
            pause_after = True

        if is_end or too_long or pause_after:
            chunks.append({
                'words': current_chunk,
                'start': chunk_start,
                'end': w['end']
            })
            current_chunk = []

    # Load fonts once
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 90)
    except:
        try:
            font = ImageFont.truetype("Arial-Bold.ttf", 90)
        except:
            font = ImageFont.load_default()

    space_width = font.getbbox(" ")[2]

    # 2. Filter function to draw directly on video frames
    def render_captions_on_frame(get_frame, t):
        frame = get_frame(t)

        # Find which chunk is active
        active_chunk = None
        for chunk in chunks:
            if chunk['start'] <= t <= chunk['end']:
                active_chunk = chunk
                break

        if not active_chunk:
            return frame # No active captions, return original frame

        # Draw on frame using PIL
        img = Image.fromarray(frame)
        draw = ImageDraw.Draw(img)

        # Calculate layout
        total_width = 0
        word_boxes = []

        for w in active_chunk['words']:
            w_text = w['word']
            bbox = font.getbbox(w_text)
            w_width = bbox[2] - bbox[0]
            word_boxes.append(w_width)
            total_width += w_width + space_width

        total_width -= space_width # Remove trailing space

        # Draw words
        x = (1080 - total_width) // 2
        y = 1350 # Lower third placement

        for i, w in enumerate(active_chunk['words']):
            is_active = w['start'] <= t <= w['end']

            # Active word is yellow, others are white
            fill_color = (255, 235, 59) if is_active else (255, 255, 255)

            text = w['word']
            stroke_width = 5

            # Draw stroke/outline
            for dx in range(-stroke_width, stroke_width + 1):
                for dy in range(-stroke_width, stroke_width + 1):
                    draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))

            # Draw text
            draw.text((x, y), text, font=font, fill=fill_color)

            # Advance x
            x += word_boxes[i] + space_width

        return np.array(img)

    return video.fl(render_captions_on_frame)

def assemble_video(audio_path, vtt_data, image_paths, output_path, music_dir="assets/music"):
    """
    Assembles the final video with images, voiceover, music, and captions.
    """
    logger.info("Starting video assembly...")

    if not image_paths:
        raise ValueError("No images provided for assembly.")

    audio = AudioFileClip(audio_path)
    total_duration = audio.duration

    # We apply a slight overlap for crossfading
    padding = 0.5

    # Calculate duration per scene
    # Because scenes overlap by `padding` seconds, the total visual time is less.
    # Total Visual Time = (N * scene_duration) - ( (N - 1) * padding )
    # Therefore, scene_duration = (Total Visual Time + (N - 1) * padding) / N
    N = len(image_paths)
    scene_duration = (total_duration + (N - 1) * padding) / N

    logger.info(f"Total duration: {total_duration:.2f}s, Scenes: {N}, Scene Duration: {scene_duration:.2f}s, Crossfade: {padding}s")

    video_clips = []

    for i, img_path in enumerate(image_paths):
        resized_path = resize_image_for_video(img_path)

        # Create image clip for this scene
        clip = ImageClip(resized_path).set_duration(scene_duration)

        # Apply Ken Burns effect (slow zoom)
        clip = apply_ken_burns(clip)

        video_clips.append(clip)

    # Concatenate scenes with crossfade
    for i in range(1, len(video_clips)):
        # Start each clip before the previous one ends
        video_clips[i] = video_clips[i].set_start(video_clips[i-1].end - padding)
        # Apply crossfade effect
        video_clips[i] = video_clips[i].crossfadein(padding)

    final_visuals = CompositeVideoClip(video_clips)

    # Ensure visual duration matches audio exactly
    final_visuals = final_visuals.set_duration(total_duration)

    # Add captions on top of visuals
    final_video = overlay_captions(final_visuals, vtt_data)

    # Mix audio
    final_audio = audio

    # Try to find and add background music
    if os.path.exists(music_dir) and os.path.isdir(music_dir):
        music_files = [f for f in os.listdir(music_dir) if f.endswith(('.mp3', '.wav', '.m4a'))]
        if music_files:
            music_file = random.choice(music_files)
            music_path = os.path.join(music_dir, music_file)
            logger.info(f"Adding background music: {music_path}")

            bg_music = AudioFileClip(music_path)

            # Loop music if shorter than video, or trim if longer
            if bg_music.duration < total_duration:
                # Naive loop by concatenating
                repeats = int(total_duration // bg_music.duration) + 1
                bg_music = concatenate_audioclips([bg_music] * repeats)

            bg_music = bg_music.subclip(0, total_duration)

            # Lower background music volume (ducking)
            bg_music = bg_music.volumex(0.15)

            final_audio = CompositeAudioClip([audio, bg_music])
        else:
            logger.info("Music directory is empty. Proceeding with narration-only audio.")
    else:
        logger.info(f"Music directory not found at {music_dir}. Proceeding with narration-only audio.")

    # Set final audio
    final_video = final_video.set_audio(final_audio)

    logger.info(f"Exporting final video to {output_path} (Vertical 1080x1920)")

    # Hard cap duration to 58s for Shorts safety
    if final_video.duration > 58.0:
        logger.warning(f"Video duration ({final_video.duration:.2f}s) exceeds 58s. Capping at 58.0s.")
        final_video = final_video.subclip(0, 58.0)

    # Export
    final_video.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="5000k",
        preset="ultrafast",
        threads=4
    )

    # Cleanup temp resized images
    for p in image_paths:
        tmp = f"{p}_resized.jpg"
        if os.path.exists(tmp):
            os.remove(tmp)

    logger.info("Video assembly complete.")
    return output_path

if __name__ == "__main__":
    # Test script directly (mock dependencies)
    pass
