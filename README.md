# Mean-Time AutoShorts

A production-oriented, fully automated YouTube Shorts pipeline for a **space-only channel**. The system generates factual scripts, verifies claims, synthesizes narration, aligns subtitles to the exact narration audio, selects relevant visuals, renders vertical video, runs strict quality gates, uploads to YouTube, and updates its topic queue transactionally.

## Channel scope

The channel is permanently focused on:

- astronomy and astrophysics
- planets, moons, asteroids, and comets
- stars, black holes, galaxies, and cosmology
- space telescopes and observatories
- rockets, spacecraft, and mission engineering
- human spaceflight and life in space
- space weather and planetary environments

Scheduled topics, automatic replacements, scripts, titles, descriptions, visuals, and metadata must remain within this scope. Manual non-space topics are rejected before generation and never modify the queue.

## Core capabilities

- Generates 35–58 second factual Shorts in 1080×1920 format.
- Uses one canonical narration file for transcription and final rendering.
- Aligns Whisper word timestamps back to the exact script.
- Handles split and merged words during alignment.
- Produces compact 2–4 word captions with timing quality checks.
- Prefers NASA imagery for space scenes.
- Scores media candidates instead of choosing randomly.
- Applies subtle motion to still images so scenes do not appear static.
- Mutes original stock audio and optionally mixes licensed background music.
- Validates the final MP4 with FFmpeg and ffprobe.
- Uploads only after all required quality gates pass.
- Rotates the queue only after a successful artifact or upload.
- Records detailed run, source, media, subtitle, and queue metadata.

## Pipeline architecture

Each run follows this sequence:

```text
reserve topic without mutating queue
→ generate a structured space-focused script
→ load verified source cache or perform batched grounded source repair
→ verify source reachability and credibility
→ synthesize narration
→ trim silence once into output/narration_final.wav
→ transcribe that exact narration with faster-whisper
→ align recognized words to the exact script
→ build timed captions and ASS subtitles
→ search, score, and select relevant scene visuals
→ apply motion to still images and trim/loop video clips
→ render a 1080×1920 H.264/AAC Short
→ run quality gates and ffprobe validation
→ upload to YouTube or create a review artifact
→ finalize the queue exactly once
→ persist queue state through GitHub Actions
```

Every run writes:

```text
autoshorts-pipeline/output/run_manifest.json
```

The manifest records the run ID, topic source, selected topic, script, narration, subtitle alignment, visual candidates, selected media, quality-gate results, upload status, YouTube URL, and queue transaction state.

## Repository structure

```text
.github/workflows/
  autoshorts-publisher.yml
  autoshorts-tests.yml

autoshorts-pipeline/
  assets/music/
  src/
  tests/
  tools/create_youtube_refresh_token.py
  .env.example
  main.sh
  requirements.txt
  topics.txt
  topic_bank.json
  topic_state.json
  queue_transaction.json
  upload_history.json
  fact_source_cache.json
```

## Requirements

- Python 3.11
- FFmpeg and ffprobe
- DejaVu Sans fonts
- Gemini API key
- Pexels and/or Pixabay API key
- YouTube OAuth credentials for publishing

### macOS setup

```bash
brew install python@3.11 ffmpeg

cd autoshorts-pipeline
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

### Ubuntu setup

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv ffmpeg fonts-dejavu-core libgomp1

cd autoshorts-pipeline
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` locally and never commit it.

## GitHub Actions configuration

### Required secrets

| Secret | Purpose |
|---|---|
| `GEMINI_API_KEY` | Script generation and grounded factual-source repair |
| `PEXELS_API_KEY` | Stock media search |
| `PIXABAY_API_KEY` | Stock media fallback |
| `YOUTUBE_CLIENT_ID` | YouTube OAuth client ID |
| `YOUTUBE_CLIENT_SECRET` | YouTube OAuth client secret |
| `YOUTUBE_REFRESH_TOKEN` | YouTube upload authorization |

At least one stock-media provider should be configured. NASA image search does not require an API key.

### Repository variables

| Variable | Recommended value | Purpose |
|---|---|---|
| `AUTOPUBLISH_ENABLED` | `false` during initial review, then `true` | Controls scheduled YouTube uploads |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Overrides the default Gemini model |

The workflow reads `AUTOPUBLISH_ENABLED` as a repository variable, not as a secret.

## YouTube OAuth setup

1. Enable **YouTube Data API v3** in Google Cloud.
2. Configure the OAuth consent screen.
3. Create an OAuth **Desktop app** client.
4. Download the client JSON file.
5. Generate a refresh token locally:

```bash
cd autoshorts-pipeline
source .venv/bin/activate
python tools/create_youtube_refresh_token.py /absolute/path/to/client_secret.json
```

6. Add the resulting values to GitHub Actions secrets.
7. Verify authentication:

```bash
python -m src.uploader --check-auth
```

Do not commit the client JSON or refresh token.

## GitHub Actions workflows

### AutoShorts Publisher

The manual workflow exposes only:

- `topic`
- `publish`

Behavior:

| Mode | Result |
|---|---|
| Topic provided, `publish=false` | Generates a review artifact; queue remains unchanged |
| Topic provided, `publish=true` | Uploads the manual space topic; queue remains unchanged |
| Topic empty, `publish=false` | Uses queue mode, creates an artifact, then finalizes the queue |
| Topic empty, `publish=true` | Uses queue mode, uploads, then finalizes the queue |
| Scheduled run | Uses queue mode and uploads only when `AUTOPUBLISH_ENABLED=true` |

Schedule:

- `03:30 UTC` — `09:00 IST`
- `14:00 UTC` — `19:30 IST`

### AutoShorts Tests

The test workflow validates:

- Python compilation
- topic-engine self-test
- queue rollback and idempotency
- space-only topic enforcement
- subtitle alignment and caption timing
- split/merged compound-word alignment
- factual-source policy and caching
- visual candidate scoring
- static-image motion rendering
- clip looping and final-duration checks
- workflow YAML structure
- FFmpeg/ffprobe smoke rendering

## First safe manual test

Keep `AUTOPUBLISH_ENABLED=false`.

Run **Actions → AutoShorts Publisher → Run workflow** with:

```text
topic: Why neutron stars spin so quickly
publish: false
```

Download the artifact and review:

- `final_short.mp4`
- `visual_contact_sheet.jpg`
- `subtitle_alignment_report.json`
- `fact_check.json`
- `quality_gate_report.json`
- `run_manifest.json`

Then run queue mode with an empty topic and `publish=false`. Confirm that exactly one topic is removed, one replacement is appended, and the queue-state commit is pushed.

After both review runs pass, perform one manual `publish=true` test. Set `AUTOPUBLISH_ENABLED=true` only after confirming the resulting Short in YouTube Studio.

## Topic queue

The channel uses:

- `topics.txt` for the active queue
- `topic_bank.json` for replacement topics
- `topic_state.json` for recent themes and completed topics
- `queue_transaction.json` for reservation/finalization state
- `upload_history.json` for recent uploads and title deduplication

The queue is transactional:

1. The first topic is reserved without being removed.
2. Generation, validation, artifact creation, or upload runs.
3. The queue is finalized only after success.
4. One topic is removed and one unique space topic is appended.
5. State files are written atomically.
6. GitHub Actions commits and pushes the updated state.

Failed runs leave the queue unchanged, allowing the same topic to be retried safely.

## Factual-source verification

The script model is not trusted to invent URLs. The pipeline:

1. checks `fact_source_cache.json` for the normalized topic;
2. live-verifies cached URLs;
3. on a cache miss, performs one batched Google Search-grounded source repair;
4. allows at most one supplemental search for an independent source;
5. accepts one reachable primary source or two independent reputable science sources;
6. saves verified sources after a successful queue transaction;
7. stops immediately on quota errors rather than repeatedly consuming requests.

## Subtitle system

The final render and Whisper transcription always use the same canonical narration file:

```text
output/narration_final.wav
```

Defaults:

- `base.en` primary model
- `small.en` fallback
- CPU int8
- beam size 5
- word timestamps enabled
- VAD enabled

Required checks include:

- raw script coverage of at least 90%
- final alignment of at least 98%
- no active-speech caption gap above 0.5 seconds
- bounded caption tails
- bounded caption durations
- no timestamps outside the final video

## Visuals and rendering

For space topics, NASA is preferred before general stock providers. The selector:

- searches using concise scene-specific queries
- rejects low-resolution and unsuitable media
- scores metadata against scene keywords
- rejects duplicate content
- mutes original stock audio
- loops or trims clips to narration-derived scene duration
- applies subtle Ken Burns motion to still images
- produces a visual contact sheet and attribution data

Final output:

```text
1080×1920
30 fps
H.264 video
AAC audio
yuv420p
faststart
```

## Music

Only licensed local tracks are permitted. Add them to:

```text
autoshorts-pipeline/assets/music/
```

Describe each track in `music_library.json`. When no track is available, narration-only rendering succeeds.

## Local verification

```bash
cd autoshorts-pipeline

python -m compileall -q src tests
PYTHONPATH=. python -m src.topic_engine --self-test
PYTHONPATH=. pytest -q
```

## Output and recovery artifacts

Successful review runs include the final video and debug reports. Failed runs upload available recovery data, including the run manifest, fact-check report, subtitle report, and queue transaction.

Critical failures are never silently ignored. Queue-push failures are surfaced in the workflow summary and preserved as recovery artifacts.

## Security and repository hygiene

Never commit:

- `.env`
- API keys or OAuth credentials
- refresh tokens
- client-secret JSON files
- generated MP4, MP3, WAV, or image files
- `output/`
- model caches
- virtual environments
- `__pycache__/`
- `.pytest_cache/`

## License and media responsibility

Use only stock media, NASA assets, and music whose terms permit the intended use. Store attribution and licence information with each selected asset. The repository does not include third-party media licences beyond the metadata supplied for local assets.
