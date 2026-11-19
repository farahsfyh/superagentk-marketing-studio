# Video Editor engine

This is the first implementation milestone for replacing the generator with a
real-footage editor. The deployed Streamlit generator is still the current UI
until the editor meets its reference-reel gate and a subsequent UI PR is merged.

## Run a local render test

Install Python dependencies and FFmpeg (including ffprobe).

```sh
python -m pip install -r editor/requirements.txt
python -m unittest discover -s editor/tests -v
```

On Windows set `EDITOR_TEST_FONT` to the absolute path of a licensed installed
TTF. Linux container tests default to DejaVu Sans. Optional `FFMPEG` and
`FFPROBE` environment values select executable paths.

Create a plan from `example-plan.json`, replace all example copy with facts
you have confirmed, and specify your media, font and logo object keys. For every
visible person, supply a confirmed people row with consent. Name cards require
name, title and company. The example's empty people array is only appropriate
for footage without visible people. Confirm the font licence and pause boundaries
after checking the actual sources.

For local engine QA, make an input map JSON whose keys match the plan and whose
values are absolute local file paths. Use a fresh output directory for each
version and format.

```sh
python -m editor.cli validate editor/example-plan.json
python -m editor.cli render-local plan.json --inputs inputs.json --output scratch/version-1-preview --format 9:16
```

The local command is for development and the reference-reel test on a worker
machine. Hosted originals, plans and results belong in persistent storage.
It creates an MP4, a thumbnail, SHA-256 and a report listing checks that still
need review. A technical render is not a verified PRD export.

## Hosted worker setup

1. Create a hosted Supabase PostgreSQL project and a private Storage bucket named
   `video-editor`. Apply `migrations/001_video_editor.sql` once as an owner.
   It adds the isolated `video_editor` schema, workspace/member records,
   immutable versioned plans, render queue and usage ledger.
2. Insert the workspace and member rows as an operator. Use the signed-in
   account's real UUID as the member ID. Read policies use Supabase
   `auth.uid()`. The worker's server-only database connection must have the
   intended write permissions, and must never be handed to the browser.
3. Set the worker's environment secrets through the hosting provider:
   `EDITOR_DATABASE_URL` (PostgreSQL URI with TLS),
   `SUPABASE_URL` (HTTPS origin), `SUPABASE_SERVICE_ROLE_KEY`.
   Optional values are `EDITOR_BUCKET`, `EDITOR_RENDER_USD_PER_MINUTE`,
   and `EDITOR_MAX_SOURCE_BYTES` (default 2 GiB per input).
4. Build and run the worker container from the repository root.

```sh
docker build -f editor/Dockerfile -t superagentk-editor .
docker run --rm --env-file /secure/path/editor.env superagentk-editor
```

Keep the environment file outside the Git checkout. Each worker claims a
database job with `FOR UPDATE SKIP LOCKED`, renews its lease while processing,
and writes outputs under a unique attempt prefix. Expired leases can be reclaimed
up to three attempts. A successful completion and its usage entry are committed
in one transaction. An interrupted attempt leaves its initial ledger row and
needs operational reconciliation for actual render time.

## Import and queue a plan

Local-file ingestion is available as an operator command. Drive/OneDrive folder
import is planned for the ingest milestone and is not implemented here. Use
workspace-prefixed object keys. Upload originals and style files before queueing.

```sh
python -m editor.cli upload /local/clip1.mp4 --key example/originals/clip1.mp4
python -m editor.cli upload /local/logo.png --key example/style/logo.png
python -m editor.cli upload /local/font.ttf --key example/style/heading.ttf
python -m editor.cli enqueue plan.json --quality preview
```

Master downloads are blocked while any required PRD quality check remains pending.
The CLI is trusted operator tooling. A future web service must derive workspace
and approval identity from authenticated membership. Browser clients must never
call it with the server's privileged credentials. Master jobs require a recorded
editor/owner approver using `--approved-by ACCOUNT_UUID`. Save a new plan version
for revisions. Every queue submission returns independent job IDs, so an operator
can retry without overwriting a good render. Operator download links last five
minutes and are intended only for authorized recipients.

```sh
python -m editor.cli enqueue approved-plan.json --quality master --approved-by ACCOUNT_UUID
python -m editor.cli download-link JOB_UUID
```

## Implemented engine scope

- Plan validation, confirmed speaker fields and consent, explicit checked pauses,
  stable consecutive-shot framing, caption timing and target-length validation.
- Per-segment video and its own speech/silence, constant 30 fps, fixed fit or
  centre crop, true file logo overlay, phone-sized text, timed line captions,
  title over the first footage frame, end card, and final loudness processing.
- Four aspect ratios, 540p-short-edge previews, full-size masters, thumbnails,
  file checksums, output duration/audio/dimension checks.
- Private object storage, checksum verification after upload, immutable plans,
  Postgres queue/lease recovery, workspace read policies and a usage ledger.

## Gate before Streamlit replacement

The November scripts and reference footage were not available during this
implementation. Their exact crop, cards and caption behaviour still needs to be
ported and compared with the shipped reference reel. The hosted migration,
storage policies and concurrent worker recovery also need an integration test
against a configured Supabase project.

The PRD's full quality gate remains open. Sub-3ms audio offset measurement,
pause-energy checks, frame-flash detection, face-aware crops/layouts, contrast
analysis and measured final loudness checks are pending. A render report marks
these as requiring review. HDR inputs are blocked pending normalization.
The engine currently accepts SDR H.264/HEVC video, a PNG logo, and licensed
TTF/OTF fonts. Photos, SVG logos, music, word highlighting and translation are
later renderer additions.

Follow-on work includes reference tests, automatic transcription and face
analysis, the real-footage Streamlit ingest/review UI, style-kit uploads,
comments/version comparison, custom templates, ad cutdowns, share copies and
Drive/OneDrive import/export. The PRD specifies that these use the same immutable
plan and persistent data layer.

Do not call the complete Video Editor finished until the PRD's two-workspace
acceptance tests and every export check pass.
