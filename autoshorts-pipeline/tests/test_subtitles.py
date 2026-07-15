from src.subtitles import WordTiming, align_script_to_whisper, create_caption_chunks


def test_forced_alignment_reaches_full_script_coverage():
    script = "Three stars can collapse into a tiny neutron star."
    whisper = [
        {"word": "three", "start": 0.20, "end": 0.48},
        {"word": "stars", "start": 0.50, "end": 0.78},
        {"word": "collapse", "start": 1.05, "end": 1.35},
        {"word": "into", "start": 1.38, "end": 1.55},
        {"word": "a", "start": 1.58, "end": 1.66},
        {"word": "tiny", "start": 1.69, "end": 1.92},
        {"word": "neutron", "start": 1.95, "end": 2.28},
        {"word": "star", "start": 2.31, "end": 2.55},
    ]
    words, report = align_script_to_whisper(script, whisper, 2.8)
    assert report["raw_coverage_ratio"] < 1.0
    assert report["final_alignment_ratio"] == 1.0
    assert len(words) == report["narration_word_count"]
    assert all(words[i].end <= words[i + 1].start for i in range(len(words) - 1))


def test_missing_whisper_word_is_interpolated_between_neighbors():
    script = "The silent ocean hides strange life."
    whisper = [
        {"word": "the", "start": 0.1, "end": 0.2},
        {"word": "silent", "start": 0.22, "end": 0.5},
        {"word": "ocean", "start": 0.52, "end": 0.8},
        {"word": "strange", "start": 1.1, "end": 1.35},
        {"word": "life", "start": 1.38, "end": 1.62},
    ]
    words, _ = align_script_to_whisper(script, whisper, 1.8)
    hidden = next(row for row in words if row.normalized == "hides")
    assert hidden.source == "interpolated"
    assert 0.8 <= hidden.start <= hidden.end <= 1.1


def test_caption_chunk_timing_limits():
    words = [WordTiming(f"word{i}", f"word{i}", i * 0.25, i * 0.25 + 0.18, "whisper", True) for i in range(12)]
    chunks = create_caption_chunks(words, 3.2)
    assert all(2 <= len(chunk.words) <= 4 for chunk in chunks[:-1])
    assert all(0.35 <= chunk.end - chunk.start <= 1.8 for chunk in chunks)
    assert chunks[-1].end < 3.2


def test_first_caption_does_not_precede_speech_by_more_than_point_one():
    words = [
        WordTiming("Hello", "hello", 0.42, 0.70, "whisper", True),
        WordTiming("world", "world", 0.72, 1.00, "whisper", True),
    ]
    chunks = create_caption_chunks(words, 1.4)
    assert chunks[0].start >= 0.32


def test_common_number_forms_align_to_spoken_words():
    script = "The object is 1,000 times heavier."
    whisper = [
        {"word": "the", "start": 0.1, "end": 0.2},
        {"word": "object", "start": 0.21, "end": 0.45},
        {"word": "is", "start": 0.46, "end": 0.55},
        {"word": "one", "start": 0.56, "end": 0.70},
        {"word": "thousand", "start": 0.71, "end": 1.05},
        {"word": "times", "start": 1.06, "end": 1.25},
        {"word": "heavier", "start": 1.26, "end": 1.55},
    ]
    words, report = align_script_to_whisper(script, whisper, 1.8)
    assert report["raw_coverage_ratio"] == 1.0
    assert [word.normalized for word in words][3:5] == ["one", "thousand"]
