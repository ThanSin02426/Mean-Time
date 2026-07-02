import os
import random
import logging
from moviepy.editor import (
    AudioFileClip, ImageClip, TextClip, CompositeVideoClip, CompositeAudioClip,
    concatenate_videoclips, concatenate_audioclips, vfx
)
from PIL import Image

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
    Applies a slow zoom effect to an ImageClip.
    """
    # Custom resize function that avoids Image.ANTIALIAS issue in moviepy 1.0.3 with newer Pillow
    def resize_frame(get_frame, t):
        frame = get_frame(t)
        # Calculate zoom factor
        zoom = 1 + 0.05 * (t / image_clip.duration)

        # New dimensions
        h, w = frame.shape[:2]
        new_h, new_w = int(h * zoom), int(w * zoom)

        # Center crop to original size
        img = Image.fromarray(frame)
        try:
            resample_filter = Image.Resampling.LANCZOS
        except AttributeError:
            resample_filter = Image.ANTIALIAS

        img_resized = img.resize((new_w, new_h), resample_filter)

        # Crop back to original (w, h)
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        img_cropped = img_resized.crop((left, top, left + w, top + h))

        import numpy as np
        return np.array(img_cropped)

    return image_clip.fl(resize_frame)

def overlay_captions(video, vtt_data):
    """
    Generates word-by-word highlighted TextClips and overlays them.
    """
    logger.info("Generating captions from VTT data...")
    clips = [video]

    for word_info in vtt_data:
        start_t = word_info['start']
        end_t = word_info['end']
        word = word_info['word']

        # Calculate duration of the word on screen
        duration = end_t - start_t
        if duration <= 0:
            duration = 0.1 # Minimum duration fallback

        try:
            # Create a TextClip for the word.
            # Use a bold sans-serif font, white text with a black stroke (outline) for contrast.
            txt_clip = TextClip(
                word,
                fontsize=90,
                color='yellow',
                stroke_color='black',
                stroke_width=3,
                font='Arial-Bold',
                method='caption',
                size=(900, None) # Limit width to fit 1080p screen
            )

            # Position at center bottom (lower third)
            txt_clip = txt_clip.set_position(('center', 1400))
            txt_clip = txt_clip.set_start(start_t).set_duration(duration)

            clips.append(txt_clip)
        except Exception as e:
            logger.warning(f"Could not generate TextClip for word '{word}': {e}. Skipping this word.")

    return CompositeVideoClip(clips)

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
    if os.path.exists(music_dir):
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

    # Set final audio
    final_video = final_video.set_audio(final_audio)

    logger.info(f"Exporting final video to {output_path} (Vertical 1080x1920)")

    # Export
    final_video.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
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
