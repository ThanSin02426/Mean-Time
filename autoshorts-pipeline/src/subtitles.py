from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

from .atomic_io import atomic_write_json, atomic_write_text
from .audio_utils import run_command

logger = logging.getLogger(__name__)

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


@dataclass(slots=True)
class WordTiming:
    word: str
    normalized: str
    start: float
    end: float
    source: str
    matched: bool


@dataclass(slots=True)
class CaptionChunk:
    text: str
    start: float
    end: float
    words: list[str]


def _integer_to_words(value: int) -> list[str]:
    if value < 0:
        return ["minus", *_integer_to_words(abs(value))]
    if value < 20:
        return [ONES[value]]
    if value < 100:
        return [TENS[value // 10]] + (_integer_to_words(value % 10) if value % 10 else [])
    if value < 1_000:
        return [ONES[value // 100], "hundred"] + (_integer_to_words(value % 100) if value % 100 else [])
    if value < 1_000_000:
        return _integer_to_words(value // 1_000) + ["thousand"] + (_integer_to_words(value % 1_000) if value % 1_000 else [])
    if value < 1_000_000_000:
        return _integer_to_words(value // 1_000_000) + ["million"] + (_integer_to_words(value % 1_000_000) if value % 1_000_000 else [])
    return [str(value)]


def _number_token_to_words(token: str) -> list[str]:
    cleaned = token.replace(",", "")
    if "." in cleaned:
        whole, decimal = cleaned.split(".", 1)
        words = _integer_to_words(int(whole or "0")) + ["point"]
        words.extend(ONES[int(digit)] for digit in decimal if digit.isdigit())
        return [item for group in words for item in (group if isinstance(group, list) else [group])]
    return _integer_to_words(int(cleaned))


def normalize_word(value: str) -> str:
    text = str(value or "").strip().lower().replace("’", "'")
    text = re.sub(r"(?<=\w)'(?=\w)", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def script_tokens(script: str) -> list[dict[str, Any]]:
    raw = re.findall(r"\d[\d,]*(?:\.\d+)?|\b[A-Za-z]+(?:[’'-][A-Za-z]+)*\b|[%£$€]|[.!?,;:]", script)
    tokens: list[dict[str, Any]] = []
    for item in raw:
        if re.fullmatch(r"[.!?,;:]", item):
            if tokens:
                tokens[-1]["display"] += item
                tokens[-1]["punctuation"] = item
            continue
        if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", item):
            for expanded in _number_token_to_words(item):
                tokens.append({"display": expanded, "normalized": normalize_word(expanded), "punctuation": ""})
            continue
        if item == "%":
            item = "percent"
        elif item in {"$", "£", "€"}:
            item = {"$": "dollars", "£": "pounds", "€": "euros"}[item]
        normalized = normalize_word(item)
        if normalized:
            tokens.append({"display": item, "normalized": normalized, "punctuation": ""})
    return tokens


def _recognized_tokens(words: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in words:
        raw = str(row.get("word", "")).strip()
        pieces = script_tokens(raw)
        if not pieces:
            continue
        start = max(0.0, float(row.get("start", 0.0) or 0.0))
        end = max(start + 0.03, float(row.get("end", start + 0.03) or start + 0.03))
        span = (end - start) / len(pieces)
        for index, piece in enumerate(pieces):
            piece_start = start + index * span
            result.append({
                "word": piece["display"],
                "normalized": piece["normalized"],
                "start": piece_start,
                "end": start + (index + 1) * span,
            })
    return result


def _nearest_recognized_bounds(
    script_index: int,
    mapping: dict[int, tuple[int, int]],
    recognized_count: int,
) -> tuple[int, int]:
    previous = [index for index in mapping if index < script_index]
    following = [index for index in mapping if index > script_index]
    left = mapping[max(previous)][1] + 1 if previous else 0
    right = mapping[min(following)][0] if following else recognized_count
    return left, right


def _augment_compound_matches(
    scripted: list[dict[str, Any]],
    recognized: list[dict[str, Any]],
    mapping: dict[int, tuple[int, int]],
) -> dict[int, tuple[float, float, str]]:
    """Match tokenization differences without borrowing surrounding silence.

    Whisper may split a script word (``landmasses`` -> ``land`` ``masses``)
    or merge adjacent script words. Exact sequence matching treats those as
    omissions, and the old interpolation then stretched the missing word to
    the next matched word, including any sentence pause.
    """
    timings: dict[int, tuple[float, float, str]] = {}
    used_recognized = {index for span in mapping.values() for index in range(span[0], span[1] + 1)}

    # One script token represented by two to four Whisper tokens.
    for script_index, script_row in enumerate(scripted):
        if script_index in mapping:
            continue
        left, right = _nearest_recognized_bounds(script_index, mapping, len(recognized))
        found: tuple[int, int] | None = None
        for width in range(2, 5):
            for recognized_start in range(left, right - width + 1):
                recognized_end = recognized_start + width - 1
                span_indices = range(recognized_start, recognized_end + 1)
                if any(index in used_recognized for index in span_indices):
                    continue
                joined = "".join(recognized[index]["normalized"] for index in span_indices)
                if joined == script_row["normalized"]:
                    found = (recognized_start, recognized_end)
                    break
            if found:
                break
        if found:
            mapping[script_index] = found
            used_recognized.update(range(found[0], found[1] + 1))
            timings[script_index] = (
                float(recognized[found[0]]["start"]),
                float(recognized[found[1]]["end"]),
                "whisper_split",
            )

    # Two to four script tokens represented by one Whisper token.
    script_index = 0
    while script_index < len(scripted):
        if script_index in mapping:
            script_index += 1
            continue
        matched_width = 0
        for width in range(2, 5):
            if script_index + width > len(scripted):
                break
            script_span = range(script_index, script_index + width)
            if any(index in mapping for index in script_span):
                continue
            left, right = _nearest_recognized_bounds(script_index, mapping, len(recognized))
            target = "".join(scripted[index]["normalized"] for index in script_span)
            candidate = next((
                index for index in range(left, right)
                if index not in used_recognized and recognized[index]["normalized"] == target
            ), None)
            if candidate is None:
                continue

            source_start = float(recognized[candidate]["start"])
            source_end = float(recognized[candidate]["end"])
            total_units = sum(max(1, len(scripted[index]["normalized"])) for index in script_span)
            cursor = source_start
            for offset, index in enumerate(script_span):
                units = max(1, len(scripted[index]["normalized"]))
                end = source_end if offset == width - 1 else cursor + (source_end - source_start) * units / total_units
                mapping[index] = (candidate, candidate)
                timings[index] = (cursor, end, "whisper_merged")
                cursor = end
            used_recognized.add(candidate)
            matched_width = width
            break
        script_index += matched_width or 1

    return timings


def align_script_to_whisper(script: str, whisper_words: Iterable[dict[str, Any]], duration: float) -> tuple[list[WordTiming], dict[str, Any]]:
    scripted = script_tokens(script)
    recognized = _recognized_tokens(whisper_words)
    if not scripted:
        raise ValueError("Narration script contains no words")
    duration = max(float(duration), 0.1)
    script_norm = [row["normalized"] for row in scripted]
    whisper_norm = [row["normalized"] for row in recognized]
    matcher = SequenceMatcher(a=script_norm, b=whisper_norm, autojunk=False)
    mapping: dict[int, tuple[int, int]] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mapping[block.a + offset] = (block.b + offset, block.b + offset)

    compound_timings = _augment_compound_matches(scripted, recognized, mapping)

    aligned: list[WordTiming | None] = [None] * len(scripted)
    for script_index, recognized_span in mapping.items():
        if script_index in compound_timings:
            raw_start, raw_end, source_name = compound_timings[script_index]
        else:
            raw_start = float(recognized[recognized_span[0]]["start"])
            raw_end = float(recognized[recognized_span[1]]["end"])
            source_name = "whisper"
        start = min(max(0.0, raw_start), duration)
        end = min(max(start + 0.03, raw_end), duration)
        aligned[script_index] = WordTiming(
            scripted[script_index]["display"], scripted[script_index]["normalized"],
            start, end, source_name, True,
        )

    matched_indices = [index for index, row in enumerate(aligned) if row is not None]
    if not matched_indices:
        step = duration / len(scripted)
        aligned = [
            WordTiming(row["display"], row["normalized"], index * step, min(duration, (index + 1) * step), "interpolated_all", False)
            for index, row in enumerate(scripted)
        ]
    else:
        boundaries = [-1, *matched_indices, len(scripted)]
        for left_index, right_index in zip(boundaries, boundaries[1:]):
            missing = list(range(left_index + 1, right_index))
            if not missing:
                continue
            left_end = aligned[left_index].end if left_index >= 0 and aligned[left_index] else 0.0
            right_start = aligned[right_index].start if right_index < len(scripted) and aligned[right_index] else duration
            available = max(0.03 * len(missing), right_start - left_end)
            step = available / len(missing)
            for offset, missing_index in enumerate(missing):
                missing_start = min(duration, left_end + offset * step)
                missing_end = min(duration, max(missing_start + 0.03, left_end + (offset + 1) * step))
                row = scripted[missing_index]
                aligned[missing_index] = WordTiming(row["display"], row["normalized"], missing_start, missing_end, "interpolated", False)

    output = [row for row in aligned if row is not None]
    previous_end = 0.0
    for index, row in enumerate(output):
        remaining = len(output) - index - 1
        latest_end = max(0.0, duration - remaining * 0.001)
        row.start = max(previous_end, min(float(row.start), latest_end))
        row.end = min(duration, max(row.start + 0.001, float(row.end)))
        if row.end < row.start:
            row.end = row.start
        row.start = round(row.start, 3)
        row.end = round(row.end, 3)
        previous_end = row.end

    matched_count = sum(row.matched for row in output)
    raw_coverage = matched_count / len(scripted)
    warnings: list[str] = []
    if raw_coverage < 0.90:
        warnings.append("Raw Whisper coverage is below the 90% target")
    if not recognized:
        warnings.append("Whisper returned no words; all timings were interpolated")
    report = {
        "narration_word_count": len(scripted),
        "raw_whisper_word_count": len(recognized),
        "matched_script_words": matched_count,
        "raw_coverage_ratio": round(raw_coverage, 4),
        "compound_split_matches": sum(row.source == "whisper_split" for row in output),
        "compound_merged_matches": sum(row.source == "whisper_merged" for row in output),
        "final_aligned_word_count": len(output),
        "final_alignment_ratio": round(len(output) / len(scripted), 4),
        "first_detected_speech_time": round(recognized[0]["start"], 3) if recognized else None,
        "last_speech_time": round(recognized[-1]["end"], 3) if recognized else None,
        "alignment_warnings": warnings,
    }
    return output, report


def _group_fits(rows: list[WordTiming], max_duration: float = 1.8, end_padding: float = 0.14) -> bool:
    if not rows:
        return True
    return max(rows[-1].end + end_padding, rows[0].start + 0.35) - rows[0].start <= max_duration + 1e-6


def _group_words(
    words: list[WordTiming],
    min_words: int,
    max_words: int,
    max_duration: float = 1.8,
    max_inter_word_silence: float = 0.35,
) -> list[list[WordTiming]]:
    """Create readable caption groups without cutting through spoken words.

    The 2–4 word rule is a preference, not permission to bridge a long pause or
    truncate the final spoken word. A one-word chunk is therefore allowed when
    timing requires it.
    """
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []

    for word in words:
        if current:
            silence = max(0.0, word.start - current[-1].end)
            projected = [*current, word]
            if silence > max_inter_word_silence or not _group_fits(projected, max_duration):
                groups.append(current)
                current = []

        current.append(word)
        punctuation_break = bool(re.search(r"[.!?,;:]$", word.word))
        if len(current) >= max_words or (len(current) >= min_words and punctuation_break):
            groups.append(current)
            current = []

    if current:
        groups.append(current)

    # Prefer 2–4 words, but merge a trailing singleton only when timing remains safe.
    if len(groups) >= 2 and len(groups[-1]) == 1:
        merged = [*groups[-2], *groups[-1]]
        if len(merged) <= max_words and _group_fits(merged, max_duration):
            groups[-2:] = [merged]

    return groups


def _build_chunk(rows: list[WordTiming], duration: float) -> CaptionChunk:
    start = max(0.0, rows[0].start)
    required_end = max(rows[-1].end + 0.14, start + 0.35)
    # Groups are formed to fit within 1.8s. Never cut through a spoken word if
    # rounding or a pathological timestamp still makes the span slightly longer.
    end = min(duration, required_end)
    if end - start > 1.8 + 1e-6:
        logger.warning(
            "Caption group exceeds 1.8s without a safe split: %.3fs (%s)",
            end - start,
            " ".join(row.word for row in rows),
        )
    return CaptionChunk(
        text=" ".join(row.word for row in rows),
        start=round(start, 3),
        end=round(end, 3),
        words=[row.word for row in rows],
    )


def create_caption_chunks(words: list[WordTiming], audio_duration: float, min_words: int = 2, max_words: int = 4) -> list[CaptionChunk]:
    if not words:
        return []

    groups = _group_words(words, min_words, max_words)
    chunks = [_build_chunk(group, audio_duration) for group in groups]

    for index in range(len(chunks) - 1):
        current, following = chunks[index], chunks[index + 1]
        spoken_end = groups[index][-1].end

        if current.end > following.start:
            # Never overlap two caption boxes. Grouping guarantees that the next
            # caption starts no earlier than the current group's final spoken word.
            current.end = round(max(spoken_end, following.start), 3)
        else:
            gap = following.start - current.end
            if 0 < gap <= 0.18:
                # Bridge only a tiny visual gap, but never keep the previous text
                # on screen more than 0.20s after its final spoken word.
                current.end = round(min(following.start, spoken_end + 0.20), 3)

    final_spoken_end = groups[-1][-1].end
    chunks[-1].end = round(
        min(chunks[-1].end, audio_duration, final_spoken_end + 0.20),
        3,
    )
    return chunks


def _caption_timing_metrics(
    words: list[WordTiming],
    chunks: list[CaptionChunk],
    min_words: int = 2,
    max_words: int = 4,
) -> dict[str, float | int]:
    groups = _group_words(words, min_words, max_words)
    durations = [max(0.0, chunk.end - chunk.start) for chunk in chunks]

    tails: list[float] = []
    if len(groups) == len(chunks):
        tails = [
            max(0.0, chunk.end - group[-1].end)
            for chunk, group in zip(chunks, groups)
        ]
    else:
        logger.warning(
            "Caption/group count mismatch while computing timing metrics: %d chunks vs %d groups",
            len(chunks),
            len(groups),
        )

    return {
        "minimum_caption_duration": round(min(durations), 3) if durations else 0.0,
        "maximum_caption_duration": round(max(durations), 3) if durations else 0.0,
        "short_caption_count": sum(duration < 0.34 for duration in durations),
        "maximum_caption_tail_after_word": round(max(tails), 3) if tails else 0.0,
    }

def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}:{minutes:02d}:{seconds % 60:05.2f}"


def _wrap_caption(text: str, limit: int = 24) -> str:
    if len(text) <= limit:
        return text
    words = text.split()
    best_index = min(range(1, len(words)), key=lambda index: abs(len(" ".join(words[:index])) - len(" ".join(words[index:]))))
    return " ".join(words[:best_index]) + r"\N" + " ".join(words[best_index:])


def write_ass(chunks: list[CaptionChunk], path: str | Path, width: int = 1080, height: int = 1920) -> None:
    font_size = 54 if width >= 1000 else max(22, round(width * 0.05))
    margin_v = 330 if height >= 1800 else max(110, round(height * 0.17))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,DejaVu Sans,{font_size},&H00FFFFFF,&H0000D7FF,&H00000000,&H98000000,-1,0,0,0,100,100,0,0,3,3,0,2,100,100,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for chunk in chunks:
        safe = _wrap_caption(chunk.text.replace("{", "(").replace("}", ")").replace("\n", " "))
        lines.append(f"Dialogue: 0,{_ass_time(chunk.start)},{_ass_time(chunk.end)},Caption,,0,0,0,,{safe}\n")
    atomic_write_text(path, "".join(lines))


def _max_active_speech_caption_gap(words: list[WordTiming], chunks: list[CaptionChunk]) -> float:
    """Return the largest uncovered interval inside any spoken-word timestamp."""
    largest = 0.0
    for word in words:
        intervals: list[tuple[float, float]] = []
        for chunk in chunks:
            start = max(word.start, chunk.start)
            end = min(word.end, chunk.end)
            if end > start:
                intervals.append((start, end))

        if not intervals:
            largest = max(largest, max(0.0, word.end - word.start))
            continue

        intervals.sort()
        cursor = word.start
        for start, end in intervals:
            if start > cursor:
                largest = max(largest, start - cursor)
            cursor = max(cursor, end)
        if cursor < word.end:
            largest = max(largest, word.end - cursor)
    return largest

def transcribe_and_align(
    audio_path: str | Path,
    exact_script: str,
    audio_duration: float,
    output_dir: str | Path,
    primary_model: str = "base.en",
    fallback_model: str = "small.en",
    device: str = "cpu",
    compute_type: str = "int8",
) -> tuple[list[WordTiming], list[CaptionChunk], dict[str, Any]]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    model_attempts = list(dict.fromkeys([primary_model, fallback_model]))
    final_words: list[WordTiming] = []
    final_report: dict[str, Any] = {}
    used_model = primary_model
    for attempt_index, model_name in enumerate(model_attempts):
        used_model = model_name
        logger.info("Transcribing canonical narration with faster-whisper model=%s", model_name)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is required for subtitle generation") from exc
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, _ = model.transcribe(
            str(audio_path), language="en", beam_size=5, word_timestamps=True,
            vad_filter=True,
            vad_parameters={
                "threshold": 0.35,
                "min_speech_duration_ms": 120,
                "min_silence_duration_ms": 300,
                "speech_pad_ms": 120,
            },
            condition_on_previous_text=False,
        )
        raw_words: list[dict[str, Any]] = []
        for segment in segments:
            for word in segment.words or []:
                raw_words.append({
                    "word": word.word,
                    "start": word.start,
                    "end": word.end,
                    "probability": word.probability,
                })
        atomic_write_json(output / f"whisper_words_{model_name.replace('.', '_')}.json", raw_words)
        final_words, final_report = align_script_to_whisper(exact_script, raw_words, audio_duration)
        if final_report["raw_coverage_ratio"] >= 0.90 or attempt_index == len(model_attempts) - 1:
            break

    chunks = create_caption_chunks(final_words, audio_duration)
    first_caption = chunks[0].start if chunks else None
    first_speech = final_report.get("first_detected_speech_time")
    timing_metrics = _caption_timing_metrics(final_words, chunks)
    final_report.update({
        "model_first_attempted": primary_model,
        "model_finally_used": used_model,
        "first_caption_time": first_caption,
        "last_caption_time": chunks[-1].end if chunks else None,
        "maximum_active_speech_caption_gap": round(_max_active_speech_caption_gap(final_words, chunks), 3),
        "caption_chunk_count": len(chunks),
        **timing_metrics,
    })
    if first_speech is not None and first_caption is not None:
        if first_caption < first_speech - 0.10:
            final_report["alignment_warnings"].append("First caption begins more than 0.10s before detected speech")
        if first_caption > first_speech + 0.25:
            final_report["alignment_warnings"].append("First caption begins more than 0.25s after detected speech")
    if final_report["maximum_active_speech_caption_gap"] > 0.5:
        final_report["alignment_warnings"].append("A caption gap exceeds 0.5s while a spoken word is active")
    if final_report["maximum_caption_tail_after_word"] > 0.20:
        final_report["alignment_warnings"].append("A caption remains visible too long after its final word")

    atomic_write_json(output / "subtitle_alignment_report.json", final_report)
    atomic_write_json(output / "aligned_words.json", [asdict(row) for row in final_words])
    atomic_write_json(output / "caption_chunks.json", [asdict(row) for row in chunks])
    write_ass(chunks, output / "captions.ass")
    create_debug_images(chunks, output)
    return final_words, chunks, final_report


def render_subtitle_test(audio_path: str | Path, captions_ass_path: str | Path, output_path: str | Path) -> None:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ass = str(Path(captions_ass_path).resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x171B26:s=1080x1920:r=30",
        "-i", str(audio_path), "-vf", f"subtitles='{ass}'", "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", "-movflags", "+faststart", str(target),
    ])


def create_debug_images(chunks: list[CaptionChunk], output_dir: str | Path, limit: int = 6) -> None:
    output = Path(output_dir)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 54)
    except OSError:
        font = ImageFont.load_default()
    for index, chunk in enumerate(chunks[:limit]):
        image = Image.new("RGB", (1080, 1920), (24, 28, 38))
        draw = ImageDraw.Draw(image)
        display = chunk.text
        box = draw.multiline_textbbox((0, 0), display, font=font, stroke_width=3, align="center")
        text_width = box[2] - box[0]
        text_height = box[3] - box[1]
        x = (1080 - text_width) // 2
        y = 1450 - text_height // 2
        pad_x, pad_y = 28, 18
        draw.rounded_rectangle((x - pad_x, y - pad_y, x + text_width + pad_x, y + text_height + pad_y), radius=18, fill=(0, 0, 0, 150))
        draw.multiline_text((x, y), display, font=font, fill="white", stroke_width=3, stroke_fill="black", align="center")
        draw.text((40, 40), f"{chunk.start:.2f}s - {chunk.end:.2f}s", font=ImageFont.load_default(), fill="white")
        image.save(output / f"caption_debug_{index:02d}.png")
