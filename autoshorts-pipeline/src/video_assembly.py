from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from .audio_utils import duration_seconds, ffprobe, run_command
from .subtitles import WordTiming, script_tokens

logger = logging.getLogger(__name__)


def allocate_scene_durations(scenes: list[dict[str, Any]], total_duration: float) -> list[float]:
    if not scenes:
        raise ValueError("At least one scene is required")
    weights = [max(1, len(str(scene.get("narration", "")).split())) for scene in scenes]
    weight_sum = sum(weights)
    raw = [total_duration * weight / weight_sum for weight in weights]
    minimum = min(2.5, total_duration / len(scenes))
    adjusted = [max(minimum, value) for value in raw]
    scale = total_duration / sum(adjusted)
    durations = [round(value * scale, 3) for value in adjusted]
    durations[-1] = round(total_duration - sum(durations[:-1]), 3)
    return durations



def allocate_scene_durations_from_alignment(
    scenes: list[dict[str, Any]], aligned_words: list[WordTiming], total_duration: float
) -> list[float]:
    """Use the canonical narration alignment to place visual cuts at scene speech boundaries."""
    if not scenes or not aligned_words:
        return allocate_scene_durations(scenes, total_duration)
    counts = [len(script_tokens(str(scene.get("narration", "")))) for scene in scenes]
    if sum(counts) != len(aligned_words) or any(count <= 0 for count in counts):
        logger.warning("Scene/script token counts do not match aligned words; using proportional allocation")
        return allocate_scene_durations(scenes, total_duration)
    boundaries = [0.0]
    cursor = 0
    for count in counts[:-1]:
        cursor += count
        boundaries.append(min(total_duration, max(boundaries[-1], aligned_words[cursor - 1].end)))
    boundaries.append(total_duration)
    durations = [round(boundaries[index + 1] - boundaries[index], 3) for index in range(len(scenes))]
    if any(duration < 1.0 for duration in durations):
        logger.warning("Alignment-derived scene duration was too short; using proportional allocation")
        return allocate_scene_durations(scenes, total_duration)
    durations[-1] = round(total_duration - sum(durations[:-1]), 3)
    return durations


def _scale_filter(width: int, height: int) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps=30,format=yuv420p"
    )


def _image_motion_filter(
    width: int,
    height: int,
    duration: float,
    motion_variant: int = 0,
) -> str:
    """Create a subtle deterministic Ken Burns zoom for still-image scenes."""
    fps = 30
    frame_count = max(2, int(round(duration * fps)))
    denominator = max(1, frame_count - 1)

    # Overscan before zooming so the motion never reveals empty borders.
    overscan = 1.12
    scaled_width = int(width * overscan + 1) // 2 * 2
    scaled_height = int(height * overscan + 1) // 2 * 2

    if motion_variant % 2 == 0:
        zoom = f"1.0+0.08*min(on/{denominator},1)"
    else:
        zoom = f"1.08-0.08*min(on/{denominator},1)"

    return (
        f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=increase,"
        f"crop={scaled_width}:{scaled_height},"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={width}x{height}:fps={fps},"
        "setsar=1,format=yuv420p"
    )


def prepare_visual_segment(
    source_path: str | Path,
    media_type: str,
    duration: float,
    output_path: str | Path,
    width: int = 1080,
    height: int = 1920,
    motion_variant: int = 0,
) -> None:
    source = Path(source_path)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if media_type == "image":
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-framerate", "30", "-i", str(source),
            "-t", f"{duration:.3f}",
            "-vf", _image_motion_filter(width, height, duration, motion_variant),
            "-an", "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", str(target),
        ]
    else:
        source_duration = duration_seconds(source)
        input_options: list[str]
        if source_duration > duration + 0.5:
            # Skip stock intros/outros and take a deterministic central subclip.
            offset = max(0.0, (source_duration - duration) * 0.5)
            input_options = ["-ss", f"{offset:.3f}", "-i", str(source)]
        else:
            input_options = ["-stream_loop", "-1", "-i", str(source)]
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *input_options,
            "-t", f"{duration:.3f}", "-vf", _scale_filter(width, height), "-an", "-r", "30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", str(target),
        ]
    run_command(command)
    actual = duration_seconds(target)
    if abs(actual - duration) > 0.20:
        raise RuntimeError(f"Prepared visual duration mismatch: expected {duration:.3f}s, got {actual:.3f}s")


def _concat_segments(paths: list[Path], concat_path: Path, output_path: Path) -> None:
    concat_path.write_text("".join(f"file '{path.resolve().as_posix()}'\n" for path in paths), encoding="utf-8")
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat_path), "-c", "copy", str(output_path),
    ])


def choose_music(music_dir: str | Path, mood: str = "neutral") -> tuple[Path | None, dict[str, Any] | None]:
    directory = Path(music_dir)
    metadata_path = directory / "music_library.json"
    if not directory.exists():
        return None, None
    metadata = {}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            metadata = {}
    tracks = sorted([path for path in directory.iterdir() if path.suffix.lower() in {".mp3", ".wav", ".m4a"}])
    if not tracks:
        return None, None
    preferred = [path for path in tracks if mood.lower() in str(metadata.get(path.name, {}).get("mood", "")).lower()]
    track = (preferred or tracks)[0]
    info = metadata.get(track.name, {})
    return track, {
        "path": str(track), "title": info.get("title", track.stem), "source": info.get("source", "local licensed track"),
        "license": info.get("license", "User is responsible for confirming the committed track licence"), "mood": info.get("mood", mood),
    }


def assemble_video(
    narration_path: str | Path,
    scenes: list[dict[str, Any]],
    selected_visuals: list[dict[str, Any]],
    captions_ass_path: str | Path,
    output_path: str | Path,
    work_dir: str | Path,
    music_dir: str | Path = "assets/music",
    mood: str = "neutral",
    width: int = 1080,
    height: int = 1920,
    scene_durations: list[float] | None = None,
) -> tuple[float, dict[str, Any] | None]:
    if len(selected_visuals) != len(scenes):
        raise ValueError("Selected visual count must match scene count")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    narration_duration = duration_seconds(narration_path)
    if narration_duration <= 0:
        raise RuntimeError("Narration has no measurable duration")
    durations = scene_durations or allocate_scene_durations(scenes, narration_duration)
    if len(durations) != len(scenes) or abs(sum(durations) - narration_duration) > 0.10:
        raise ValueError("Scene durations must match scene count and canonical narration duration")
    segment_paths: list[Path] = []
    for index, (visual, duration) in enumerate(zip(selected_visuals, durations)):
        segment_path = work / f"segment_{index:02d}.mp4"
        prepare_visual_segment(
            visual["path"], visual["media_type"], duration, segment_path, width, height,
            motion_variant=index,
        )
        segment_paths.append(segment_path)
    visuals_concat = work / "visuals_concat.mp4"
    _concat_segments(segment_paths, work / "concat.txt", visuals_concat)

    music_path, music_meta = choose_music(music_dir, mood)
    ass = str(Path(captions_ass_path).resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    video_filter = f"subtitles='{ass}'"
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(visuals_concat), "-i", str(narration_path),
    ]
    if music_path:
        command += ["-stream_loop", "-1", "-i", str(music_path)]
        fade_out_start = max(0.0, narration_duration - 1.2)
        filter_complex = (
            f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
            f"[2:a]volume=0.075,afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out_start:.3f}:d=1.0[music];"
            f"[voice][music]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
    else:
        filter_complex = "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
    command += ["-filter_complex", filter_complex, "-map", "0:v:0", "-map", "[aout]"]
    command += [
        "-vf", video_filter, "-t", f"{narration_duration:.3f}", "-r", "30", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", "-shortest", str(output),
    ]
    run_command(command)
    final_duration = duration_seconds(output)
    if abs(final_duration - narration_duration) > 0.30:
        raise RuntimeError(f"Final duration differs from narration by {abs(final_duration - narration_duration):.3f}s")
    return final_duration, music_meta


def smoke_render(output_path: str | Path, duration: float = 3.0, width: int = 360, height: int = 640) -> dict[str, Any]:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=0x223344:s={width}x{height}:r=30:d={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(target),
    ])
    data = ffprobe(target)
    video = next(row for row in data["streams"] if row.get("codec_type") == "video")
    audio = next(row for row in data["streams"] if row.get("codec_type") == "audio")
    return {
        "path": str(target), "duration": float(data["format"]["duration"]),
        "width": int(video["width"]), "height": int(video["height"]),
        "video_codec": video["codec_name"], "audio_codec": audio["codec_name"],
    }
