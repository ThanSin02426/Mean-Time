from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import requests

from .atomic_io import atomic_write_json

logger = logging.getLogger(__name__)

AUTHORITATIVE_DOMAINS = {
    "nasa.gov", "noaa.gov", "nih.gov", "who.int", "usgs.gov", "si.edu",
    "britannica.com", "nature.com", "science.org", "nationalgeographic.com",
    "nationalacademies.org", "pnas.org", "royalsociety.org", "aps.org", "acs.org",
    "edu", "gov", "ac.uk", "museum", "esa.int", "cern.ch",
}


def is_authoritative(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    for domain in AUTHORITATIVE_DOMAINS:
        if domain in {"edu", "gov", "museum"}:
            if host.endswith("." + domain):
                return True
        elif host == domain or host.endswith("." + domain):
            return True
    return False


def verify_url(url: str, timeout: int = 8) -> tuple[bool, str]:
    if not url.startswith(("https://", "http://")):
        return False, "not an HTTP URL"
    try:
        headers = {"User-Agent": "Mozilla/5.0 AutoShortsFactCheck/2.0"}
        with requests.get(url, timeout=timeout, allow_redirects=True, headers=headers, stream=True) as response:
            status = response.status_code
        ok = status < 500 and status not in {404, 410}
        return ok, f"HTTP {status}"
    except requests.RequestException as exc:
        return False, str(exc)


def build_fact_check(script_data: dict, output_path: str | Path, network_verify: bool = True) -> dict:
    rows = []
    all_valid = True
    for index, scene in enumerate(script_data.get("scenes", []), start=1):
        sources = scene.get("sources") or []
        if isinstance(sources, str):
            sources = [sources]
        source_rows = []
        for source in sources:
            authoritative = is_authoritative(source)
            reachable, detail = verify_url(source) if network_verify else (True, "network verification skipped")
            source_rows.append({"url": source, "authoritative": authoritative, "reachable": reachable, "detail": detail})
        scene_valid = bool(scene.get("claim")) and bool(source_rows) and any(row["authoritative"] and row["reachable"] for row in source_rows)
        if not scene_valid:
            all_valid = False
        rows.append({
            "scene": index,
            "claim": scene.get("claim", ""),
            "source_note": scene.get("source_note", ""),
            "sources": source_rows,
            "passed": scene_valid,
        })
    report = {"passed": all_valid and len(rows) >= 4, "scene_count": len(rows), "claims": rows}
    atomic_write_json(output_path, report)
    return report
