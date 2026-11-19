# Google Drive pilot

The owner selected Google Drive for the initial editor. This pilot stores assets,
immutable plan snapshots, render job records, usage records, and previews in one
private Drive folder. It does not require PostgreSQL or Supabase. The previous
Postgres worker remains available as a later backend option.

Use one Streamlit instance and one render worker for the folder. The UI serializes
writes within its process; this is a small private pilot rather than a distributed
queue. The worker can run on the owner's computer. It must remain running to pick
up queued previews. The existing engine and PRD reference/quality gates still
apply.

## Authorize the app to use Drive

The Streamlit app needs its own server authorization to the account that owns the
folder. Its credentials are not supplied by the Drive connector used in this chat.

1. In Google Cloud, enable the Google Drive API. Configure an OAuth consent screen
   and create an OAuth client with type **Desktop app**. Download its JSON to a
   secure location outside the Git checkout.
2. Install `editor/drive-requirements.txt` locally. From the repository root, run
   the setup helper and sign in to the account that owns the editor folder.

```sh
python -m pip install -r editor/drive-requirements.txt
python -m editor.authorize_drive --client /secure/desktop-client.json --output /secure/editor.credentials.json
```

The helper opens Google's consent page in your browser and writes the result to
the requested file. It never prints tokens. Keep that file outside Git.

Official references:
[Drive upload authorization](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
and [OAuth for installed apps](https://developers.google.com/identity/protocols/oauth2/native-app).

## Configure Streamlit

After this draft is reviewed and the engine gate is accepted, enable the editor
using these **server-side Streamlit Secrets**. Keep other Secrets entries needed
by the current deployment. Replace the values using the saved authorization file.

```toml
EDITOR_UI_ENABLED = true
EDITOR_WORKSPACE_ID = "farah-editor"
EDITOR_ACCESS_PASSWORD = "replace-with-a-strong-unique-password-at-least-16-characters"
GOOGLE_DRIVE_FOLDER_ID = "your-private-editor-folder-id"

[GOOGLE_DRIVE_OAUTH]
client_id = "from-the-authorization-file"
client_secret = "from-the-authorization-file"
refresh_token = "from-the-authorization-file"
token_uri = "https://oauth2.googleapis.com/token"
```

Use the same Desktop OAuth client and authorization on the Streamlit server and
worker. Drive application metadata is private to its OAuth application. All app
operations enforce the configured folder and workspace. The pilot password gives
access to that shared workspace; individual member roles are future work.
The application creates no public permissions.

Until `EDITOR_UI_ENABLED` is true, the existing Streamlit entrypoint continues
to serve the current generator. The flag permits review without changing the
deployed workflow on merge.

## Start the worker

Install FFmpeg with ffprobe, Pillow, and `editor/drive-requirements.txt` on the
worker computer. Set the environment variables below without putting credentials
in shell command text. Set `GOOGLE_DRIVE_OAUTH_FILE` to the protected
authorization JSON created by the helper.

```text
GOOGLE_DRIVE_FOLDER_ID
GOOGLE_DRIVE_OAUTH_FILE
EDITOR_WORKSPACE_ID
```

Run from the repository root:

```sh
python -m editor.drive_worker
```

Optional `FFMPEG` and `FFPROBE` values select executable paths. The local lock
prevents two workers on the same computer. Do not start workers on multiple
computers for this Drive pilot. Interrupted jobs resume with a separate render
attempt, up to three attempts. Each attempt creates a usage record and unique
output files, preserving earlier successful previews.

The optional container uses the same three environment values. Mount the
credentials file read-only and point `GOOGLE_DRIVE_OAUTH_FILE` at the mounted
path.

```sh
docker build -f editor/Dockerfile.drive -t superagentk-editor-drive .
```

## Editing workflow

- Sign in with the studio password.
- Under **Footage and style files**, upload footage, a PNG logo, and a licensed
  font. Or register a file already directly inside the configured Drive folder
  using its Drive link. Register every source before using it in a plan.
- Under **Build the edit**, choose the output formats, enter confirmed title/CTA
  copy, edit speaker facts/consent, and select clips with their in/out times.
  Included rows play in the displayed order. Append rows for additional cuts.
  Confirm pause boundaries after listening to the original footage.
- **Validate and queue preview** checks the plan and saves it to Drive. A repeat
  submission of the same version reuses its jobs. Changed plans require a new
  version.
- **Library** shows queued, rendering, ready, or failed status per format. Retry
  a failed preview independently. **Revise this version** creates an editable
  copy with a higher version number.
- A completed preview opens in Google Drive for private playback and download.
  Its permissions are those of the folder; the app does not publish the file.

The upload control accepts up to 100 MB per file. Larger originals up to the
worker's 2 GB input bound can be placed in the folder through Drive and registered
by link. Automatic recursive folder imports and participant grouping are pending.
Speaker detection, transcription, agent drafting, word captions/translations,
review comments, measured quality gates and approved master exports are also
future milestones. This PR offers a manual preview workflow.

Drive plan/job records are application JSON files. Folder contents remain editable
by the Drive owner, so use the editor UI to revise versions and preserve old plans.
Do not edit queue JSON while the worker is running. Concurrent writers on multiple
Streamlit deployments and multiple worker hosts require a transactional queue
backend and are outside this pilot.

## Persistence and costs

Previews, files, plans and job/usage records live in Drive. Streamlit uses uploaded
file buffers in memory and writes no new editor SQLite database or local media
files. FFmpeg scratch files live only on the render worker and are deleted after
processing.

Render minutes are wall-clock processing time. Estimated USD is zero in this
local-worker pilot. Interrupted records show their status and need reconciliation
if exact time/cost accounting is required. Transcription and text-model usage tracking remains a later PRD milestone.
