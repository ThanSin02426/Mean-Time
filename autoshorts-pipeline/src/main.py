import os
import sys
import argparse
import logging
import random
import re
from typing import List

# Pillow 10+ removed Image.ANTIALIAS, while MoviePy 1.0.3 still references it.
try:
    from PIL import Image
    if not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
except Exception:
    pass

from src.script_gen import generate_script
from src.voiceover import generate_audio
from src.subtitles import transcribe_audio_with_whisper
from src.video_assembly import assemble_video
from src.uploader import upload_video

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("autoshorts")

CATEGORY_KEYWORDS = {
    "space": ["space", "planet", "galaxy", "black hole", "nasa", "moon", "mars", "asteroid", "universe", "star", "solar"],
    "ocean": ["ocean", "sea", "deep sea", "marine", "shark", "whale"],
    "animals": ["animal", "animals", "wildlife", "creature", "predator", "birds", "insects"],
    "history": ["history", "ancient", "civilization", "empire", "war", "king", "queen"],
    "psychology": ["psychology", "brain", "mind", "habit", "human behavior"],
    "physics": ["physics", "quantum", "gravity", "time", "energy", "science"],
    "ai": ["artificial intelligence", "ai", "robot", "technology", "future tech"],
    "places": ["places", "earth", "city", "country", "island", "desert", "mountain"],
}

TOPIC_TEMPLATES = {
    "space": [
        "3 terrifying space facts that sound fake",
        "3 black hole facts that feel impossible",
        "3 Mars mysteries scientists still debate",
        "3 moon facts that will mess with your head",
        "3 galaxy facts that make Earth feel tiny",
        "3 asteroid facts that are genuinely scary",
        "3 universe facts that sound unreal",
        "3 NASA discoveries that changed space science",
        "3 planet facts you will not forget",
        "3 star facts that are hard to believe",
    ],
    "ocean": [
        "3 deep ocean facts that sound fake",
        "3 terrifying sea creatures you won't believe exist",
        "3 ocean mysteries scientists still cannot explain",
        "3 deep sea facts scarier than space",
        "3 shark facts that are misunderstood",
        "3 whale facts that feel impossible",
        "3 hidden ocean places that look unreal",
        "3 marine facts that will surprise you",
        "3 ocean survival facts everyone should know",
        "3 underwater discoveries that changed science",
    ],
    "animals": [
        "3 animal facts that sound fake",
        "3 dangerous animal facts you should know",
        "3 wildlife facts that are hard to believe",
        "3 predator facts that feel unreal",
        "3 insect facts that will shock you",
        "3 bird facts that sound impossible",
        "3 animal survival tricks that are genius",
        "3 weird creature facts you won't forget",
        "3 nature facts that prove animals are smarter",
        "3 animal myths that are actually false",
    ],
    "history": [
        "3 history facts they never taught you",
        "3 ancient civilization facts that sound fake",
        "3 empire facts that changed the world",
        "3 crazy history facts that are actually real",
        "3 ancient mysteries still unsolved",
        "3 war facts that changed everything",
        "3 lost city facts that feel unreal",
        "3 royal history facts that sound impossible",
        "3 archaeology discoveries that shocked scientists",
        "3 forgotten history facts worth knowing",
    ],
    "psychology": [
        "3 psychology facts that explain people",
        "3 brain facts that sound fake",
        "3 human behavior facts you can use daily",
        "3 mind tricks your brain plays on you",
        "3 habit facts that changed how I think",
        "3 memory facts that feel impossible",
        "3 social psychology facts everyone should know",
        "3 motivation facts that actually make sense",
        "3 decision-making facts that are scary",
        "3 body language facts that reveal more than words",
    ],
    "physics": [
        "3 physics facts that sound impossible",
        "3 time facts that will bend your brain",
        "3 gravity facts that feel fake",
        "3 quantum facts that sound unreal",
        "3 energy facts you will not forget",
        "3 science facts that changed physics",
        "3 light facts that are hard to believe",
        "3 universe physics facts that feel illegal",
        "3 motion facts that explain everyday life",
        "3 temperature facts that sound fake",
    ],
    "ai": [
        "3 AI facts everyone should know",
        "3 future tech facts that sound unreal",
        "3 robot facts that feel like science fiction",
        "3 artificial intelligence myths people believe",
        "3 AI tools changing how people work",
        "3 tech facts that will matter soon",
        "3 automation facts that are already real",
        "3 machine learning facts explained simply",
        "3 scary AI facts without the hype",
        "3 AI career facts beginners should know",
    ],
    "places": [
        "3 places on Earth that look unreal",
        "3 weird places you will not believe exist",
        "3 hidden places that feel like another planet",
        "3 dangerous places people still visit",
        "3 natural wonders that sound fake",
        "3 mystery locations scientists study",
        "3 abandoned places with strange stories",
        "3 islands with unbelievable facts",
        "3 desert facts that feel impossible",
        "3 Earth facts that make maps feel different",
    ],
    "general": [
        "3 facts that sound fake but are real",
        "3 weird facts you will remember",
        "3 science facts that feel impossible",
        "3 facts that changed how I see the world",
        "3 hidden facts most people never hear",
        "3 unbelievable facts explained simply",
        "3 facts that are stranger than fiction",
        "3 surprising facts that actually matter",
        "3 facts that make you question everything",
        "3 quick facts worth knowing today",
    ],
}


def _normalize_topic_key(text: str) -> str:
    lowered = str(text or "").lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(k in lowered for k in keywords):
            return category
    return "general"


def _clean_topic_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip(" \t\n\r-•*0123456789.()[]"))


def _generate_similar_topic(seed_topic: str, existing_topics: List[str]) -> str:
    """Return a same-family topic that is appended at the END of the queue.

    If topics.txt has around 10 lines, the same family naturally returns after the
    rest of the queue has cycled, which matches the desired never-ending queue.
    """
    category = _normalize_topic_key(seed_topic)
    templates = TOPIC_TEMPLATES.get(category, TOPIC_TEMPLATES["general"])
    existing_lower = {t.lower().strip() for t in existing_topics}
    start = abs(hash(seed_topic)) % len(templates)
    for offset in range(len(templates)):
        candidate = templates[(start + offset) % len(templates)]
        if candidate.lower() not in existing_lower and candidate.lower() != seed_topic.lower():
            logger.info(f"Generated replacement queue topic from category '{category}': {candidate}")
            return candidate
    suffix = random.randint(10, 999)
    return f"3 surprising {category} facts you will remember {suffix}"


def pop_topic_and_refresh_queue(topics_file: str) -> str:
    if not os.path.exists(topics_file):
        raise FileNotFoundError(f"Topics file not found: {topics_file}")
    with open(topics_file, "r", encoding="utf-8") as f:
        topics = [_clean_topic_line(line) for line in f.readlines()]
    topics = [t for t in topics if t]
    if not topics:
        raise RuntimeError(f"No topics found in {topics_file}")

    selected = topics.pop(0)
    replacement = _generate_similar_topic(selected, topics)
    topics.append(replacement)

    # Keep order and remove accidental duplicate lines.
    seen, final_topics = set(), []
    for topic in topics:
        key = topic.lower().strip()
        if key and key not in seen:
            final_topics.append(topic)
            seen.add(key)

    with open(topics_file, "w", encoding="utf-8") as f:
        for topic in final_topics:
            f.write(topic + "\n")

    logger.info(f"Popped topic from queue: '{selected}'")
    logger.info(f"Appended replacement topic at queue end: '{replacement}'")
    return selected


def _narration_text(script_data: dict) -> str:
    full_text = str(script_data.get("script") or "").strip()
    if full_text:
        return full_text
    scenes = script_data.get("scenes", [])
    return " ".join(str(s.get("text", "")) for s in scenes).strip()


def process_topic(topic, upload=True):
    logger.info(f"=== Starting Pipeline for Topic: '{topic}' ===")
    temp_files = []
    try:
        script_data = generate_script(topic)
        title = script_data.get('title', 'YouTube Short')
        description = script_data.get('description', '')
        tags = script_data.get('tags', [])
        scenes = script_data.get('scenes', [])
        full_text = _narration_text(script_data)
        if not full_text:
            raise RuntimeError("Generated script/narration text is empty.")

        from src.visuals import generate_visuals
        visual_data = generate_visuals(scenes, output_dir="temp_images", topic=topic)
        temp_files.extend([v["path"] for v in visual_data if os.path.exists(v.get("path", ""))])
        logger.info(f"Generated {len(visual_data)} visuals.")

        audio_path = "temp_audio.mp3"
        edge_json_path = "temp_edge_captions.json"
        synced_json_path = "output/captions.json"
        os.makedirs("output", exist_ok=True)

        logger.info("Generating TTS narration audio...")
        edge_timings = generate_audio(full_text, audio_path, edge_json_path)
        temp_files.extend([audio_path, edge_json_path])
        logger.info(f"TTS audio saved to: {audio_path}")

        logger.info("Generating synced subtitles from ACTUAL audio using Whisper/faster-whisper...")
        word_timings = transcribe_audio_with_whisper(
            audio_path=audio_path,
            output_json_path=synced_json_path,
            reference_text=full_text,
        )
        logger.info(f"Synced captions saved to: {synced_json_path} ({len(word_timings)} words)")
        if len(word_timings) < max(5, int(len(full_text.split()) * 0.45)):
            logger.warning("Synced caption word count is low; falling back to edge/even timings may have occurred.")

        output_video_path = "output/final_short.mp4"
        assemble_video(audio_path, word_timings, visual_data, output_video_path)
        if not os.path.exists(output_video_path):
            raise FileNotFoundError(f"Video assembly failed to produce {output_video_path}")

        from moviepy.editor import VideoFileClip
        final_clip = VideoFileClip(output_video_path)
        final_duration = float(final_clip.duration)
        final_clip.close()
        file_size_mb = os.path.getsize(output_video_path) / (1024 * 1024)

        try:
            image_paths = [v["path"] for v in visual_data if v.get("type") == "image" and os.path.exists(v.get("path", ""))]
            if image_paths:
                from PIL import Image
                thumb_w, thumb_h = 270, 480
                contact = Image.new('RGB', (thumb_w * len(image_paths), thumb_h), (10, 10, 15))
                for idx, imp in enumerate(image_paths):
                    im = Image.open(imp).convert("RGB").resize((thumb_w, thumb_h))
                    contact.paste(im, (idx * thumb_w, 0))
                contact.save("output/debug_contact_sheet.jpg")
        except Exception as exc:
            logger.warning(f"Failed to create debug contact sheet: {exc}")

        logger.info("--- Pipeline Summary ---")
        logger.info(f"Topic: {topic}")
        logger.info(f"Scenes Generated: {len(visual_data)}")
        logger.info(f"Narration Words: {len(full_text.split())}")
        logger.info(f"Synced Caption Words: {len(word_timings)}")
        logger.info(f"Video Duration: {final_duration:.2f}s")
        logger.info(f"Output MP4: {output_video_path} ({file_size_mb:.2f} MB)")

        if final_duration > 58.0:
            raise RuntimeError(f"Quality Gate Failed: Video is too long ({final_duration:.2f}s).")
        if not visual_data:
            raise RuntimeError("Quality Gate Failed: No visual scenes were generated.")
        if not word_timings:
            raise RuntimeError("Quality Gate Failed: Synced captions JSON has zero words.")
        if file_size_mb < 0.5:
            raise RuntimeError("Quality Gate Failed: Output MP4 is suspiciously small.")

        if upload:
            video_url = upload_video(output_video_path, title, description, tags)
            try:
                if visual_data and visual_data[0].get("type") == "image":
                    from src.uploader import upload_thumbnail
                    video_id = video_url.split('/')[-1]
                    upload_thumbnail(video_id, visual_data[0]["path"])
            except Exception as exc:
                logger.warning(f"Thumbnail upload skipped/failed: {exc}")
            logger.info(f"=== Pipeline Completed Successfully! URL: {video_url} ===")
        else:
            logger.info(f"=== Pipeline Completed Successfully! Video saved to {output_video_path} (Upload skipped) ===")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        raise
    finally:
        logger.info("Cleaning up temporary files...")
        for f in temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception as e:
                    logger.warning(f"Could not remove {f}: {e}")
        if os.path.exists("temp_images"):
            try:
                for p in os.listdir("temp_images"):
                    pp = os.path.join("temp_images", p)
                    if os.path.isfile(pp):
                        os.remove(pp)
                os.rmdir("temp_images")
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="AutoShorts: End-to-End YouTube Shorts Automation Pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic", type=str, help="Single topic to generate a short for")
    group.add_argument("--topics-file", type=str, help="Topic queue file. First topic is used, then replaced at the end.")
    parser.add_argument("--no-upload", action="store_true", help="Skip YouTube upload and save artifact locally")
    args = parser.parse_args()

    if args.topic:
        topic = args.topic.strip()
    else:
        topic = pop_topic_and_refresh_queue(args.topics_file)
    process_topic(topic, upload=not args.no_upload)


if __name__ == "__main__":
    main()
