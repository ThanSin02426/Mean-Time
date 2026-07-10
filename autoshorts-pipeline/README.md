# AutoShorts Pipeline

A GitHub Actions pipeline that generates vertical YouTube Shorts from a topic, creates narration, transcribes the final narration with faster-whisper for synchronized captions, sources stock visuals, assembles the video, and optionally uploads it to YouTube.

## Workflow behavior

The manual workflow has only two inputs:

- `topic`: optional. Leave blank to use the first line of `topics.txt`.
- `publish`: `false` creates a downloadable artifact; `true` uploads to YouTube.

Scheduled runs use `topics.txt` and publish automatically at:

- 09:00 AM IST
- 07:30 PM IST

When queue mode is used, the first topic is removed and a replacement topic is appended to keep the queue running.

## Required GitHub secrets

Add these under **Repository → Settings → Secrets and variables → Actions → Secrets**:

```text
GEMINI_API_KEY
YOUTUBE_CLIENT_ID
YOUTUBE_CLIENT_SECRET
YOUTUBE_REFRESH_TOKEN
PEXELS_API_KEY
```

Optional:

```text
PIXABAY_API_KEY
GEMINI_MODEL
WHISPER_MODEL
```

API keys and OAuth tokens should be stored as **Secrets**, not public repository variables.

## Visual and subtitle pipeline

The pipeline uses this visual order automatically:

1. Pexels
2. Pixabay fallback
3. NASA media for relevant space topics
4. Local designed fallback slides

Subtitle architecture:

```text
TTS narration
→ trim leading/trailing silence
→ transcribe that exact final narration with faster-whisper
→ create phrase captions from word timestamps
→ burn captions into the MP4
```

The same final narration file is used for Whisper and for the finished video.

## YouTube OAuth setup

1. Enable **YouTube Data API v3** in Google Cloud.
2. Configure an External OAuth consent screen.
3. Create an OAuth client of type **Web application**.
4. Add this redirect URI exactly:

```text
https://developers.google.com/oauthplayground
```

5. Open Google OAuth 2.0 Playground.
6. Enable **Use your own OAuth credentials** and enter the same client ID and client secret.
7. Authorize this scope:

```text
https://www.googleapis.com/auth/youtube.upload
```

8. Exchange the authorization code for tokens.
9. Save the new refresh token as `YOUTUBE_REFRESH_TOKEN` in GitHub Actions secrets.

## Critical: prevent refresh-token expiry

If the OAuth consent screen remains in **Testing**, Google refresh tokens for an external app can expire after seven days. The workflow then fails with:

```text
invalid_grant: Token has been expired or revoked
```

For reliable twice-daily automation:

1. Go to **Google Cloud Console → Google Auth Platform / OAuth consent screen → Audience**.
2. Change the publishing status from **Testing** to **In production**.
3. Generate a **new** refresh token in OAuth Playground after changing the status.
4. Replace the GitHub secret `YOUTUBE_REFRESH_TOKEN` with the new token.

Changing the status does not revive the old token. You must generate and save a new token.

A personal app may still display an unverified-app warning during consent. Continue only for the Google account that owns your channel. Do not share OAuth credentials or refresh tokens.

## OAuth preflight

Publish runs validate the refresh token **before** generating the video. This prevents an expired token from wasting 8–10 minutes of rendering time.

You can verify credentials locally or in a configured environment with:

```bash
cd autoshorts-pipeline
python -m src.uploader --check-auth
```

If upload fails after rendering for another reason, GitHub Actions uploads a recovery artifact containing the MP4 and metadata.

## First safe test

Run the workflow manually with:

```text
topic: 3 terrifying space facts that sound fake
publish: false
```

Open the completed run and download the `generated-short-<run number>` artifact. Check the video before using `publish=true`.

## Local run

```bash
cd autoshorts-pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python src/main.py --topic "3 terrifying space facts that sound fake" --no-upload
```

Never commit `.env`.

## Background music

Place copyright-safe `.mp3` or `.wav` files in:

```text
autoshorts-pipeline/assets/music/
```

Use tracks from sources whose licenses permit your use, such as the YouTube Audio Library. The pipeline continues with narration only when no safe music is present.

## Troubleshooting

### `invalid_grant: Token has been expired or revoked`

The refresh token is invalid. This is not a transient upload problem and retries cannot fix it.

- Set the OAuth app to **In production**.
- Generate a new refresh token.
- Replace `YOUTUBE_REFRESH_TOKEN` in GitHub secrets.
- Re-run the workflow.

### Manual `publish=false` has no artifact

The run uploads an artifact only when a rendered MP4 exists. Open **Final debug summary** and confirm `final_short_exists=true`.

### Topic queue does not commit

Go to:

```text
Repository → Settings → Actions → General → Workflow permissions
```

Select **Read and write permissions**.

### Captions are early or late

Keep:

```text
CAPTION_LEAD_SECONDS=0.0
```

The pipeline already trims silence and transcribes the final narration. Review `output/captions.json` and the caption timing report in the artifact before changing timing values.
