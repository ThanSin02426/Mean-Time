import os
import time
import httplib2
import logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

# Constants for retry logic
MAX_RETRIES = 5
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

def get_authenticated_service():
    """
    Constructs credentials using environment variables and builds the YouTube service.
    Requires YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, and YOUTUBE_REFRESH_TOKEN.
    """
    client_id = os.environ.get('YOUTUBE_CLIENT_ID')
    client_secret = os.environ.get('YOUTUBE_CLIENT_SECRET')
    refresh_token = os.environ.get('YOUTUBE_REFRESH_TOKEN')

    if not all([client_id, client_secret, refresh_token]):
        raise ValueError("Missing YouTube OAuth credentials in environment variables.")

    # Create credentials object from the refresh token
    creds = Credentials(
        token=None,  # We just have the refresh token
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )

    return build("youtube", "v3", credentials=creds)

def resumable_upload(request):
    """
    Executes a resumable upload with retry logic.
    """
    response = None
    error = None
    retry = 0

    while response is None:
        try:
            logger.info("Uploading video...")
            status, response = request.next_chunk()
            if status:
                logger.info(f"Uploaded {int(status.progress() * 100)}%")
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = f"A retriable HTTP error {e.resp.status} occurred: {e.content}"
            else:
                raise e
        except httplib2.HttpLib2Error as e:
            error = f"A retriable error occurred: {e}"
        except Exception as e:
            error = f"An unexpected error occurred: {e}"

        if error is not None:
            logger.error(error)
            retry += 1
            if retry > MAX_RETRIES:
                raise Exception("Max retries exceeded.")

            sleep_seconds = (2 ** retry) # Exponential backoff
            logger.info(f"Sleeping {sleep_seconds} seconds and then retrying...")
            time.sleep(sleep_seconds)
            error = None # reset error

    return response

def upload_video(video_path, title, description, tags, category_id="22", privacy_status="public"):
    """
    Uploads a video to YouTube.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    logger.info(f"Authenticating with YouTube API...")
    youtube = get_authenticated_service()

    # Make sure #Shorts is in the description
    if "#Shorts" not in description:
        description += "\n\n#Shorts"

    body = {
        "snippet": {
            "title": title[:100], # YouTube title limit is 100 chars
            "description": description[:5000], # Description limit is 5000 chars
            "tags": tags,
            "categoryId": category_id
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False
        }
    }

    # Setup MediaFileUpload
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")

    # Create the request
    logger.info("Initializing upload request...")
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media
    )

    # Execute the upload
    response = resumable_upload(request)

    video_id = response.get("id")
    video_url = f"https://www.youtube.com/shorts/{video_id}"
    logger.info(f"Video uploaded successfully! URL: {video_url}")

    return video_url

def upload_thumbnail(video_id, thumbnail_path):
    """
    Uploads a custom thumbnail for a given video ID.
    """
    if not os.path.exists(thumbnail_path):
        logger.warning(f"Thumbnail file not found: {thumbnail_path}. Skipping thumbnail upload.")
        return

    try:
        logger.info(f"Uploading custom thumbnail from {thumbnail_path}...")
        youtube = get_authenticated_service()

        request = youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path)
        )
        response = request.execute()
        logger.info(f"Thumbnail uploaded successfully.")
        return response
    except Exception as e:
        logger.error(f"Failed to upload thumbnail (this is common for Shorts if feature not enabled on channel): {e}")

if __name__ == "__main__":
    # Test script directly (will fail without credentials)
    logging.basicConfig(level=logging.INFO)
    print("Testing upload_video... (Expected to fail without env vars)")
    try:
        upload_video("dummy.mp4", "Test Title", "Test Description", ["test"])
    except Exception as e:
        print(f"Failed as expected: {e}")
