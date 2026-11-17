# AGENTS.md — SuperAgentK Marketing Studio

Instructions for coding agents (Codex, Claude Code) working in this repo. Read this file before every task.

## What this app is

A private Streamlit studio that turns a user's text idea into a short AI-generated marketing video.
Pages: Create video (Single clip, Storyboard), Library, Usage, Brand kit, Models.
Providers today: Mock (free, for testing) and BytePlus Seedance (real, billed).

## Rules that must never be broken

1. **No API keys in code, prompts, logs, commits or test fixtures.** Keys come only from encrypted settings or Streamlit secrets. Never print a full key; show at most the last 4 characters.
2. **Every video provider goes through the provider adapter interface** (`providers/`). Pages never call a provider's HTTP API directly.
3. **Mock must keep working.** Every change must still run end to end with Mock selected, without spending credits.
4. **A single clip is one generation.** Its length can never exceed the selected model's maximum. Only Storyboard joins clips.
5. **Never invent business facts.** Business name, key message, CTA, website and phone come from the brand kit or the user's input, never from a model's imagination.
6. **Every billable call writes one row to the usage ledger** (provider or model, seconds or tokens, estimated cost, user, time). Usage and caps read only from the ledger.
7. **Logos are overlaid on the finished video, not drawn by the video model.**
8. **Don't change the database schema without a migration** and a note in the pull request explaining it.
9. **Small pull requests.** One prompt = one branch = one PR. Don't refactor unrelated code.

## Before you say a task is done

- The app starts locally with `streamlit run` and every page loads without errors.
- A Mock generation completes and appears in the Library with status, cost and a playable clip.
- New logic has tests (provider adapters, capability checks, cost maths, caption parsing).
- The PR description lists what changed, how you tested it, and anything the owner must do (new secret, new package, migration).

## Architecture notes

### Repo and pages

The Streamlit entrypoint is `SuperAgentK Streamlit GitHub files/streamlit_app.py`.
The app uses one script with sidebar navigation. Repo-root `requirements.txt`
defines Python dependencies, and repo-root `packages.txt` installs FFmpeg on
Streamlit Community Cloud.

- **Create video** has Single clip and Storyboard modes. Single clip submits
  one five-second job or saves a draft. Storyboard accepts two to five named
  scenes with individual prompts and durations. Both show job state and allow
  playback and download once ready.
- **Library** lists single clips and storyboards separately. Single clips can
  be filtered by status and searched by prompt. Opening a job or storyboard
  resumes progress checks.
- **Brand kit** saves named text presets and appends selected notes to single
  clip prompts. It currently has no file uploads, brand images, or logo overlays.
- **Usage** displays job rows, estimated cost, and a CSV export. **Models**
  displays the configured Seedance model and region and can test the key with
  a read-only provider call.

### Records, files, and usage

- **SQLite** is the only database. `studio.database()` opens a local
  `sqlite3.connect(DATA / "studio.sqlite3", timeout=20)` connection per context,
  initializes tables with `CREATE TABLE IF NOT EXISTS`, and closes the
  connection on exit. `DATA` is set by `STUDIO_DATA_DIR` or defaults to
  `SuperAgentK Streamlit GitHub files/studio-data/`. There is no hosted DB
  connection or formal migration runner today.
- `jobs` stores single clips, drafts, and scene attempts, including prompts,
  provider IDs, status, errors, estimated cost in integer cents, and creation
  time. `presets` stores brand kit names and notes. `storyboards`,
  `storyboard_scenes`, and `storyboard_attempts` store projects, ordered scene
  links and durations, and attempt claims. The monthly cap sums the selected
  provider's `jobs.cents` since the start of the UTC month. Pending scene costs
  are reserved before submission; definitive submission rejections release the
  estimate. There is no separate usage ledger or per-user accounting yet.
- Seedance MP4s are downloaded into `DATA/<job-id>.mp4`; combined files go
  to `DATA/storyboard-<project-id>.mp4`. Downloads and renders use temporary
  files before replacing the final path. Mock uses the tracked
  `assets/sample-*.mp4` clips and the Create screen uses `assets/still.jpg`.
  The app does not generate or save per-video thumbnails.

### Generation and stitching

- Mock marks jobs Ready and points to a bundled sample for the chosen aspect
  ratio. It makes no HTTP calls and costs zero. Mock storyboard scenes loop
  the sample when necessary to match the chosen duration.
- Seedance is called by `studio.submit_seedance()` and `studio.poll()`
  through `studio.ark()`. The Python server uses BytePlus ModelArk's regional
  tasks API with a bearer key read from Streamlit Secrets or the server
  environment. The Models key test lists tasks without starting a generation.
  There is no provider adapter registry yet. Streamlit fragments check job
  status every eight seconds while the detail is open; no independent worker
  polls while the studio is closed.
- `storyboard.py` reserves the total estimated cost and creates scene jobs in
  one SQLite transaction. The storyboard detail claims and submits waiting
  scenes, polls active ones, and permits a retry for a failed scene without
  rerunning successful scenes. When every scene is Ready, FFmpeg normalizes
  the clips to the selected dimensions and 24 fps and concatenates them in
  scene order into one silent H.264 MP4. The final file is published locally
  after the encode succeeds.

### Streamlit Community Cloud storage risk

Community Cloud [does not guarantee local file persistence](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data).
A reboot or redeploy can lose SQLite job and storyboard history, brand
presets, usage and cap accounting, and generated MP4s. Losing the SQLite
file can reset the studio's monthly estimate while BytePlus billing
continues. Bundled Mock assets can be restored from the Git checkout.
Setting `STUDIO_DATA_DIR` to another local directory does not provide a
persistent volume in Community Cloud.

### Proposed persistent storage

Use hosted PostgreSQL, such as Supabase, for jobs, scene and attempt links,
brand presets, and a usage ledger. Keep reservations and cap checks
transactional, and record each billable call once with an idempotency key.
Use [private object storage](https://supabase.com/docs/guides/storage/buckets/fundamentals)
for scene clips, stitched outputs, and future brand files or thumbnails.
Store object keys in Postgres and serve authorized downloads with short-lived
signed URLs. Keep local disk for FFmpeg scratch files.

Before cutover, snapshot the existing SQLite records and copy the MP4s
while they are still available. Add schema migrations, verify record counts
and object links, and validate Mock end to end before moving live jobs.
This audit does not provision storage or migrate app data.
