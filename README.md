# Mean-Time AutoShorts — Production Rebuild

This repository package replaces the incremental AutoShorts patches with one source-first, transactional, testable pipeline. It generates a 1080×1920 factual Short, creates one canonical narration file, aligns captions to that exact file, selects scored visuals, validates the final MP4, and uploads only after every required gate passes.

## What changed

The previous branch had several interacting failure modes:

- The queue was popped and rewritten before script, render, quality checks, or upload succeeded.
- Subtitle timing could fall back to evenly distributed words instead of alignment to the exact narration.
- The primary Whisper model was `tiny.en`, and the final render was not protected by a raw-recognition coverage gate.
- Pexels/Pixabay results were selected randomly from early search results.
- Generic sentences could be appended to a short script, changing the requested topic while satisfying a word-count check.
- Analytics-like scoring was active by default and the queue contained only ten starting topics.
- Queue push failures were hidden as warnings, leaving GitHub state different from the successful run.
- There was no automated test suite for subtitle timing, queue rollback, media looping, title duplication, or workflow structure.

This rebuild removes those paths instead of layering another compatibility patch over them.

## Canonical run architecture

Every run writes `autoshorts-pipeline/output/run_manifest.json`. The manifest records:

- run ID and lifecycle status
- manual or queue topic source
- selected topic and niche
- structured script and exact narration text
- narration word count, canonical audio path, and duration
- Whisper/alignment report
- scene list and aligned scene durations
- visual candidates, scores, selections, and attribution
- final MP4 path and duration
- quality-gate results
- upload state and YouTube URL
- queue transaction state
- precise failure messages

Pipeline order:

```text
reserve topic without mutating queue
→ generate structured sourced script
→ verify factual source URLs
→ synthesize TTS
→ trim silence once into output/narration_final.wav
→ transcribe that exact WAV with faster-whisper
→ force-align recognized words to the normalized script
→ create 2–4 word captions and ASS subtitles
→ select scored scene visuals
→ derive scene cuts from aligned narration
→ render 1080×1920 H.264/AAC MP4
→ run ffprobe and content quality gates
→ upload or create review artifact
→ finalize queue exactly once
→ commit/push queue state in GitHub Actions
```

## Repository layout

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
```

## Prerequisites

- Python 3.11
- FFmpeg and ffprobe
- DejaVu Sans fonts
- A Gemini API key
- A Pexels and/or Pixabay API key; NASA image search needs no key
- YouTube OAuth credentials only for publishing

### macOS

```bash
brew install python@3.11 ffmpeg
cd autoshorts-pipeline
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

### Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv ffmpeg fonts-dejavu-core libgomp1
cd autoshorts-pipeline
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` locally. Never commit it.

## GitHub Actions secrets and variables

### Required secrets

| Name | Required for | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | every production run | Structured factual script generation |
| `PEXELS_API_KEY` | recommended | General stock video/photo search |
| `PIXABAY_API_KEY` | recommended fallback | General stock video/photo search |
| `YOUTUBE_CLIENT_ID` | publish runs | OAuth client ID |
| `YOUTUBE_CLIENT_SECRET` | publish runs | OAuth client secret |
| `YOUTUBE_REFRESH_TOKEN` | publish runs | Offline YouTube upload authorization |

At least one of Pexels or Pixabay should be configured. The pipeline creates a local designed fallback when providers fail, but an all-fallback video should be reviewed before publishing.

### Repository variables

| Name | Safe initial value | Purpose |
|---|---|---|
| `AUTOPUBLISH_ENABLED` | `false` | Scheduled runs create artifacts until deliberately enabled |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Script model override |

Analytics is modularly reserved but disabled by default. There is no analytics-driven topic exploitation in this build.

## YouTube OAuth setup

1. In Google Cloud, enable **YouTube Data API v3**.
2. Configure the OAuth consent screen and add your Google account while setting it up.
3. For an external app, change publishing status from **Testing** to **In production** before creating the long-lived token. Testing-mode refresh tokens commonly expire after seven days.
4. Create an OAuth **Desktop app** client and download its JSON file.
5. Locally run:

```bash
cd autoshorts-pipeline
source .venv/bin/activate
python tools/create_youtube_refresh_token.py /absolute/path/to/client_secret.json
```

6. Copy the printed values into GitHub Actions secrets. Do not commit the downloaded JSON.
7. Verify the token before rendering:

```bash
python -m src.uploader --check-auth
```

A refresh token does not require weekly replacement merely because a week passed when the OAuth app is in production. It can still become invalid if access is revoked, credentials are changed/deleted, the account security state changes, or Google invalidates it. `invalid_grant` is treated as permanent and is not retried.

## Exact first safe manual test

Keep `AUTOPUBLISH_ENABLED=false`.

1. Open **Actions → AutoShorts Publisher → Run workflow**.
2. Select the branch containing this package.
3. Set:
   - `topic`: `Why skyscrapers are designed to sway`
   - `publish`: unchecked / `false`
4. Run the workflow.
5. Download the `autoshorts-...` artifact.
6. Review at minimum:
   - `final_short.mp4`
   - `visual_contact_sheet.jpg`
   - `subtitle_alignment_report.json`
   - `fact_check.json`
   - `quality_gate_report.json`
   - `run_manifest.json`
7. Confirm the queue is unchanged because a manual topic never mutates it.

Then run one queue-mode artifact test with an empty `topic` and `publish=false`. After success, verify that exactly one queue topic was removed, one replacement was appended, and the state commit was pushed.

## Enabling publishing safely

1. Complete the two artifact-only tests above.
2. Run a manual `publish=true` test with a manual topic.
3. Confirm the uploaded title, audio, captions, visual relevance, privacy status, and duration.
4. Run a manual queue-mode `publish=true` test with the topic field empty.
5. Confirm the queue state commit appears on the same branch.
6. Set repository variable `AUTOPUBLISH_ENABLED=true`.

Scheduled runs occur at:

- `03:30 UTC` = `09:00 IST`
- `14:00 UTC` = `19:30 IST`

When `AUTOPUBLISH_ENABLED` is not exactly `true`, scheduled runs still generate review artifacts but do not upload.

## Manual UI behavior

The publisher workflow exposes only:

- `topic`
- `publish`

Rules:

- non-empty topic: manual mode; queue is never changed
- empty topic: queue mode
- `publish=false`: generate downloadable artifact
- `publish=true`: OAuth preflight, render, quality gates, then upload
- schedule: queue mode; publish only when `AUTOPUBLISH_ENABLED=true`

## Transactional topic queue

The starting queue contains 24 interleaved topics across 12 niches. The bank contains 96 topics.

Queue files:

- `topics.txt`
- `topic_state.json`
- `topic_bank.json`
- `queue_transaction.json`
- `upload_history.json`

A queue run only reserves the first topic. It does not remove it. Finalization happens after a successful artifact or upload:

1. verify the reserved topic is still the queue head
2. select a unique replacement
3. enforce exact-topic cooldown and niche diversity
4. atomically replace queue/state files
5. mark the transaction finalized
6. commit and push through GitHub Actions

A failed pipeline writes a failed transaction record while leaving the queue unchanged. Re-finalizing the same successful transaction is idempotent.

Run the 50-rotation simulation locally:

```bash
cd autoshorts-pipeline
PYTHONPATH=. python -m src.topic_engine --self-test
```

## Subtitle system

The final video and Whisper always consume the same `output/narration_final.wav`.

Defaults:

- primary model: `base.en`
- fallback model: `small.en`
- CPU + int8
- beam size 5
- word timestamps enabled
- carefully configured VAD

The aligner normalizes punctuation, apostrophes, whitespace, and common numeric forms. Script words omitted by Whisper receive bounded, monotonic interpolation between surrounding matched words. A publishable run requires:

- raw script coverage at least 90%
- final aligned coverage at least 98%
- acceptable first-caption timing
- no active-speech caption gap over 0.5 seconds
- no caption tail over 0.20 seconds
- caption timestamps within the final video

Set `RENDER_SUBTITLE_TEST=true` locally to also produce `output/subtitle_test.mp4`.

## Visual selection

Each script scene contains:

- narration
- visual subject
- concise visual query
- negative terms
- preferred media type
- scene keywords
- factual claim and source notes

Selection is deterministic and scored. There is no `random.choice` stock selection. The pipeline:

- prefers NASA imagery for space
- uses Pexels then Pixabay for general topics
- rejects low-resolution and excessively wide media
- scores metadata overlap and negative-term hits
- rejects repeated IDs and duplicate downloaded content
- mutes all source audio
- loops short clips to the scene duration
- records candidates, scores, selections, and attribution
- produces a visual contact sheet

## Music

The build never scrapes trending/copyrighted Shorts audio. Add only cleared tracks to `assets/music/` and describe each one in `assets/music/music_library.json`:

```json
{
  "example-track.mp3": {
    "title": "Example Track",
    "source": "YouTube Audio Library",
    "license": "YouTube Audio Library terms",
    "mood": "curious"
  }
}
```

When no track exists, narration-only rendering succeeds. Music is looped, faded, and mixed around -22.5 dB relative to the narration path.

## Tests

Run everything:

```bash
cd autoshorts-pipeline
python -m compileall -q src tests
PYTHONPATH=. python -m src.topic_engine --self-test
PYTHONPATH=. pytest -q
```

The separate **AutoShorts Tests** workflow runs compilation, the 50-rotation queue self-test, subtitle alignment tests, queue rollback/idempotency tests, visual scoring tests, clip-looping tests, workflow YAML validation, and a real FFmpeg/ffprobe smoke render.

## Output and failure artifacts

Successful review runs include the final video plus debug reports. Failed runs upload any available recovery data, including the run manifest and queue transaction. A queue push failure is never hidden: the workflow retries once after `git pull --rebase`, emits a visible warning, and uploads the queue recovery files.

## Migration from `autoshorts-pipeline-2563395256958636168`

1. Back up the current branch.
2. Replace the current `autoshorts-pipeline/` directory with this package's directory.
3. Replace the old publisher workflow with `.github/workflows/autoshorts-publisher.yml`.
4. Add `.github/workflows/autoshorts-tests.yml`.
5. Do not copy any old output, cache, `.env`, token, MP4, VTT, or temporary media files.
6. Re-enter secrets/variables in GitHub; none are stored in this package.
7. Keep `AUTOPUBLISH_ENABLED=false` until both artifact tests pass.
8. Run tests before merging.

Suggested local replacement commands from the root of a clone:

```bash
# Adjust the extracted package path as needed.
rsync -a --delete /path/to/Mean-Time-AutoShorts-Final/autoshorts-pipeline/ ./autoshorts-pipeline/
cp /path/to/Mean-Time-AutoShorts-Final/.github/workflows/autoshorts-publisher.yml .github/workflows/
cp /path/to/Mean-Time-AutoShorts-Final/.github/workflows/autoshorts-tests.yml .github/workflows/
cp /path/to/Mean-Time-AutoShorts-Final/.gitignore .gitignore

git add .github/workflows autoshorts-pipeline .gitignore
git status
git commit -m "rebuild AutoShorts as transactional production pipeline"
git push origin HEAD
```

## Final Jules prompt

Copy the following prompt into Jules and attach the extracted ZIP:

> Work on repository `ThanSin02426/Mean-Time`, branch `autoshorts-pipeline-2563395256958636168`. I am attaching `Mean-Time-AutoShorts-Final.zip`, which is the approved replacement implementation. Apply it exactly as the source of truth: replace the existing `autoshorts-pipeline/` directory, replace `.github/workflows/autoshorts-publisher.yml`, add `.github/workflows/autoshorts-tests.yml`, and apply the included root `.gitignore`. Do not merge old subtitle, queue, MoviePy, analytics, random-stock, VTT, preview-mode, or compatibility-patch logic back into the replacement. Do not add secrets, generated media, caches, virtual environments, or extra handoff Markdown files. Preserve unrelated repository content outside these paths. After applying the files, run `python -m compileall -q src tests`, `PYTHONPATH=. python -m src.topic_engine --self-test`, and `PYTHONPATH=. pytest -q` from `autoshorts-pipeline`. Also parse both workflow YAML files. Fix only genuine integration errors found by those checks; do not redesign the approved architecture. Show the exact test output and final changed-file list, then commit the complete replacement in one clean commit.
