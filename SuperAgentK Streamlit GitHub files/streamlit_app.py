"""SuperAgentK Video Studio for private Streamlit hosting."""
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

# Community Cloud launches this nested entrypoint from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio
import storyboard

def editor_ui_enabled():
    value = os.environ.get("EDITOR_UI_ENABLED")
    if value is None:
        try:
            value = st.secrets.get("EDITOR_UI_ENABLED", True)
        except StreamlitSecretNotFoundError:
            value = True
    return value is True or isinstance(value, str) and value.lower() in ("true", "1")


if editor_ui_enabled():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from editor.app import main as editor_main
    editor_main()
    st.stop()

st.set_page_config(page_title="SuperAgentK Video Studio", page_icon="🎬", layout="wide")
st.markdown("""
<style>
[data-testid="stSidebar"] {border-right:1px solid #36332d;}
.block-container {max-width:1130px; padding-top:2.4rem;}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius:14px;}
</style>
""", unsafe_allow_html=True)


def secret(name, default=""):
    if name in os.environ:
        return os.environ[name]
    try:
        return st.secrets.get(name, default)
    except StreamlitSecretNotFoundError:
        return default


def cents(value):
    try:
        number = Decimal(str(value))
        if number.is_finite() and 0 < number <= 1000000:
            return max(1, round(number * 100))
    except InvalidOperation:
        pass
    return None


settings = {
    "key": str(secret("BYTEPLUS_API_KEY")).strip(),
    "model": str(secret("BYTEPLUS_MODEL_ID", "dreamina-seedance-2-0-mini-260615")),
    "region": str(secret("BYTEPLUS_REGION", "ap-southeast-1")),
}
rate = cents(secret("BYTEPLUS_ESTIMATED_USD_PER_SECOND"))
cap = cents(secret("BYTEPLUS_MONTHLY_CAP_USD"))
live_ready = bool(settings["key"] and rate and cap and settings["region"] in studio.REGIONS)

if "next_page" in st.session_state:
    st.session_state["workspace"] = st.session_state.pop("next_page")

with st.sidebar:
    st.markdown("### SuperAgentK")
    st.caption("VIDEO STUDIO")
    page = st.radio("Workspace", ("Create video", "Library", "Brand kit", "Usage", "Models"), key="workspace")
    st.divider()
    st.caption("Private studio · Streamlit")

if "next_prompt" in st.session_state:
    st.session_state["idea"] = st.session_state.pop("next_prompt")
if "next_ratio" in st.session_state:
    st.session_state["format"] = st.session_state.pop("next_ratio")
if "next_provider" in st.session_state:
    st.session_state["provider"] = st.session_state.pop("next_provider")


def remix(job):
    st.session_state.update(next_prompt=job["prompt"], next_ratio=job["ratio"],
                            next_provider=job["provider"], next_page="Create video")


@st.fragment(run_every=8)
def job_details(job_id):
    job = studio.get_job(job_id)
    if job is None:
        st.info("This job is unavailable.")
        return
    with st.container(border=True):
        st.markdown("#### Generation status")
        banner = st.empty()

        def show_status(status):
            if status == "Ready":
                banner.success("Ready · Video completed")
            elif status == "Failed":
                banner.error("Failed · Video could not be generated")
            elif status == "Draft":
                banner.info("Draft · Saved without submitting")
            elif status == "Queued":
                banner.info("Queued · Waiting for BytePlus to accept the job")
            elif status == "Finishing":
                banner.info("Finishing · Saving your video")
            else:
                banner.info("Generating · BytePlus is working on your video")

        show_status(job["status"])
        if job["status"] in studio.ACTIVE:
            try:
                studio.poll(job, settings)
            except studio.StudioError as exc:
                st.warning(str(exc))
            job = studio.get_job(job_id)
            show_status(job["status"])
        st.caption(f"{job['provider']} · {job['ratio']} · ${job['cents'] / 100:.2f} estimate")
        st.write(job["prompt"])
        if job["provider_id"]:
            st.caption(f"BytePlus task ID: {job['provider_id']}")
        if job["error"]:
            st.error(job["error"])
        if job["status"] in studio.ACTIVE:
            st.info("Checking for a result every eight seconds. Return to this job if you leave the studio.")
        if job["status"] == "Ready":
            media = studio.media_path(job)
            if media.is_file():
                st.video(str(media))
                st.download_button("Download MP4", media.read_bytes(), f"superagentk-{job_id}.mp4",
                                   mime="video/mp4", key=f"download-{job_id}")
            else:
                st.warning("The video file is unavailable.")
        if job["status"] not in studio.ACTIVE:
            st.button("Use this prompt", key=f"remix-{job_id}", on_click=remix, args=(job,))


@st.fragment(run_every=8)
def storyboard_progress(project_id):
    project, scenes = storyboard.load(project_id)
    if not project:
        st.info("This storyboard is unavailable.")
        return
    st.subheader(project["title"])
    st.caption(f'{project["provider"]} · {project["ratio"]} · {sum(s["duration"] for s in scenes)} seconds planned')
    pending = any(scene["status"] in (*studio.ACTIVE, "Waiting") for scene in scenes)
    output = storyboard.export_path(project_id)
    renderable = all(scene["status"] == "Ready" for scene in scenes) and not output.is_file() and not project["render_error"]
    if pending or renderable:
        message = st.empty()
        message.info("Checking scene progress…" if pending else "Stitching the scene clips…")
        try:
            storyboard.advance(project, scenes, settings)
        except studio.StudioError as exc:
            message.warning(str(exc))
        else:
            message.empty()
        project, scenes = storyboard.load(project_id)
    ready_count = sum(scene["status"] == "Ready" for scene in scenes)
    st.progress(ready_count / len(scenes), text=f"{ready_count} of {len(scenes)} scenes ready")
    for scene in scenes:
        with st.expander(f'{scene["position"]}. {scene["label"]} · {scene["duration"]}s · {scene["status"]}'):
            st.write(scene["prompt"])
            if scene["provider_id"]:
                st.caption(f'BytePlus task ID: {scene["provider_id"]}')
            if scene["error"]:
                st.error(scene["error"])
            if scene["status"] == "Failed":
                with st.form(f'retry-{scene["id"]}'):
                    prompt = st.text_area("Edit scene prompt", scene["prompt"], max_chars=6000)
                    st.caption(f"Estimated retry cost ${scene['duration'] * (rate or 0) / 100:.2f}")
                    if st.form_submit_button("Retry this scene", disabled=not live_ready):
                        try:
                            storyboard.retry(project_id, scene["id"], prompt, settings, rate, cap)
                        except studio.StudioError as exc:
                            st.error(str(exc))
                        else:
                            st.rerun(scope="app")
    if output.is_file():
        st.success("Ready · Combined video completed")
        st.video(str(output))
        st.download_button("Download combined MP4", output.read_bytes(), f"storyboard-{project_id}.mp4",
                           mime="video/mp4", key=f"combined-{project_id}")
    elif project["render_error"]:
        st.error(project["render_error"])
        if st.button("Retry stitching", key=f"restitch-{project_id}"):
            storyboard.retry_export(project_id)
            st.rerun(scope="app")
    elif ready_count == len(scenes):
        st.info("All scenes are ready. Stitching will finish here.")
    elif any(scene["status"] in (*studio.ACTIVE, "Waiting") for scene in scenes):
        st.info("Keep this page open to submit and check scenes. You can resume from Library → Storyboards.")
    else:
        st.warning("Retry the failed scenes to finish the combined video.")


def storyboard_editor():
    left, right = st.columns([2, 1], gap="large")
    with left:
        st.subheader("Edit your scenes")
        provider = st.radio("Storyboard provider", ["Mock", "Seedance"], horizontal=True)
        live = provider == "Seedance"
        title = st.text_input("Storyboard title", placeholder="15-second product launch", max_chars=100)
        ratio = st.selectbox("Storyboard aspect ratio", studio.LIVE_RATIOS if live else studio.RATIOS)
        count = st.selectbox("Number of scenes", [2, 3, 4, 5], index=1)
        labels = ["Hook", "Product demo", "Call to action"] if count == 3 else ["Hook", *[f"Scene {index}" for index in range(2, count)], "Call to action"]
        limit = storyboard.max_scene_seconds(settings["model"]) if live else 15
        scenes = []
        for index, label in enumerate(labels):
            with st.container(border=True):
                name_col, time_col = st.columns([3, 1])
                label = name_col.text_input(f"Scene {index + 1} name", value=label, max_chars=80, key=f"scene-label-{count}-{index}")
                seconds = time_col.number_input(f"Scene {index + 1} seconds", 4, limit, 5, key=f"scene-seconds-{index}")
                prompt = st.text_area(f"Scene {index + 1} prompt", max_chars=6000,
                                      placeholder="Describe the subject, action, setting, and camera.",
                                      key=f"scene-prompt-{index}", height=95)
                scenes.append((label, prompt, seconds))
        with right, st.container(border=True):
            st.subheader("Export")
            seconds = sum(scene[2] for scene in scenes)
            estimated = seconds * (rate or 0) / 100 if live else 0
            st.metric("Planned duration", f"{seconds} seconds")
            st.metric("Estimated generation cost", f"${estimated:.2f}" if not live or live_ready else "Unavailable")
            st.caption("One silent MP4, with scenes in the order shown.")
            if live and not live_ready:
                st.warning("Configure Seedance in Streamlit Secrets to generate live scenes.")
            if provider == "Mock":
                st.caption("Mock repeats the bundled sample in each scene for testing.")
            if not storyboard.ffmpeg():
                st.warning("Install FFmpeg or set STUDIO_FFMPEG before exporting.")
        status = st.empty()
        if st.button("Generate storyboard", type="primary", disabled=(live and not live_ready) or not storyboard.ffmpeg()):
            try:
                st.session_state["current_storyboard"] = storyboard.create(
                    title, scenes, ratio, provider, settings, (rate or 0) if live else 0, cap if live else None)
            except studio.StudioError as exc:
                status.error(str(exc))
        projects = storyboard.list_projects()
        project_id = st.session_state.get("current_storyboard") or (projects[0]["id"] if projects else None)
        if project_id:
            storyboard_progress(project_id)


if page == "Create video":
    st.title("Create a video")
    st.caption("Describe one short clip and choose the provider.")
    mode = st.radio("Video mode", ["Single clip", "Storyboard"], horizontal=True)
    if mode == "Storyboard":
        storyboard_editor()
    else:
        main, summary = st.columns([2, 1], gap="large")
        with main:
            provider = st.radio("Provider", ["Mock", "Seedance"], horizontal=True, key="provider")
            live = provider == "Seedance"
            ratios = studio.LIVE_RATIOS if live else studio.RATIOS
            if st.session_state.get("format") not in ratios:
                st.session_state["format"] = ratios[0]
            with st.form("create", clear_on_submit=False):
                idea = st.text_area("What video do you want to make?", height=175, max_chars=5000, key="idea",
                                    placeholder="A cinematic shot of mist drifting over a rainforest at sunrise.")
                ratio = st.selectbox("Aspect ratio", ratios, key="format")
                available_presets = studio.presets()
                preset = st.selectbox("Brand preset", ["None", *available_presets])
                refinements = st.text_input("Optional visual or camera direction", max_chars=300)
                sent = st.form_submit_button("Generate video", type="primary", disabled=live and not live_ready)
                draft = st.form_submit_button("Save draft")
            submission = st.empty()
            if sent or draft:
                prompt = "\n".join(part for part in [idea.strip(), refinements.strip(), available_presets.get(preset)] if part)
                try:
                    if not idea.strip():
                        raise studio.StudioError("Describe the clip before submitting.")
                    if sent and live:
                        submission.info("Submitting to BytePlus…")
                    st.session_state.current_job = studio.start_job(
                        prompt, ratio, provider, settings, cap if live else None,
                        (rate or 0) if live else 0, draft=draft,
                    )
                except studio.StudioError as exc:
                    submission.error(str(exc))
                else:
                    submission.empty()
            latest_job_id = st.session_state.get("current_job") or studio.latest_job_id()
            if latest_job_id:
                job_details(latest_job_id)
            else:
                st.caption("Generation status appears here after you submit a prompt.")
        with summary, st.container(border=True):
            st.markdown("#### Output")
            st.image(str(studio.ASSETS / "still.jpg"))
            if live:
                st.write("Seedance · five seconds · 720p")
                if live_ready:
                    st.metric("Estimated per clip", f"${(rate * 5) / 100:.2f}")
                else:
                    st.info("Set the BytePlus key, rate, and cap in Streamlit Secrets to enable Seedance.")
            else:
                st.write("Mock · five seconds · bundled sample")
                st.metric("Estimated per clip", "$0.00")

elif page == "Library":
    st.title("Library")
    kind = st.radio("Library view", ["Single clips", "Storyboards"], horizontal=True)
    if kind == "Storyboards":
        projects = {project["id"]: project for project in storyboard.list_projects()}
        if projects:
            project_id = st.selectbox("Storyboard", projects, format_func=lambda key: projects[key]["title"])
            storyboard_progress(project_id)
        else:
            st.info("No storyboards yet.")
    else:
        jobs = studio.single_jobs()
        query = st.text_input("Search prompts")
        status = st.selectbox("Status", ["All", "Draft", "Queued", "Generating", "Finishing", "Ready", "Failed"])
        matches = {job["id"]: job for job in jobs if (status == "All" or job["status"] == status)
                   and query.casefold() in job["prompt"].casefold()}
        if matches:
            selection = st.selectbox("Video", matches, format_func=lambda key: f"{matches[key]['status']} · {matches[key]['title']}")
            job_details(selection)
        else:
            st.info("No matching videos.")

elif page == "Brand kit":
    st.title("Brand kit")
    st.caption("Save reusable brand notes to add to new prompts.")
    with st.form("preset_form"):
        name = st.text_input("Preset name", max_chars=80)
        notes = st.text_area("Brand notes", max_chars=600, placeholder="Palette, visual style, voice, product notes, or CTA.")
        if st.form_submit_button("Save preset", type="primary"):
            if not name.strip() or name.strip().casefold() == "none" or not notes.strip():
                st.error("Enter a preset name and brand notes.")
            else:
                studio.save_preset(name.strip(), notes.strip())
                st.success("Preset saved.")
    for name, notes in studio.presets().items():
        with st.expander(name):
            st.write(notes)

elif page == "Usage":
    st.title("Usage")
    jobs = studio.all_jobs()
    first, second, third = st.columns(3)
    first.metric("Ready videos", sum(job["status"] == "Ready" for job in jobs))
    with studio.database() as conn:
        second.metric("Seedance estimate this month", f"${studio.monthly_spend(conn, 'Seedance') / 100:.2f}")
    third.metric("Monthly estimated cap", f"${cap / 100:.2f}" if cap else "Not configured")
    st.caption("BytePlus ModelArk shows actual billing.")
    st.dataframe([{"Prompt": job["title"], "Provider": job["provider"], "Status": job["status"],
                   "Estimate (USD)": job["cents"] / 100} for job in jobs], hide_index=True)
    st.download_button("Export CSV", studio.usage_csv(jobs), "superagentk-usage.csv", "text/csv")

else:
    st.title("Models")
    mock, seedance = st.columns(2)
    with mock, st.container(border=True):
        st.markdown("#### Mock")
        st.write("Available · no credits needed.")
    with seedance, st.container(border=True):
        st.markdown("#### BytePlus Seedance")
        st.write(f"Model: {settings['model']}")
        st.write(f"Region: {settings['region']}")
        st.caption("The API key is read server-side from Streamlit Secrets.")
        if not live_ready:
            st.warning("Live generation needs a key, a supported region, an estimated rate, and a spend cap.")
        if st.button("Test Seedance key", disabled=not settings["key"]):
            try:
                studio.ark(settings, "?page_size=1")
                st.success("BytePlus accepted the key. Model access is checked when you generate a clip.")
            except studio.StudioError as exc:
                st.error(str(exc))
