from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import requests

from .atomic_io import atomic_write_json

logger = logging.getLogger(__name__)

# Tier A: primary institutions, governments, universities, scientific societies,
# museums, and peer-reviewed publishers. One reachable Tier A source is enough.
PRIMARY_DOMAINS = {
    "nasa.gov",
    "noaa.gov",
    "nih.gov",
    "who.int",
    "usgs.gov",
    "si.edu",
    "nationalacademies.org",
    "pnas.org",
    "royalsociety.org",
    "aps.org",
    "acs.org",
    "esa.int",
    "cern.ch",
    "openstax.org",
    "nature.com",
    "science.org",
    "edu",
    "gov",
    "ac.uk",
    "museum",
}

# Tier B: established science publications and reference publishers with editorial
# review. A scene needs two independent reachable Tier B domains when no Tier A
# source is available. This keeps hypothetical science topics usable without
# treating low-quality forums or generic news sites as evidence.
REPUTABLE_SCIENCE_DOMAINS = {
    "britannica.com",
    "nationalgeographic.com",
    "scientificamerican.com",
    "smithsonianmag.com",
    "space.com",
    "livescience.com",
    "astronomy.com",
    "newscientist.com",
    "phys.org",
    "sciencedaily.com",
    "bbc.com",
    "bbc.co.uk",
}

BLOCKED_DOMAINS = {
    "quora.com",
    "reddit.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "vedantu.com",
}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _matches_domain(host: str, domain: str) -> bool:
    if domain in {"edu", "gov", "museum"}:
        return host.endswith("." + domain)
    return host == domain or host.endswith("." + domain)


def is_authoritative(url: str) -> bool:
    host = _host(url)
    return bool(host) and any(_matches_domain(host, domain) for domain in PRIMARY_DOMAINS)


def is_reputable_science(url: str) -> bool:
    host = _host(url)
    if not host or any(_matches_domain(host, domain) for domain in BLOCKED_DOMAINS):
        return False
    return any(_matches_domain(host, domain) for domain in REPUTABLE_SCIENCE_DOMAINS)


def source_tier(url: str) -> str:
    if is_authoritative(url):
        return "primary"
    if is_reputable_science(url):
        return "reputable"
    return "rejected"


def evidence_domain(url: str) -> str:
    """Return the credibility-domain bucket used for independence counting."""
    host = _host(url)
    if not host:
        return ""
    for domain in PRIMARY_DOMAINS | REPUTABLE_SCIENCE_DOMAINS | BLOCKED_DOMAINS:
        if _matches_domain(host, domain):
            return domain
    return host.removeprefix("www.")


def verify_url_details(url: str, timeout: int = 10) -> tuple[bool, str, str]:
    """Verify a URL and expose its final redirect destination.

    Google Search grounding can return a redirect URL. Credibility is evaluated
    against the final destination. HTTP 401/403/405 still prove that a resource
    exists; 404/410 and server failures do not. TLS verification is never disabled.
    """
    if not url.startswith(("https://", "http://")):
        return False, "not an HTTP URL", url

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/124.0 Safari/537.36 AutoShortsFactCheck/5.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    }
    try:
        with requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers=headers,
            stream=True,
        ) as response:
            status = response.status_code
            resolved_url = str(response.url or url)
            reachable = status < 500 and status not in {404, 410}
            return reachable, f"HTTP {status}", resolved_url
    except requests.RequestException as exc:
        return False, str(exc), url


def verify_url(url: str, timeout: int = 10) -> tuple[bool, str]:
    reachable, detail, _ = verify_url_details(url, timeout=timeout)
    return reachable, detail


def _scene_evidence_passes(source_rows: list[dict]) -> tuple[bool, str]:
    reachable_primary = {
        row["evidence_domain"]
        for row in source_rows
        if row["reachable"] and row["tier"] == "primary" and row["evidence_domain"]
    }
    reachable_reputable = {
        row["evidence_domain"]
        for row in source_rows
        if row["reachable"] and row["tier"] == "reputable" and row["evidence_domain"]
    }

    if reachable_primary:
        return True, "one_or_more_reachable_primary_sources"
    if len(reachable_reputable) >= 2:
        return True, "two_or_more_independent_reputable_sources"
    if reachable_reputable:
        return False, "only_one_reputable_source_and_no_primary_source"
    return False, "no_reachable_accepted_source"


def build_fact_check(
    script_data: dict,
    output_path: str | Path,
    network_verify: bool = True,
) -> dict:
    rows = []
    all_valid = True

    for index, scene in enumerate(script_data.get("scenes", []), start=1):
        sources = scene.get("sources") or []
        if isinstance(sources, str):
            sources = [sources]

        source_rows: list[dict] = []
        seen_resolved: set[str] = set()
        for source in sources:
            source_url = str(source).strip()
            if network_verify:
                reachable, detail, resolved_url = verify_url_details(source_url)
            else:
                reachable, detail, resolved_url = True, "network verification skipped", source_url

            # Avoid counting duplicate redirects or repeated URLs twice.
            normalized_resolved = resolved_url.rstrip("/")
            if normalized_resolved in seen_resolved:
                continue
            seen_resolved.add(normalized_resolved)

            tier = source_tier(resolved_url)
            source_rows.append(
                {
                    "url": source_url,
                    "resolved_url": resolved_url,
                    "host": _host(resolved_url),
                    "evidence_domain": evidence_domain(resolved_url),
                    "tier": tier,
                    "authoritative": tier == "primary",
                    "reputable": tier == "reputable",
                    "reachable": reachable,
                    "detail": detail,
                }
            )

        evidence_passed, evidence_reason = _scene_evidence_passes(source_rows)
        scene_valid = bool(str(scene.get("claim", "")).strip()) and evidence_passed
        if not scene_valid:
            all_valid = False

        rows.append(
            {
                "scene": index,
                "claim": scene.get("claim", ""),
                "source_note": scene.get("source_note", ""),
                "sources": source_rows,
                "evidence_reason": evidence_reason,
                "passed": scene_valid,
            }
        )

    report = {
        "passed": all_valid and len(rows) >= 4,
        "scene_count": len(rows),
        "policy": {
            "primary_required": 1,
            "reputable_required_without_primary": 2,
            "independent_domains_required": True,
        },
        "claims": rows,
    }
    atomic_write_json(output_path, report)
    return report
