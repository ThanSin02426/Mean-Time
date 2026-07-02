# AutoShorts Pipeline

A completely free, 100% automated Python pipeline that generates and publishes YouTube Shorts end-to-end. It uses free APIs, requires no paid credits, and runs autonomously in the cloud via GitHub Actions.

## Features
- **Script Generation**: Gemini AI (with a fallback to Pollinations AI text endpoint).
- **Voiceover**: Free Microsoft Edge TTS (`edge-tts`) with word-level VTT timings.
- **Visuals**: Pollinations AI (Flux) for high-quality, text-free scene images.
- **Video Assembly**: `moviepy` and `ffmpeg` to add Ken Burns effects, crossfades, ducked background music, and word-by-word highlighted captions.
- **Upload**: YouTube Data API v3 for fully automated, resumable uploads.
- **Automation**: GitHub Actions workflow to run daily for free.

## Setup Instructions

If you want to run this entirely on GitHub Actions, you don't even need to run it locally. Just follow these steps to get your free credentials and add them to your repo.

### 1. Gemini API Key (Free)
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Sign in with your Google account.
3. Click "Create API key" and copy the key.
4. Go to your GitHub repository -> Settings -> Secrets and variables -> Actions.
5. Add a new repository secret named `GEMINI_API_KEY` and paste the key.

### 2. YouTube API Credentials (Free)
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new Project (e.g., "AutoShorts").
3. Go to **APIs & Services > Library** and search for "YouTube Data API v3". Enable it.
4. Go to **APIs & Services > OAuth consent screen**.
   - Choose "External" (or Internal if you have a Workspace account).
   - Fill in the required app info (App name, support email, developer email).
   - Add the scope `https://www.googleapis.com/auth/youtube.upload`.
   - Add your own Google Account as a "Test User".
5. Go to **APIs & Services > Credentials**.
   - Click "Create Credentials" > "OAuth client ID".
   - Application type: "Desktop app". Name it something like "AutoShorts Uploader".
   - Click Create. Download the JSON or copy the **Client ID** and **Client Secret**.
6. Add these as GitHub secrets: `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`.

### 3. Getting the YouTube Refresh Token
Since the GitHub Action runs headlessly, we need a refresh token to generate short-lived access tokens.
You can get this easily by running a small script locally, or using a tool like [Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground/):
1. Go to the OAuth 2.0 Playground.
2. Click the gear icon (OAuth 2.0 configuration) in the top right. Check "Use your own OAuth credentials" and paste your Client ID and Client Secret.
3. In Step 1, input your own scopes: `https://www.googleapis.com/auth/youtube.upload` and click "Authorize APIs".
4. Log in with the Google Account that owns your YouTube channel.
5. In Step 2, click "Exchange authorization code for tokens".
6. Copy the **Refresh token**.
7. Add it as a GitHub secret: `YOUTUBE_REFRESH_TOKEN`.

### 4. Background Music
The script expects background music in the `assets/music` folder.
1. Download a few free, copyright-safe tracks from the [YouTube Audio Library](https://studio.youtube.com/channel/UC/music).
2. Commit them to your repo inside `assets/music/` (e.g., `assets/music/track1.mp3`). The script will randomly pick one for each video.

### 5. Running Locally (Optional)
If you want to run it on your own machine:
```bash
pip install -r requirements.txt
# Ensure you have ffmpeg and ImageMagick installed on your system
cp .env.example .env
# Fill in your .env file with your credentials
./main.sh --topic "Fascinating facts about the ocean" --no-upload
```

### 6. Set Topics
Edit `topics.txt` with a list of topics you want the bot to make videos about. The GitHub Action will pick one randomly each day.
