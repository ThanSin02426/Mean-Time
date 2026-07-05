# AutoShorts Pipeline

## 1. Project Overview

AutoShorts Pipeline is a completely free, automated Python pipeline that generates and publishes YouTube Shorts end-to-end. It requires no paid credits or subscriptions. It uses:
- Gemini for script generation (with a fallback to Pollinations.ai)
- edge-tts for voiceover and synchronized captions
- Pexels/Pixabay APIs and NASA imagery for stock media visuals (with beautifully designed local slide fallbacks)
- MoviePy & ffmpeg for video assembly (Ken Burns effects, crossfades, ducked background music, and animated captions)
- YouTube Data API v3 for optional automated uploads
- GitHub Actions for free cloud execution on a daily schedule

## 2. Repository Structure

- The main application code and Python scripts are located inside the `autoshorts-pipeline/` directory.
- The GitHub Actions workflow file is located at the repository root: `.github/workflows/daily.yml`.
- The workflow natively uses `working-directory: autoshorts-pipeline` to ensure scripts run in the correct context.
- Assets (such as background music) should be placed inside `autoshorts-pipeline/assets/music/`.

## 3. Required GitHub Secrets

To run this pipeline via GitHub Actions, you must configure the following Repository Secrets:
- `GEMINI_API_KEY`
- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`
- `PEXELS_API_KEY`
- `PIXABAY_API_KEY` (optional fallback)

## 4. Gemini API Setup

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Sign in with your Google account.
3. Click "Create API key" and copy the generated key.
4. Go to your GitHub repository -> Settings -> Secrets and variables -> Actions.
5. Create a new secret named `GEMINI_API_KEY` and paste your key.

By default, the script auto-detects the best Gemini Flash model available. If you want to force a specific model, add a GitHub secret (or `.env` variable) named `GEMINI_MODEL` (e.g., `gemini-2.5-flash`).

## 5. YouTube API Setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new Project (e.g., "AutoShorts").
3. Go to **APIs & Services > Library** and search for "YouTube Data API v3". Enable it.
4. Go to **APIs & Services > OAuth consent screen**.
   - Choose "External" (or Internal if you have a Workspace account).
   - Fill in the required app info (App name, support email, developer email).
   - Add the following scope: `https://www.googleapis.com/auth/youtube.upload`
   - **Important:** Add the Gmail account that owns your YouTube channel as a "Test User" while the app is in Testing mode.

## 6. Correct OAuth Client Setup

1. Go to **APIs & Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. **Application type MUST be "Web application"** (do NOT select Desktop app).
4. Name it (e.g., "AutoShorts Web Client").
5. Under **Authorized redirect URIs**, click Add URI and paste EXACTLY:
   `https://developers.google.com/oauthplayground`
6. Click Create.
7. Copy the **Client ID** and **Client Secret**.
8. Save them as GitHub secrets: `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`.

## 7. Getting the YouTube Refresh Token

Because this script runs headlessly via GitHub Actions, it needs a refresh token to generate short-lived access tokens for uploads.
1. Open the [Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground/).
2. Click the gear icon (OAuth 2.0 configuration) in the top right.
3. Check the box for "Use your own OAuth credentials".
4. Paste your Web application **Client ID** and **Client Secret**.
5. Close the gear menu. In **Step 1**, paste this scope into the "Input your own scopes" field:
   `https://www.googleapis.com/auth/youtube.upload`
6. Click **Authorize APIs**.
7. Sign in using the Gmail account that owns your YouTube channel (the one you added as a Test User).
8. If you see an "unverified app" warning, click Advanced and continue (since this is your personal testing app).
9. In **Step 2**, click **Exchange authorization code for tokens**.
10. Copy the **Refresh token**.
11. Save it as a GitHub secret: `YOUTUBE_REFRESH_TOKEN`.

## 8. Workflow Configuration & Automatic Schedules

The GitHub Actions workflow operates automatically twice a day based on standard cron schedules:
- **09:00 AM IST**
- **07:30 PM IST**

Scheduled runs will automatically pull a topic from `topics.txt` and publish directly to YouTube.

You can also manually trigger a run (via **Run workflow**), where you have two simple options:
- **Topic**: Leave blank to pop a topic from your `topics.txt` queue, or type a specific topic.
- **Publish**: Check this (`true`) to upload the video to YouTube. Leave it unchecked (`false`) to generate the video and upload it as a downloadable artifact for review instead.

## 9. Recommended Visual Media Strategy

The pipeline automatically handles sourcing high-quality vertical visuals for your Shorts using a prioritized cascade:
1. **Pexels Video/Photo API** (Primary source)
2. **Pixabay Video/Photo API** (Secondary fallback)
3. **NASA Media Library** (Triggered exclusively for space/science topics)
4. **Local Designed Fallback** (If all networks fail, generates beautiful, cinematic text-based slides)

*Pollinations AI image generation is no longer used by default due to high unreliability and rate limiting.*

## 10. How to Preview and Publish

Always test your setup using **Preview Mode** first! We recommend starting with a fast test:
1. Go to Actions > Daily YouTube Shorts Auto-Publisher.
2. Click **Run workflow**.
3. Fill out the fields with these recommended settings:
   - **Topic**: `3 terrifying space facts that sound fake`
   - **Publish**: `false` (unchecked)
4. Click **Run workflow** and wait for it to complete.
5. Open the run details and scroll down to the **Artifacts** section at the bottom of the Summary page.
6. Download the `generated-short` artifact to view your MP4 video and its metadata JSON.
7. If everything looks good, you can re-run with **Publish** checked. The pipeline has a built-in Quality Gate that explicitly blocks publishing if the video exceeds 58 seconds.

## 11. Music

Do **NOT** attempt to use trending copyrighted YouTube Shorts songs automatically via scripts. It will cause copyright strikes.
- The safest method is to manually download copyright-safe tracks from the [YouTube Audio Library](https://studio.youtube.com/channel/UC/music).
- Place these files in `autoshorts-pipeline/assets/music/`.
- If local music is found, the pipeline mixes it ducked under the voiceover.
- If no local music exists, it will search the Pixabay Audio API for a cinematic track.
- If all fails, it renders narration-only without crashing.

## 12. Local Testing

To run and test the pipeline on your own machine:

```bash
cd autoshorts-pipeline
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Fill out the .env file with your API keys and credentials
# WARNING: NEVER commit your .env file to version control!

python src/main.py --topic "3 strange facts about the ocean" --no-upload
```

## 13. Troubleshooting

- **Write access to repository not granted?** If the workflow completes the video but fails at the final "Commit queue updates" step with a 403 error, go to your GitHub repository **Settings -> Actions -> General -> Workflow permissions** and select **Read and write permissions**.
- **Gemini model not found error?** The script tries to auto-detect a working model, but if it fails, set `GEMINI_MODEL` as an environment variable/GitHub Secret to a currently supported model (e.g. `gemini-2.5-flash`).
- **Workflow doesn't show in Actions?** Ensure that `.github/workflows/daily.yml` is at the absolute root of your repository, NOT inside `autoshorts-pipeline/`.
- **Run workflow button is missing?** Ensure the `workflow_dispatch` trigger is properly defined in the `daily.yml` file.
- **OAuth says access blocked?** Ensure you added your email address as a "Test User" on the OAuth consent screen in Google Cloud Console.
- **OAuth Playground redirect fails?** Ensure you created the OAuth client as a **Web application** (not a Desktop app) and typed the redirect URI perfectly.
- **Video artifact is missing?** Ensure `publish` was false and that the `upload-artifact` step executed successfully without path errors.

## Queue and Caption Reliability Notes

### Never-ending topic queue

When the workflow runs with `topics.txt`, the app now uses the first topic, removes it, and appends a new similar topic to the end of the file. This keeps the queue moving forever instead of slowly becoming empty.

Example behavior:

```text
Before:
1. 3 terrifying space facts that sound fake
2. The darkest secrets of the deep ocean
...

After one scheduled run:
1. The darkest secrets of the deep ocean
...
10. 3 black hole facts that feel impossible
```

Because the similar topic is appended at the end, the same topic family returns only after the queue has cycled through the other topics.

### Subtitle coverage

Captions now use `captions.json` word timings only. Phrase captions are extended until the next phrase starts, and the final caption is extended to the end of the video. This prevents missing subtitles during pauses and avoids large empty gaps while speech is playing.
