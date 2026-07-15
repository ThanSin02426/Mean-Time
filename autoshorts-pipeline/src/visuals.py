from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw

from .atomic_io import atomic_write_json, read_json
from .audio_utils import ffprobe

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VisualCandidate:
    provider: str
    candidate_id: str
    media_type: str
    url: str
    preview_url: str
    title: str
    tags: list[str]
    width: int
    height: int
    author: str
    source_url: str
    score: float = 0.0
    rejected_reason: str = ""


def _tokens(value: str | list[str]) -> set[str]:
    if isinstance(value, list):
        value = " ".join(value)
    return set(re.findall(r"[a-z0-9]+", str(value).lower()))


def score_candidate(candidate: dict[str, Any] | VisualCandidate, keywords: list[str], negative_terms: list[str], used_ids: set[str] | None = None) -> float:
    data = asdict(candidate) if isinstance(candidate, VisualCandidate) else candidate
    if used_ids and str(data.get("candidate_id")) in used_ids:
        return -100.0
    haystack = _tokens([str(data.get("title", "")), " ".join(data.get("tags", []) or [])])
    wanted = _tokens(keywords)
    negatives = _tokens(negative_terms)
    exact = len(haystack & wanted)
    negative_hits = len(haystack & negatives)
    width = int(data.get("width", 0) or 0)
    height = int(data.get("height", 0) or 0)
    orientation_bonus = 1.5 if height >= width and height >= 720 else 0.0
    resolution_bonus = 1.0 if max(width, height) >= 1080 else 0.0
    media_bonus = 0.4 if data.get("media_type") == "video" else 0.0
    return round(exact * 2.5 + orientation_bonus + resolution_bonus + media_bonus - negative_hits * 5.0, 3)


def _pexels_candidates(query: str) -> list[VisualCandidate]:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        return []
    headers = {"Authorization": key}
    candidates: list[VisualCandidate] = []
    response = requests.get(
        f"https://api.pexels.com/videos/search?query={quote(query)}&orientation=portrait&per_page=12",
        headers=headers, timeout=20,
    )
    if response.ok:
        for video in response.json().get("videos", []):
            files = [row for row in video.get("video_files", []) if row.get("link") and int(row.get("height", 0) or 0) >= 720]
            if not files:
                continue
            best = sorted(files, key=lambda row: (int(row.get("height", 0) or 0), int(row.get("width", 0) or 0)), reverse=True)[0]
            page_url = str(video.get("url", ""))
            slug = page_url.rstrip("/").split("/")[-1].replace("-", " ")
            candidates.append(VisualCandidate(
                "pexels", str(video.get("id")), "video", best["link"], video.get("image", ""),
                slug, list(_tokens(slug)), int(best.get("width", 0)), int(best.get("height", 0)),
                video.get("user", {}).get("name", ""), page_url,
            ))
    response = requests.get(
        f"https://api.pexels.com/v1/search?query={quote(query)}&orientation=portrait&per_page=12",
        headers=headers, timeout=20,
    )
    if response.ok:
        for photo in response.json().get("photos", []):
            candidates.append(VisualCandidate(
                "pexels", str(photo.get("id")), "image", photo.get("src", {}).get("large2x", ""), photo.get("src", {}).get("medium", ""),
                str(photo.get("alt", "")), list(_tokens(str(photo.get("alt", "")))), int(photo.get("width", 0)), int(photo.get("height", 0)),
                photo.get("photographer", ""), photo.get("url", ""),
            ))
    return candidates


def _pixabay_candidates(query: str) -> list[VisualCandidate]:
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key:
        return []
    candidates: list[VisualCandidate] = []
    response = requests.get(f"https://pixabay.com/api/videos/?key={key}&q={quote(query)}&safesearch=true&per_page=12", timeout=20)
    if response.ok:
        for hit in response.json().get("hits", []):
            files = hit.get("videos", {})
            chosen = files.get("large") or files.get("medium") or files.get("small") or {}
            if not chosen.get("url"):
                continue
            candidates.append(VisualCandidate(
                "pixabay", str(hit.get("id")), "video", chosen["url"], hit.get("picture_id", ""),
                hit.get("tags", query), [tag.strip() for tag in str(hit.get("tags", "")).split(",")],
                int(chosen.get("width", 0)), int(chosen.get("height", 0)), hit.get("user", ""), hit.get("pageURL", ""),
            ))
    response = requests.get(f"https://pixabay.com/api/?key={key}&q={quote(query)}&image_type=photo&safesearch=true&per_page=12", timeout=20)
    if response.ok:
        for hit in response.json().get("hits", []):
            candidates.append(VisualCandidate(
                "pixabay", str(hit.get("id")), "image", hit.get("largeImageURL", ""), hit.get("previewURL", ""),
                hit.get("tags", query), [tag.strip() for tag in str(hit.get("tags", "")).split(",")],
                int(hit.get("imageWidth", 0)), int(hit.get("imageHeight", 0)), hit.get("user", ""), hit.get("pageURL", ""),
            ))
    return candidates


def _nasa_candidates(query: str) -> list[VisualCandidate]:
    candidates: list[VisualCandidate] = []
    response = requests.get(f"https://images-api.nasa.gov/search?q={quote(query)}&media_type=image&page_size=20", timeout=20)
    if not response.ok:
        return candidates
    for item in response.json().get("collection", {}).get("items", []):
        data = (item.get("data") or [{}])[0]
        links = item.get("links") or []
        media_type = "image"
        preview = next((row.get("href") for row in links if row.get("href")), "")
        if not preview:
            continue
        candidates.append(VisualCandidate(
            "nasa", str(data.get("nasa_id", preview)), media_type, preview, preview,
            data.get("title", query), data.get("keywords", []) or [query], 1200, 1200,
            data.get("center", "NASA"), f"https://images.nasa.gov/details/{data.get('nasa_id', '')}",
        ))
    return candidates


def _download(url: str, path: Path, max_bytes: int = 80 * 1024 * 1024) -> None:
    downloaded = 0
    with requests.get(url, stream=True, timeout=45, headers={"User-Agent": "AutoShorts/2.0"}) as response:
        response.raise_for_status()
        declared = int(response.headers.get("content-length", 0) or 0)
        if declared and declared > max_bytes:
            raise RuntimeError(f"Media file is too large: {declared} bytes")
        with path.open("wb") as handle:
            for chunk in response.iter_content(1024 * 256):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise RuntimeError("Media download exceeded the size limit")
                handle.write(chunk)


def _valid_media(path: Path, media_type: str) -> bool:
    try:
        if media_type == "image":
            with Image.open(path) as image:
                width, height = image.size
            return width >= 640 and height >= 640
        data = ffprobe(path)
        stream = next((row for row in data.get("streams", []) if row.get("codec_type") == "video"), None)
        return bool(
            stream
            and int(stream.get("width", 0)) >= 640
            and int(stream.get("height", 0)) >= 640
            and float(data.get("format", {}).get("duration", 0) or 0) >= 0.25
        )
    except Exception:
        return False


def create_local_fallback(path: str | Path, seed_text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    top = tuple(20 + value // 4 for value in digest[:3])
    bottom = tuple(8 + value // 8 for value in digest[3:6])
    image = Image.new("RGB", (1080, 1920), top)
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(1920):
        ratio = y / 1919
        color = tuple(round(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, 1080, y), fill=color)
    for i in range(18):
        x = (digest[i % len(digest)] * 37 + i * 97) % 1080
        y = (digest[(i + 5) % len(digest)] * 53 + i * 131) % 1920
        radius = 40 + digest[(i + 9) % len(digest)]
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(255, 255, 255, 14), outline=(255, 255, 255, 28), width=2)
    image.save(target, quality=94)


def _recent_visual_ids(history_path: str | Path) -> set[str]:
    rows = read_json(history_path, [])
    result: set[str] = set()
    for row in rows[-20:]:
        if isinstance(row, dict):
            result.update(str(value) for value in row.get("visual_ids", []) if value)
    return result


def select_visuals(
    scenes: list[dict[str, Any]], niche: str, work_dir: str | Path, output_dir: str | Path,
    history_path: str | Path = "upload_history.json", min_score: float = 2.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    work = Path(work_dir)
    output = Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    attribution: list[dict[str, Any]] = []
    used_ids = _recent_visual_ids(history_path)
    current_ids: set[str] = set()
    current_hashes: set[str] = set()

    for index, scene in enumerate(scenes):
        query = str(scene.get("visual_query") or scene.get("visual_subject") or "abstract science")
        keywords = list(scene.get("scene_keywords") or []) + query.split()
        negatives = list(scene.get("visual_negative_terms") or [])
        providers = (_nasa_candidates, _pexels_candidates, _pixabay_candidates) if niche == "space" else (_pexels_candidates, _pixabay_candidates)
        candidates: list[VisualCandidate] = []
        for provider in providers:
            try:
                candidates.extend(provider(query))
            except requests.RequestException as exc:
                logger.warning("Visual provider failed for %s: %s", query, exc)
        deduped: dict[str, VisualCandidate] = {}
        for candidate in candidates:
            if not candidate.url:
                continue
            key = hashlib.sha256(candidate.url.encode()).hexdigest()
            if key not in deduped:
                candidate.score = score_candidate(candidate, keywords, negatives, used_ids | current_ids)
                if candidate.provider == "nasa" and niche == "space":
                    candidate.score += 1.0
                if candidate.media_type == str(scene.get("preferred_media_type", "video")):
                    candidate.score += 0.4
                if candidate.width and candidate.height and candidate.width / max(1, candidate.height) > 1.95:
                    candidate.rejected_reason = "landscape crop would discard too much of the frame"
                    candidate.score = -50.0
                elif min(candidate.width, candidate.height) < 640:
                    candidate.rejected_reason = "resolution below 640 pixels"
                    candidate.score = -50.0
                elif candidate.score < min_score:
                    candidate.rejected_reason = "metadata relevance score below threshold"
                deduped[key] = candidate
        ranked = sorted(deduped.values(), key=lambda row: row.score, reverse=True)
        all_candidates.extend([{**asdict(row), "scene_index": index} for row in ranked[:12]])

        chosen: VisualCandidate | None = None
        chosen_path: Path | None = None
        for candidate in ranked:
            if candidate.score < min_score or candidate.candidate_id in current_ids:
                continue
            suffix = ".mp4" if candidate.media_type == "video" else ".jpg"
            destination = work / f"scene_{index:02d}_{candidate.provider}_{candidate.candidate_id}{suffix}"
            try:
                _download(candidate.url, destination)
                if _valid_media(destination, candidate.media_type):
                    content_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
                    if content_hash in current_hashes:
                        candidate.rejected_reason = "duplicate media content"
                        destination.unlink(missing_ok=True)
                        continue
                    current_hashes.add(content_hash)
                    chosen, chosen_path = candidate, destination
                    break
                destination.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Candidate download rejected: %s", exc)
                destination.unlink(missing_ok=True)
        if chosen is None or chosen_path is None:
            chosen_path = work / f"scene_{index:02d}_local.jpg"
            create_local_fallback(chosen_path, str(scene.get("visual_subject") or query))
            chosen = VisualCandidate(
                "local", f"local-{index}-{hashlib.sha1(query.encode()).hexdigest()[:10]}", "image", "", "",
                str(scene.get("visual_subject") or query), keywords, 1080, 1920, "AutoShorts local fallback", "local", score=min_score,
            )
        current_ids.add(chosen.candidate_id)
        row = {
            **asdict(chosen), "path": str(chosen_path), "scene_index": index,
            "visual_subject": scene.get("visual_subject", ""), "visual_query": query,
        }
        selected.append(row)
        attribution.append({
            "scene_index": index, "provider": chosen.provider, "author": chosen.author,
            "source_url": chosen.source_url, "candidate_id": chosen.candidate_id,
            "license_note": "Provider terms apply" if chosen.provider != "local" else "Generated locally by the pipeline",
        })
    atomic_write_json(output / "visual_candidates.json", all_candidates)
    atomic_write_json(output / "media_attribution.json", attribution)
    create_contact_sheet(selected, output / "visual_contact_sheet.jpg")
    return selected, all_candidates, attribution


def create_contact_sheet(selected: list[dict[str, Any]], output_path: str | Path) -> None:
    thumbs: list[Image.Image] = []
    for row in selected:
        path = Path(row["path"])
        try:
            if row["media_type"] == "video":
                frame = path.with_suffix(".contact.jpg")
                subprocess.run([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "0.4", "-i", str(path),
                    "-frames:v", "1", "-vf", "scale=270:480:force_original_aspect_ratio=increase,crop=270:480", str(frame),
                ], check=True)
                image = Image.open(frame).convert("RGB")
            else:
                image = Image.open(path).convert("RGB").resize((270, 480))
            thumbs.append(image.copy())
        except Exception:
            continue
    if not thumbs:
        return
    sheet = Image.new("RGB", (270 * len(thumbs), 480), (12, 12, 16))
    for index, image in enumerate(thumbs):
        sheet.paste(image.resize((270, 480)), (index * 270, 0))
    sheet.save(output_path, quality=92)
