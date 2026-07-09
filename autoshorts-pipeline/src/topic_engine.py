import json
import logging
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_ANALYTICS_AGE_HOURS = int(os.environ.get("MIN_ANALYTICS_AGE_HOURS", "48") or 48)
PREFERRED_ANALYTICS_AGE_HOURS = int(os.environ.get("PREFERRED_ANALYTICS_AGE_HOURS", "72") or 72)
NICHE_REPEAT_DISTANCE = int(os.environ.get("NICHE_REPEAT_DISTANCE", "10") or 10)
EXPLORATION_RATE = float(os.environ.get("EXPLORATION_RATE", "0.20") or 0.20)

CATEGORY_KEYWORDS = {
    "space": ["space", "planet", "galaxy", "black hole", "nasa", "moon", "mars", "asteroid", "universe", "star", "solar"],
    "ocean": ["ocean", "sea", "deep sea", "marine", "shark", "whale", "abyss"],
    "animals": ["animal", "animals", "wildlife", "creature", "predator", "birds", "insects", "deadliest"],
    "history": ["history", "ancient", "civilization", "empire", "war", "king", "queen", "wonder"],
    "psychology": ["psychology", "brain", "mind", "habit", "human behavior", "body language"],
    "science": ["science", "physics", "quantum", "gravity", "time", "energy", "body", "rare"],
    "mystery": ["mystery", "unsolved", "strange", "creepy", "forbidden", "lost"],
    "places": ["places", "earth", "city", "country", "island", "desert", "mountain"],
}

# Demand-oriented seed topics. The engine exploits high-scoring niches but still rotates categories.
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
        "3 neutron star facts that sound illegal",
    ],
    "ocean": [
        "3 deep ocean facts that sound fake",
        "3 terrifying sea creatures you will not believe exist",
        "3 ocean mysteries scientists still cannot explain",
        "3 deep sea facts scarier than space",
        "3 shark facts that are misunderstood",
        "3 whale facts that feel impossible",
        "3 hidden ocean places that look unreal",
        "3 underwater discoveries that changed science",
        "3 ocean survival facts everyone should know",
        "3 abyss facts that feel like a nightmare",
    ],
    "animals": [
        "3 animal facts that sound fake",
        "3 dangerous animal facts you should know",
        "3 wildlife facts that are hard to believe",
        "3 predator facts that feel unreal",
        "3 insect facts that will shock you",
        "3 bird facts that sound impossible",
        "3 animal survival tricks that are genius",
        "3 weird creature facts you will not forget",
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
    "science": [
        "3 science facts that feel impossible",
        "3 time facts that will bend your brain",
        "3 gravity facts that feel fake",
        "3 quantum facts that sound unreal",
        "3 rare body facts that sound fake",
        "3 light facts that are hard to believe",
        "3 physics facts that sound impossible",
        "3 temperature facts that sound fake",
        "3 weird science facts you will remember",
        "3 everyday science facts that feel like magic",
    ],
    "mystery": [
        "3 unsolved mysteries that still feel creepy",
        "3 lost technologies that sound impossible",
        "3 forbidden history facts people ignore",
        "3 strange internet mysteries that are still unsolved",
        "3 creepy natural phenomena caught on camera",
        "3 mystery facts that make no sense at first",
        "3 weird disappearances that still confuse people",
        "3 hidden facts that feel like a movie",
        "3 strange discoveries scientists still debate",
        "3 facts that sound like conspiracy but are real",
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
}

DEFAULT_NICHE_SCORES = {
    "space": 1.30,
    "ocean": 1.22,
    "animals": 1.15,
    "history": 1.10,
    "mystery": 1.08,
    "science": 1.00,
    "psychology": 0.94,
    "places": 0.90,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(path: str, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Could not read {path}: {exc}")
        return default


def _json_save(path: str, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clean_topic_line(line: str) -> str:
    return re.sub(r"\s+", " ", str(line or "").strip(" \t\n\r-•*0123456789.()[]"))


def categorize_topic(topic: str) -> str:
    low = str(topic or "").lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(k in low for k in keywords):
            return category
    return "mystery"


def _default_seed_queue(min_count: int = 30) -> List[str]:
    """Demand-oriented seed queue used if topics.txt is missing/empty."""
    order = ["space", "ocean", "animals", "history", "mystery", "science", "places", "psychology"]
    topics: List[str] = []
    idx = 0
    while len(topics) < min_count:
        niche = order[idx % len(order)]
        templates = TOPIC_TEMPLATES.get(niche, [])
        if templates:
            topics.append(templates[(idx // len(order)) % len(templates)])
        idx += 1
    return topics


def read_topics(topics_file: str) -> List[str]:
    if not os.path.exists(topics_file):
        logger.warning(f"Topics file not found: {topics_file}. Creating a fresh demand-oriented queue.")
        seed = _default_seed_queue()
        write_topics(topics_file, seed)
        return seed
    topics = [clean_topic_line(line) for line in Path(topics_file).read_text(encoding="utf-8").splitlines()]
    topics = [t for t in topics if t]
    if not topics:
        logger.warning(f"Topics file is empty: {topics_file}. Refilling it with demand-oriented seed topics.")
        topics = _default_seed_queue()
        write_topics(topics_file, topics)
    return topics


def write_topics(topics_file: str, topics: List[str]) -> None:
    seen = set()
    clean = []
    for t in topics:
        t = clean_topic_line(t)
        key = t.lower()
        if t and key not in seen:
            clean.append(t)
            seen.add(key)
    Path(topics_file).write_text("\n".join(clean) + "\n", encoding="utf-8")


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def update_niche_scores_from_history() -> Dict[str, Dict]:
    history = _json_load("analytics_history.json", [])
    matured = []
    now = datetime.now(timezone.utc)
    for item in history:
        published_at = _parse_dt(item.get("published_at"))
        if not published_at:
            continue
        age_hours = (now - published_at).total_seconds() / 3600.0
        if age_hours >= MIN_ANALYTICS_AGE_HOURS:
            matured.append(item)

    grouped: Dict[str, List[Dict]] = {}
    for item in matured:
        niche = item.get("niche") or categorize_topic(item.get("topic") or item.get("title") or "")
        grouped.setdefault(niche, []).append(item)

    scores = {}
    for niche in TOPIC_TEMPLATES:
        rows = grouped.get(niche, [])[-20:]
        if rows:
            views = [float(r.get("views") or 0) for r in rows]
            likes = [float(r.get("likes") or 0) for r in rows]
            avg_views = sum(views) / len(views)
            like_rate = (sum(likes) / max(1.0, sum(views))) if views else 0.0
            score = avg_views * (1.0 + min(like_rate * 8.0, 0.7))
        else:
            avg_views = 0.0
            like_rate = 0.0
            score = DEFAULT_NICHE_SCORES.get(niche, 0.8)
        scores[niche] = {
            "niche": niche,
            "recent_avg_views": round(avg_views, 2),
            "recent_avg_like_rate": round(like_rate, 4),
            "sample_size": len(rows),
            "score": round(score, 4),
            "updated_at": now_iso(),
            "min_analytics_age_hours": MIN_ANALYTICS_AGE_HOURS,
        }
    _json_save("niche_scores.json", scores)
    return scores


def _recent_niches() -> List[str]:
    state = _json_load("topic_state.json", {"recent_niches": [], "used_topics": []})
    return list(state.get("recent_niches", []))[-NICHE_REPEAT_DISTANCE:]


def _select_niche(selected_topic: str, current_topics: Optional[List[str]] = None) -> Tuple[str, str]:
    scores = update_niche_scores_from_history()
    recent = _recent_niches()
    # Avoid immediately appending the same niche as the topic just used.
    selected_niche = categorize_topic(selected_topic)
    protected_recent = list(dict.fromkeys((recent + [selected_niche])[-NICHE_REPEAT_DISTANCE:]))
    current_topics = current_topics or []
    current_niches = [categorize_topic(t) for t in current_topics[:max(3, NICHE_REPEAT_DISTANCE // 2)]]
    exploration = random.random() < EXPLORATION_RATE
    if exploration:
        candidates = [n for n in TOPIC_TEMPLATES if n not in protected_recent and n not in current_niches] or [n for n in TOPIC_TEMPLATES if n not in protected_recent] or list(TOPIC_TEMPLATES)
        return random.choice(candidates), "exploration"

    ranked = sorted(scores.values(), key=lambda x: x.get("score", 0), reverse=True)
    for row in ranked:
        niche = row["niche"]
        if niche not in protected_recent and niche not in current_niches:
            return niche, "analytics_exploitation"
    for row in ranked:
        niche = row["niche"]
        if niche not in protected_recent:
            return niche, "analytics_exploitation_relaxed"
    # If every niche appears in the recent window, use the selected topic's niche as a safe fallback.
    return random.choice([n for n in TOPIC_TEMPLATES if n != selected_niche] or list(TOPIC_TEMPLATES)), "repeat_distance_fallback"


def _pick_topic_from_niche(niche: str, existing_topics: List[str], selected_topic: str) -> str:
    templates = TOPIC_TEMPLATES.get(niche) or TOPIC_TEMPLATES["mystery"]
    existing = {t.lower().strip() for t in existing_topics}
    used_state = _json_load("topic_state.json", {"used_topics": []})
    recently_used = {t.lower().strip() for t in used_state.get("used_topics", [])[-40:]}
    start = abs(hash(selected_topic + niche + now_iso()[:10])) % len(templates)
    for offset in range(len(templates)):
        candidate = templates[(start + offset) % len(templates)]
        ck = candidate.lower().strip()
        if ck not in existing and ck not in recently_used and ck != selected_topic.lower().strip():
            return candidate
    for candidate in templates:
        ck = candidate.lower().strip()
        if ck not in existing and ck != selected_topic.lower().strip():
            return candidate
    return f"3 {niche} facts that sound unreal {random.randint(100,999)}"


def _record_topic_bank(topic: str, source: str = "queue", status: str = "active") -> None:
    bank = _json_load("topic_bank.json", [])
    key = topic.lower().strip()
    found = False
    for row in bank:
        if row.get("topic", "").lower().strip() == key:
            row["last_seen_at"] = now_iso()
            row["times_used"] = int(row.get("times_used", 0)) + (1 if source == "used" else 0)
            row["status"] = status or row.get("status", "active")
            found = True
            break
    if not found:
        bank.append({
            "topic": topic,
            "niche": categorize_topic(topic),
            "angle": "facts/reveals",
            "created_at": now_iso(),
            "last_seen_at": now_iso(),
            "source": source,
            "status": status,
            "times_used": 1 if source == "used" else 0,
        })
    _json_save("topic_bank.json", bank[-500:])


def _update_topic_state(selected: str, replacement: str, replacement_niche: str, mode: str) -> None:
    state = _json_load("topic_state.json", {"recent_niches": [], "used_topics": [], "events": []})
    selected_niche = categorize_topic(selected)
    state.setdefault("recent_niches", []).append(selected_niche)
    state["recent_niches"] = state["recent_niches"][-NICHE_REPEAT_DISTANCE:]
    state.setdefault("used_topics", []).append(selected)
    state["used_topics"] = state["used_topics"][-80:]
    state.setdefault("events", []).append({
        "at": now_iso(),
        "selected_topic": selected,
        "selected_niche": selected_niche,
        "replacement_topic": replacement,
        "replacement_niche": replacement_niche,
        "mode": mode,
    })
    state["events"] = state["events"][-200:]
    _json_save("topic_state.json", state)


def pop_topic_and_refresh_queue(topics_file: str) -> str:
    # Best-effort analytics sync. It never blocks video generation.
    try:
        if os.environ.get("ENABLE_ANALYTICS_SYNC", "false").lower() in {"1", "true", "yes"}:
            sync_analytics_if_possible()
    except Exception as exc:
        logger.warning(f"Analytics sync skipped: {exc}")

    topics = read_topics(topics_file)
    if not topics:
        raise RuntimeError(f"No topics found in {topics_file}")

    selected = topics.pop(0)
    replacement_niche, mode = _select_niche(selected, topics)
    replacement = _pick_topic_from_niche(replacement_niche, topics, selected)
    topics.append(replacement)
    write_topics(topics_file, topics)

    _record_topic_bank(selected, source="used", status="active")
    _record_topic_bank(replacement, source=mode, status=("exploration" if mode == "exploration" else "active"))
    _update_topic_state(selected, replacement, replacement_niche, mode)

    logger.info(f"Popped topic from queue: '{selected}'")
    logger.info(f"Appended replacement topic: '{replacement}' [{replacement_niche}, {mode}]")
    logger.info(f"Topic queue now has {len(topics)} topics. Next queued topic: '{topics[0] if topics else '<empty>'}'")
    return selected


def record_uploaded_video(video_url: str, title: str, topic: str) -> None:
    video_id = str(video_url).rstrip("/").split("/")[-1]
    history = _json_load("upload_history.json", [])
    history.append({
        "video_id": video_id,
        "url": video_url,
        "title": title,
        "topic": topic,
        "niche": categorize_topic(topic),
        "published_at": now_iso(),
        "analytics_mature_after": datetime.now(timezone.utc).timestamp() + MIN_ANALYTICS_AGE_HOURS * 3600,
    })
    _json_save("upload_history.json", history[-1000:])


def sync_analytics_if_possible() -> None:
    """Best-effort YouTube Data API stats sync. Never raises to callers."""
    uploads = _json_load("upload_history.json", [])
    if not uploads:
        update_niche_scores_from_history()
        return

    now = datetime.now(timezone.utc)
    matured_uploads = []
    for row in uploads:
        published = _parse_dt(row.get("published_at"))
        if published and (now - published).total_seconds() / 3600.0 >= MIN_ANALYTICS_AGE_HOURS:
            matured_uploads.append(row)
    if not matured_uploads:
        logger.info(f"No videos older than {MIN_ANALYTICS_AGE_HOURS}h yet; analytics scoring unchanged.")
        update_niche_scores_from_history()
        return

    ids = [r.get("video_id") for r in matured_uploads if r.get("video_id")]
    if not ids:
        update_niche_scores_from_history()
        return

    try:
        from src.uploader import get_authenticated_service
        youtube = get_authenticated_service()
        existing = _json_load("analytics_history.json", [])
        by_id = {row.get("video_id"): row for row in existing if row.get("video_id")}
        for i in range(0, len(ids), 50):
            batch = ids[i:i+50]
            resp = youtube.videos().list(part="statistics,snippet,contentDetails", id=",".join(batch)).execute()
            upload_map = {r.get("video_id"): r for r in matured_uploads}
            for item in resp.get("items", []):
                vid = item.get("id")
                stats = item.get("statistics", {})
                base = upload_map.get(vid, {})
                by_id[vid] = {
                    "video_id": vid,
                    "title": base.get("title") or item.get("snippet", {}).get("title"),
                    "topic": base.get("topic") or item.get("snippet", {}).get("title"),
                    "niche": base.get("niche") or categorize_topic(base.get("topic") or item.get("snippet", {}).get("title")),
                    "published_at": base.get("published_at") or item.get("snippet", {}).get("publishedAt"),
                    "views": int(stats.get("viewCount", 0) or 0),
                    "likes": int(stats.get("likeCount", 0) or 0),
                    "comments": int(stats.get("commentCount", 0) or 0),
                    "pulled_at": now_iso(),
                    "source": "youtube_data_api",
                }
        _json_save("analytics_history.json", list(by_id.values())[-1000:])
        update_niche_scores_from_history()
        logger.info("Analytics sync completed.")
    except Exception as exc:
        logger.warning(f"YouTube analytics sync failed; using existing local scores only: {exc}")
        update_niche_scores_from_history()
