from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import requests

from .atomic_io import atomic_write_json

logger = logging.getLogger(__name__)

AUTHORITATIVE_DOMAINS = {
    "nasa.gov",
    "noaa.gov",
    "nih.gov",
    "who.int",
    "usgs.gov",
    "si.edu",
    "britannica.com",
    "nature.com",
    "science.org",
    "nationalgeographic.com",
    "nationalacademies.org",
    "pnas.org",
    "royalsociety.org",
    "aps.org",
    "acs.org",
    "edu",
    "gov",
    "ac.uk",
    "museum",
    "esa.int",
    "cern.ch",
    "openstax.org",
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


def verify_url_details(url: str, timeout: int = 8) -> tuple[bool, str, str]:
    """Verify a URL and expose its final redirect destination.

    Google Search grounding often returns a Google redirect URL. Authority must be
    evaluated against the resolved destination, not the redirect host.
    """
    if not url.startswith(("https://", "http://")):
        return False, "not an HTTP URL", url
    try:
        headers = {"User-Agent": "Mozilla/5.0 AutoShortsFactCheck/3.0"}
        with requests.get(url, timeout=timeout, allow_redirects=True, headers=headers, stream=True) as response:
            status = response.status_code
            resolved_url = str(response.url or url)
            ok = status < 500 and status not in {404, 410}
            return ok, f"HTTP {status}", resolved_url
    except requests.RequestException as exc:
        return False, str(exc), url


def verify_url(url: str, timeout: int = 8) -> tuple[bool, str]:
    reachable, detail, _ = verify_url_details(url, timeout=timeout)
    return reachable, detail


def build_fact_check(script_data: dict, output_path: str | Path, network_verify: bool = True) -> dict:
    rows = []
    all_valid = True
    for index, scene in enumerate(script_data.get("scenes", []), start=1):
        sources = scene.get("sources") or []
        if isinstance(sources, str):
            sources = [sources]
        source_rows = []
        for source in sources:
            source_url = str(source).strip()
            if network_verify:
                reachable, detail, resolved_url = verify_url_details(source_url)
            else:
                reachable, detail, resolved_url = True, "network verification skipped", source_url
            authoritative = is_authoritative(resolved_url)
            source_rows.append(
                {
                    "url": source_url,
                    "resolved_url": resolved_url,
                    "authoritative": authoritative,
                    "reachable": reachable,
                    "detail": detail,
                }
            )
        scene_valid = (
            bool(scene.get("claim"))
            and bool(source_rows)
            and any(row["authoritative"] and row["reachable"] for row in source_rows)
        )
        if not scene_valid:
            all_valid = False
        rows.append(
            {
                "scene": index,
                "claim": scene.get("claim", ""),
                "source_note": scene.get("source_note", ""),
                "sources": source_rows,
                "passed": scene_valid,
            }
        )
    report = {"passed": all_valid and len(rows) >= 4, "scene_count": len(rows), "claims": rows}
    atomic_write_json(output_path, report)
    return report
