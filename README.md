## Video Editor replacement in progress

The November 19 PRD and owner decision change the target to editing real footage.
The [engine milestone](editor/README.md) adds versioned plans, rendering and
persistent worker storage. The current deployment remains on the generator until
the reference-reel gate and the subsequent editor UI milestone are ready.

# SuperAgentK Video Studio

A private Streamlit app with a bundled Mock provider and live BytePlus ModelArk text-to-video. Mock works immediately.

## Deploy to Streamlit Community Cloud

1. Sign in at [Streamlit Community Cloud](https://share.streamlit.io/) and connect the GitHub account that owns the private repository. Grant private-repository access when Streamlit prompts for it.
2. Choose **Create app** and use repository **farahsfyh/superagentk-video-studio**, branch **main**, and entrypoint **streamlit_app.py**.
3. Keep the app private. Anyone allowed to open it can see this studio's shared library and use its BytePlus account.
4. For live video, add the following to **Advanced settings → Secrets**. Replace the illustrative values with your configuration. Use a ModelArk key and activated model or endpoint from the same region.

```toml
BYTEPLUS_API_KEY = "your-modelark-api-key"
BYTEPLUS_MODEL_ID = "dreamina-seedance-2-0-mini-260615"
BYTEPLUS_REGION = "ap-southeast-1"
BYTEPLUS_ESTIMATED_USD_PER_SECOND = "0.10"
BYTEPLUS_MONTHLY_CAP_USD = "10.00"
```

Use **Models → Test Seedance key** to check key access without submitting a video. BytePlus verifies access to the selected model when a video is submitted. The studio spend cap uses your entered estimate. BytePlus bills the actual usage.

## Run locally

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py --server.address 127.0.0.1
```

Set the same values as environment variables or in the ignored `.streamlit/secrets.toml` file.

## Storage

Job metadata, brand presets and MP4s use SQLite and the server's local disk. Set `STUDIO_DATA_DIR` to a persistent volume when self-hosting. Community Cloud local files can be lost when the app is rebuilt or restarted, so download clips you need to keep. Open the job in the library to resume checking a pending BytePlus generation.

Existing videos, presets, and keys in the Sites app require a separate migration. Enter the model credentials in Streamlit Secrets.

## Streamlit deployment

The existing app uses branch `main` and the entrypoint
`SuperAgentK Streamlit GitHub files/streamlit_app.py`.
Install dependencies from the root `requirements.txt`.

## Storyboards

In **Create video**, select **Storyboard**, choose a provider and aspect ratio,
then edit the names, prompts, and lengths of two to five scenes. Review the
estimated generation cost before submitting. Keep the storyboard open to
submit and check scene jobs, or resume from **Library → Storyboards**.
You can edit and retry a failed scene individually. Download the combined
silent MP4 when all scenes finish and FFmpeg has assembled them.

Streamlit Community Cloud installs FFmpeg from `packages.txt`. For local
runs, install FFmpeg on your PATH or set `STUDIO_FFMPEG` to its executable.
