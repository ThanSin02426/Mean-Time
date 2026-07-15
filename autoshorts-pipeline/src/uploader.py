from __future__ import annotations

import argparse
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class YouTubeAuthenticationError(RuntimeError):
    pass


class YouTubeUploadError(RuntimeError):
    pass


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    values = {
        "client_id": os.getenv("YOUTUBE_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("YOUTUBE_CLIENT_SECRET", "").strip(),
        "refresh_token": os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise YouTubeAuthenticationError(f"Missing YouTube OAuth secrets: {', '.join(missing)}")
    credentials = Credentials(
        token=None,
        refresh_token=values["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=values["client_id"],
        client_secret=values["client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    try:
        credentials.refresh(Request())
    except Exception as exc:
        message = str(exc)
        if "invalid_grant" in message or "expired or revoked" in message.lower():
            raise YouTubeAuthenticationError(
                "Permanent OAuth failure: refresh token is expired or revoked. Generate a new token after setting the OAuth app to In production."
            ) from exc
        raise YouTubeAuthenticationError(f"OAuth preflight failed: {exc}") from exc
    return credentials


def check_auth() -> None:
    credentials = _credentials()
    if not credentials.valid:
        raise YouTubeAuthenticationError("OAuth credentials did not become valid")
    logger.info("YouTube OAuth preflight passed")


def upload_video(path: str | Path, title: str, description: str, tags: list[str]) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    credentials = _credentials()
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    body = {
        "snippet": {"title": title[:100], "description": description, "tags": tags[:30], "categoryId": "28"},
        "status": {"privacyStatus": os.getenv("YOUTUBE_PRIVACY_STATUS", "public"), "selfDeclaredMadeForKids": False},
    }
    request = youtube.videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True),
    )
    retries = 0
    response: dict[str, Any] | None = None
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as exc:
            if exc.resp.status in {500, 502, 503, 504} and retries < 5:
                delay = min(32, (2 ** retries) + random.random())
                retries += 1
                logger.warning("Transient YouTube upload error; retrying in %.1fs", delay)
                time.sleep(delay)
                continue
            raise YouTubeUploadError(f"YouTube upload failed: {exc}") from exc
    video_id = response.get("id")
    if not video_id:
        raise YouTubeUploadError(f"YouTube response did not contain a video ID: {response}")
    return f"https://youtu.be/{video_id}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-auth", action="store_true")
    args = parser.parse_args()
    if args.check_auth:
        check_auth()


if __name__ == "__main__":
    main()
