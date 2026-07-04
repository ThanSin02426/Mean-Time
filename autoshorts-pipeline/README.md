# AutoShorts Pipeline

## 1. Project Overview

AutoShorts Pipeline is a completely free, automated Python pipeline that generates and publishes YouTube Shorts end-to-end. It requires no paid credits or subscriptions. It uses:
- **Gemini** for script generation (with a fallback to Pollinations.ai)
- **edge-tts** for voiceover and synchronized captions
- **Pollinations** for high-quality, text-free AI images (Flux model)
- **MoviePy & ffmpeg** for video assembly (Ken Burns effects, crossfades, ducked background music, and animated captions)
- **YouTube Data API v3** for optional automated uploads
- **GitHub Actions** for free cloud execution on a daily schedule

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

## 8. Workflow Configuration & Modes

When triggering the workflow manually (via **Run workflow**), you have several options:
- **Topic**: Leave blank to pop a topic from your `topics.txt` queue, or type a specific topic.
- **Publish**: Check this to upload the video to YouTube. Leave it unchecked to run in **Preview Mode**. In Preview Mode, the video is rendered and saved as a downloadable GitHub Action Artifact instead of being uploaded.
- **Quality Mode**:
  - `preview` (default): Fast generation targeting ~30s and 3-4 scenes. Ideal for testing.
  - `production`: Targets the full 45-55s duration with 5-7 scenes. Scheduled daily runs use this mode automatically.
- **Image Provider Mode**:
  - `hybrid` (default): Tries to use the AI Image generator, but gracefully falls back to beautiful typography slides if the API times out.
  - `pollinations`: Strictly enforces AI image generation.
  - `local_only`: Completely skips the AI image API and instantly generates modern text-based slides. Great for ultra-fast local testing.

## 9. How to Preview and Publish

Always test your setup using **Preview Mode** first!
1. Go to Actions > Daily YouTube Shorts Auto-Publisher.
2. Click **Run workflow**. Keep **Publish** unchecked.
3. Once the workflow finishes, open the run summary and scroll to the bottom to download the `generated-short` artifact.
4. If everything looks good, you can check the **Publish** box on your next run.
5. The pipeline has a built-in Quality Gate that explicitly blocks publishing if the video exceeds 58 seconds to ensure your video is successfully categorized as a Short on YouTube.

## 10. Music

- Background music is **optional**.
- If the `autoshorts-pipeline/assets/music/` directory is empty or missing, the pipeline will intelligently render a narration-only video without failing.
- You can populate this folder later with free MP3/WAV tracks downloaded from the [YouTube Audio Library](https://studio.youtube.com/channel/UC/music).

## 11. Local Testing

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

## 12. Troubleshooting

- **Write access to repository not granted?** If the workflow completes the video but fails at the final "Commit queue updates" step with a 403 error, go to your GitHub repository **Settings -> Actions -> General -> Workflow permissions** and select **Read and write permissions**.
- **Gemini model not found error?** The script tries to auto-detect a working model, but if it fails, set `GEMINI_MODEL` as an environment variable/GitHub Secret to a currently supported model (e.g. `gemini-2.5-flash`).
- **Workflow doesn't show in Actions?** Ensure that `.github/workflows/daily.yml` is at the absolute root of your repository, NOT inside `autoshorts-pipeline/`.
- **Run workflow button is missing?** Ensure the `workflow_dispatch` trigger is properly defined in the `daily.yml` file.
- **OAuth says access blocked?** Ensure you added your email address as a "Test User" on the OAuth consent screen in Google Cloud Console.
- **OAuth Playground redirect fails?** Ensure you created the OAuth client as a **Web application** (not a Desktop app) and typed the redirect URI perfectly.
- **Video artifact is missing?** Ensure `publish` was false and that the `upload-artifact` step executed successfully without path errors.
