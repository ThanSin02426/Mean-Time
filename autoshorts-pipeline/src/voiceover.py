import os
import subprocess
import webvtt
import logging

logger = logging.getLogger(__name__)

def generate_audio(text, output_mp3_path, output_vtt_path, voice="en-US-ChristopherNeural"):
    """
    Generates TTS audio using edge-tts CLI and creates a VTT file for subtitles.
    """
    logger.info(f"Generating TTS audio using voice {voice}...")

    # Use python module directly to get word level boundaries and create our own VTT
    import asyncio
    import edge_tts

    async def _generate():
        communicate = edge_tts.Communicate(text, voice)
        submaker = edge_tts.SubMaker()
        with open(output_mp3_path, "wb") as file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    submaker.create_sub((chunk["offset"], chunk["duration"]), chunk["text"])

        with open(output_vtt_path, "w", encoding="utf-8") as file:
            file.write(submaker.generate_subs())

    asyncio.run(_generate())
    logger.info(f"Successfully generated audio at {output_mp3_path} and subtitles at {output_vtt_path}")

def time_to_seconds(time_str):
    """
    Converts VTT time format (HH:MM:SS.mmm) to seconds as float.
    """
    try:
        h, m, s = time_str.split(':')
        return float(h) * 3600 + float(m) * 60 + float(s)
    except ValueError:
        # Fallback if only MM:SS.mmm
        m, s = time_str.split(':')
        return float(m) * 60 + float(s)

def parse_vtt(vtt_path):
    """
    Parses a VTT file and returns a list of dictionaries with word-level timings.
    Each dict contains: start, end, word.
    """
    logger.info(f"Parsing VTT file: {vtt_path}")
    if not os.path.exists(vtt_path):
        raise FileNotFoundError(f"VTT file not found: {vtt_path}")

    word_boundaries = []

    try:
        # edge-tts sometimes outputs SRT format to the VTT file (with comma instead of dot in timings)
        # We'll read the file directly if webvtt fails or just parse it manually to handle both.
        with open(vtt_path, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]

        # Simple parser for SRT/VTT format
        for i in range(len(lines)):
            if '-->' in lines[i]:
                time_line = lines[i]
                start_str, end_str = time_line.split('-->')
                start_str = start_str.strip().replace(',', '.')
                end_str = end_str.strip().replace(',', '.')

                start_sec = time_to_seconds(start_str)
                end_sec = time_to_seconds(end_str)

                # The next line is the text
                if i + 1 < len(lines):
                    word = lines[i+1].strip()

                    # Split into individual words if it outputs sentences
                    words_split = word.split(' ')

                    if len(words_split) > 1:
                        # Distribute time evenly across words
                        duration = end_sec - start_sec
                        word_duration = duration / len(words_split)
                        for j, w in enumerate(words_split):
                            if w:
                                word_boundaries.append({
                                    "start": start_sec + (j * word_duration),
                                    "end": start_sec + ((j + 1) * word_duration),
                                    "word": w
                                })
                    else:
                        if word:
                            word_boundaries.append({
                                "start": start_sec,
                                "end": end_sec,
                                "word": word
                            })

        logger.info(f"Successfully parsed {len(word_boundaries)} words from subtitle file.")
        return word_boundaries
    except Exception as e:
        logger.error(f"Failed to parse VTT file: {e}")
        raise

if __name__ == "__main__":
    # Test script directly
    logging.basicConfig(level=logging.INFO)
    test_text = "This is a test to verify audio and subtitle generation."

    mp3_file = "test_audio.mp3"
    vtt_file = "test_subs.vtt"

    generate_audio(test_text, mp3_file, vtt_file)

    if os.path.exists(vtt_file):
        words = parse_vtt(vtt_file)
        print("Parsed VTT Data:")
        for w in words:
            print(f"[{w['start']:.2f} -> {w['end']:.2f}] {w['word']}")

        os.remove(mp3_file)
        os.remove(vtt_file)
