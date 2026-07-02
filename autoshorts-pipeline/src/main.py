import os
import sys
import argparse
import logging
import random
from src.script_gen import generate_script
from src.voiceover import generate_audio
import json
from src.visuals import generate_images
from src.video_assembly import assemble_video
from src.uploader import upload_video

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("autoshorts")

def process_topic(topic, upload=True):
    """
    Runs the full end-to-end pipeline for a single topic.
    """
    logger.info(f"=== Starting Pipeline for Topic: '{topic}' ===")
    temp_files = []

    try:
        # 1. Script Generation
        script_data = generate_script(topic)
        title = script_data.get('title', 'YouTube Short')
        description = script_data.get('description', '')
        tags = script_data.get('tags', [])
        scenes = script_data.get('scenes', [])

        # Extract full text for narration
        full_text = script_data.get('script')
        if not full_text:
            full_text = " ".join([s['text'] for s in scenes])

        # 2. Images Generation
        image_dir = "temp_images"
        image_paths = generate_images(scenes, output_dir=image_dir)
        temp_files.extend(image_paths)
        logger.info(f"Generated {len(image_paths)} images.")

        # 3. Voiceover & Subtitles Generation
        audio_path = "temp_audio.mp3"
        json_path = "temp_captions.json"

        logger.info("Generating voiceover and capturing word boundaries...")
        vtt_data = generate_audio(full_text, audio_path, json_path)
        temp_files.extend([audio_path, json_path])
        logger.info(f"Audio saved to: {audio_path}")
        logger.info(f"Captions/timing saved to: {json_path}")

        # 4. Video Assembly
        os.makedirs("output", exist_ok=True)
        output_video_path = "output/final_short.mp4"
        assemble_video(audio_path, vtt_data, image_paths, output_video_path)

        if os.path.exists(output_video_path):
            file_size_mb = os.path.getsize(output_video_path) / (1024 * 1024)
            logger.info(f"Final MP4 created successfully at {output_video_path} ({file_size_mb:.2f} MB)")
        else:
            logger.error(f"Failed to create final MP4 at {output_video_path}")
            raise FileNotFoundError(f"Video assembly failed to produce {output_video_path}")

        # 5. YouTube Upload
        if upload:
            video_url = upload_video(output_video_path, title, description, tags)
            # The prompt requested setting a thumbnail from the first scene image
            if image_paths:
                from src.uploader import upload_thumbnail
                video_id = video_url.split('/')[-1]
                upload_thumbnail(video_id, image_paths[0])

            logger.info(f"=== Pipeline Completed Successfully! URL: {video_url} ===")
        else:
            logger.info(f"=== Pipeline Completed Successfully! Video saved locally to {output_video_path} (Upload skipped) ===")

    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)

    finally:
        # Cleanup
        logger.info("Cleaning up temporary files...")
        for f in temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception as e:
                    logger.warning(f"Could not remove {f}: {e}")

        # Remove empty temp_images dir
        if os.path.exists("temp_images") and not os.listdir("temp_images"):
            os.rmdir("temp_images")

def main():
    parser = argparse.ArgumentParser(description="AutoShorts: End-to-End YouTube Shorts Automation Pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--topic", type=str, help="Single topic to generate a short for")
    group.add_argument("--topics-file", type=str, help="File containing a list of topics (one per line). Will pick one randomly.")

    parser.add_argument("--no-upload", action="store_true", help="Skip YouTube upload and save video locally")

    args = parser.parse_args()

    topic = None
    if args.topic:
        topic = args.topic
    elif args.topics_file:
        if not os.path.exists(args.topics_file):
            logger.error(f"Topics file not found: {args.topics_file}")
            sys.exit(1)

        with open(args.topics_file, 'r', encoding='utf-8') as f:
            topics = [line.strip() for line in f.readlines() if line.strip()]

        if not topics:
            logger.error(f"No topics found in {args.topics_file}")
            sys.exit(1)

        topic = topics.pop(0) # Take the first topic
        logger.info(f"Popped topic from queue: '{topic}'")

        # Write remaining topics back to act as a queue
        with open(args.topics_file, 'w', encoding='utf-8') as f:
            for t in topics:
                f.write(t + '\n')

    process_topic(topic, upload=not args.no_upload)

if __name__ == "__main__":
    main()
