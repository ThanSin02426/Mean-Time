import os
import subprocess
import webvtt
import logging

logger = logging.getLogger(__name__)

import json
import asyncio
import edge_tts

def generate_audio(text, output_mp3_path, output_json_path, voice="en-US-ChristopherNeural"):
    """
    Generates TTS audio using edge-tts module and saves WordBoundary timings directly to a JSON file.
    """
    logger.info(f"Generating TTS audio using voice {voice}...")

    word_boundaries = []

    async def _generate():
        communicate = edge_tts.Communicate(text, voice)
        with open(output_mp3_path, "wb") as file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    # offset and duration are in 100-nanosecond units (1e-7 seconds)
                    # Convert them to seconds for our JSON schema.
                    start_sec = chunk["offset"] / 1e7
                    duration_sec = chunk["duration"] / 1e7
                    end_sec = start_sec + duration_sec

                    word_boundaries.append({
                        "word": chunk["text"],
                        "start": start_sec,
                        "end": end_sec
                    })

        if not word_boundaries:
            logger.warning("No WordBoundary events returned by TTS. Generating fallback boundaries based on text.")
            words = text.split()
            # Estimate 0.3s per word if we have no other data
            for i, w in enumerate(words):
                start_sec = i * 0.3
                end_sec = start_sec + 0.3
                word_boundaries.append({
                    "word": w,
                    "start": start_sec,
                    "end": end_sec
                })

    asyncio.run(_generate())

    # Save directly to JSON
    with open(output_json_path, "w", encoding="utf-8") as file:
        json.dump(word_boundaries, file, indent=2)

    logger.info(f"Successfully generated audio at {output_mp3_path} and timing JSON at {output_json_path}")
    return word_boundaries

if __name__ == "__main__":
    # Test script directly
    logging.basicConfig(level=logging.INFO)
    test_text = "This is a test to verify audio and timing generation."

    mp3_file = "test_audio.mp3"
    json_file = "test_captions.json"

    words = generate_audio(test_text, mp3_file, json_file)

    if os.path.exists(json_file):
        print("Parsed JSON Data:")
        for w in words:
            print(f"[{w['start']:.2f} -> {w['end']:.2f}] {w['word']}")

        os.remove(mp3_file)
        os.remove(json_file)
