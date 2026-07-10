import argparse
import logging
import os
import random
import socket
import time
from typing import Optional

import httplib2
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

MAX_RETRIES = int(os.environ.get("YOUTUBE_UPLOAD_MAX_RETRIES", "5"))
RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class YouTubeAuthenticationError(RuntimeError):
    """Raised when the YouTube OAuth credentials cannot be refreshed."""


def _credential_values() -> tuple[str, str, str]:
    client_id = (os.environ.get("YOUTUBE_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("YOUTUBE_CLIENT_SECRET") or "").strip()
    refresh_token = (os.environ.get("YOUTUBE_REFRESH_TOKEN") or "").strip()

    missing = [
        name
        for name, value in (
            ("YOUTUBE_CLIENT_ID", client_id),
            ("YOUTUBE_CLIENT_SECRET", client_secret),
            ("YOUTUBE_REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise YouTubeAuthenticationError(
            "Missing YouTube OAuth secrets: " + ", ".join(missing)
        )
    return client_id, client_secret, refresh_token


def _build_credentials() -> Credentials:
    client_id, client_secret, refresh_token = _credential_values()
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )


def _friendly_refresh_error(exc: Exception) -> YouTubeAuthenticationError:
    message = str(exc)
    lower = message.lower()
    if "invalid_grant" in lower or "expired or revoked" in lower:
        return YouTubeAuthenticationError(
            "YouTube OAuth refresh token is expired or revoked (invalid_grant). "
            "Do not retry this upload. Set the Google OAuth consent screen to "
            "In production, generate a NEW refresh token with the same Web OAuth "
            "client in Google OAuth Playground, and replace the GitHub secret "
            "YOUTUBE_REFRESH_TOKEN."
        )
    return YouTubeAuthenticationError(
        f"YouTube OAuth authentication failed: {message}"
    )


def check_youtube_auth() -> Credentials:
    """
    Validate the refresh token before expensive video rendering.

    This performs an actual token refresh. An invalid/revoked token fails quickly
    instead of wasting several minutes generating a video that cannot be uploaded.
    """
    logger.info("Preflight: validating YouTube OAuth refresh token...")
    creds = _build_credentials()
    try:
        creds.refresh(GoogleAuthRequest())
    except RefreshError as exc:
        raise _friendly_refresh_error(exc) from exc
    except Exception as exc:
        raise YouTubeAuthenticationError(
            f"Could not validate YouTube OAuth credentials: {exc}"
        ) from exc

    if not creds.valid or not creds.token:
        raise YouTubeAuthenticationError(
            "YouTube OAuth token refresh returned no valid access token."
        )
    logger.info("Preflight: YouTube OAuth credentials are valid.")
    return creds


def get_authenticated_service():
    """Build a YouTube Data API client from validated OAuth credentials."""
    creds = check_youtube_auth()
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _retry_delay_seconds(retry_number: int, retry_after: Optional[str] = None) -> float:
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    base = min(2 ** retry_number, 32)
    return base + random.uniform(0.0, 1.0)


def resumable_upload(request):
    """Execute a resumable upload, retrying only transient failures."""
    response = None
    retry = 0

    while response is None:
        try:
            logger.info("Uploading video...")
            status, response = request.next_chunk()
            if status:
                logger.info("Uploaded %d%%", int(status.progress() * 100))
        except RefreshError as exc:
            # Authentication failures are permanent until the secret is replaced.
            raise _friendly_refresh_error(exc) from exc
        except HttpError as exc:
            status_code = int(getattr(exc.resp, "status", 0) or 0)
            if status_code not in RETRIABLE_STATUS_CODES:
                raise
            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError(
                    f"YouTube upload failed after {MAX_RETRIES} transient retries: {exc}"
                ) from exc
            retry_after = None
            try:
                retry_after = exc.resp.get("retry-after")
            except Exception:
                pass
            delay = _retry_delay_seconds(retry, retry_after)
            logger.warning(
                "Transient YouTube HTTP %s error. Retrying in %.1fs (%s/%s).",
                status_code,
                delay,
                retry,
                MAX_RETRIES,
            )
            time.sleep(delay)
        except (httplib2.HttpLib2Error, socket.timeout, TimeoutError, ConnectionError) as exc:
            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError(
                    f"YouTube upload failed after {MAX_RETRIES} network retries: {exc}"
                ) from exc
            delay = _retry_delay_seconds(retry)
            logger.warning(
                "Transient upload network error. Retrying in %.1fs (%s/%s): %s",
                delay,
                retry,
                MAX_RETRIES,
                exc,
            )
            time.sleep(delay)
        except Exception:
            # Unknown errors should surface immediately. Retrying invalid_grant or
            # programming errors only wastes workflow time and hides the root cause.
            raise

    return response


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags,
    category_id: str = "22",
    privacy_status: str = "public",
):
    """Upload a validated MP4 to YouTube and return its Shorts URL."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    logger.info("Authenticating with YouTube API...")
    youtube = get_authenticated_service()

    if "#Shorts" not in description:
        description += "\n\n#Shorts"

    body = {
        "snippet": {
            "title": str(title)[:100],
            "description": str(description)[:5000],
            "tags": list(tags or []),
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        chunksize=-1,
        resumable=True,
        mimetype="video/mp4",
    )
    logger.info("Initializing upload request...")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )
    response = resumable_upload(request)

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube upload response did not contain a video ID: {response}")
    video_url = f"https://www.youtube.com/shorts/{video_id}"
    logger.info("Video uploaded successfully! URL: %s", video_url)
    return video_url


def upload_thumbnail(video_id: str, thumbnail_path: str):
    """Best-effort custom thumbnail upload."""
    if not os.path.exists(thumbnail_path):
        logger.warning("Thumbnail file not found: %s. Skipping.", thumbnail_path)
        return None
    try:
        logger.info("Uploading custom thumbnail from %s...", thumbnail_path)
        youtube = get_authenticated_service()
        response = youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path),
        ).execute()
        logger.info("Thumbnail uploaded successfully.")
        return response
    except YouTubeAuthenticationError:
        raise
    except Exception as exc:
        logger.warning(
            "Thumbnail upload skipped/failed (often unavailable for Shorts): %s", exc
        )
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube OAuth/upload utilities")
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="Validate YOUTUBE_* OAuth secrets and exit without uploading.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.check_auth:
        check_youtube_auth()
        print("YouTube OAuth preflight passed.")
    else:
        parser.error("Use --check-auth. Video uploads are invoked by src/main.py.")


if __name__ == "__main__":
    main()
