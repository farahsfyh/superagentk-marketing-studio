"""Private Streamlit upload and manual review surface for the Video Editor."""
import copy
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from editor.forms import build_plan
from editor.plan import FORMATS, PlanError
from editor.service import EditorSession

REQUIRED = ("GOOGLE_DRIVE_FOLDER_ID", "GOOGLE_DRIVE_OAUTH", "EDITOR_WORKSPACE_ID", "EDITOR_ACCESS_PASSWORD")
TEMPLATES = {
    "Interview / testimonial reel": "interview_reel",
    "Session + interview reel": "session_interview",
    "Event recap": "event_recap",
    "Ad cutdown": "ad_cutdown",
}
PALETTES = {
    "Corporate Blue": ("#10263D", "#FFFFFF", "#66B6EF"),
    "Warm Editorial": ("#F3EBDE", "#29231F", "#A94720"),
    "Bold Builder": ("#211437", "#FFFFFF", "#7DE5E1"),
    "Energetic Warm": ("#38190F", "#FFFFFF", "#FFAD70"),
    "Minimal Mono": ("#171717", "#FFFFFF", "#D8D8D8"),
}


def settings():
    def value(key):
        if key in os.environ:
            return os.environ[key]
        try:
            return st.secrets.get(key, "")
        except StreamlitSecretNotFoundError:
            return ""
    config = {key: value(key) for key in REQUIRED}
    if not config["GOOGLE_DRIVE_OAUTH"] and os.environ.get("GOOGLE_DRIVE_OAUTH_JSON"):
        config["GOOGLE_DRIVE_OAUTH"] = json.loads(os.environ["GOOGLE_DRIVE_OAUTH_JSON"])
    return config


def unlock(password):
    if len(password) < 16:
        st.error("Set EDITOR_ACCESS_PASSWORD to a unique password of at least 16 characters.")
        return False
    expected = hashlib.sha256(password.encode()).digest()
    if hmac.compare_digest(st.session_state.get("editor_authenticated", b""), expected):
        return True
    st.title("Video Editor")
    with st.form("editor_sign_in", clear_on_submit=True):
        supplied = st.text_input("Studio password", type="password")
        if st.form_submit_button("Open workspace", type="primary"):
            if hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(), expected):
                st.session_state.editor_authenticated = expected
                st.rerun()
            st.error("The password did not match.")
    return False


def setup(config):
    st.title("Set up Video Editor")
    st.write("Connect a private Google Drive folder and the render worker to start editing real footage.")
    missing = [key for key in REQUIRED if not config[key]]
    st.info("Missing Streamlit Secrets · " + ", ".join(missing))
    st.markdown("[Worker and storage setup](https://github.com/farahsfyh/superagentk-marketing-studio/blob/main/editor/README.md)")
    st.caption("Footage, edit plans, job status, and outputs are saved in Drive. Run one render worker on your computer to process previews.")


def uploads(session):
    st.title("Footage and style files")
    st.write("Upload MP4 or MOV footage, a PNG logo, and a licensed TTF or OTF font to your private Google Drive folder.")
    st.caption("Up to 100 MB per file for this upload milestone. You can also register files already in the configured Drive folder.")
    kind = st.selectbox("File category", ["footage", "logo", "font"])
    with st.form("editor_upload", clear_on_submit=True):
        files = st.file_uploader("Choose files", type=list({"footage": ["mp4", "mov"], "logo": ["png"], "font": ["ttf", "otf"]}[kind]),
                                 accept_multiple_files=True)
        rights = st.checkbox("We have the right to use these files, including the font licence where applicable.")
        if st.form_submit_button("Save to workspace", type="primary"):
            if not files:
                st.warning("Choose at least one file.")
            elif not rights:
                st.error("Confirm your right to use the files before uploading.")
            else:
                for source in files:
                    try:
                        with st.spinner(f"Saving {source.name}…"):
                            session.upload(source, source.name, kind, rights)
                    except PlanError as error:
                        st.error(str(error))
                    except Exception:
                        st.error("Storage could not finish saving this file. Check the workspace connection.")
                    else:
                        st.success(f"Saved and checksum verified · {source.name}")
    with st.form("register_drive_file"):
        link = st.text_input("Existing file link in the configured Drive folder")
        imported_kind = st.selectbox("Existing file category", ["footage", "logo", "font"])
        permission = st.checkbox("We have the right to use this existing file.")
        if st.form_submit_button("Add existing Drive file"):
            try:
                session.register(link, imported_kind, permission)
            except PlanError as error:
                st.error(str(error))
            else:
                st.success("Drive file registered.")
    assets = session.assets()
    st.dataframe([{"Name": item["original_name"], "Category": item["kind"],
                   "Size (MB)": round(item["byte_size"] / 1024**2, 2)} for item in assets], hide_index=True)


def select_asset(label, values, selected_key=None):
    return st.selectbox(label, values, index=next((i for i, a in enumerate(values) if a["object_key"] == selected_key), 0),
                        format_func=lambda item: item["original_name"])


def initial_tables(plan):
    if not plan:
        return ([{"Name": "", "Title": "", "Company": "", "Confirmed": False, "Permission": False}],
                [{"Include": True, "Source": "", "In (s)": 0.0, "Out (s)": 5.0,
                  "Role": "broll", "People": "", "Name card": "", "Audio": "speech",
                  "Framing": "fit", "Pause checked": False, "Caption": ""}])
    people = [{"Name": p["name"], "Title": p.get("title", ""), "Company": p.get("company", ""),
               "Confirmed": p["confirmed"], "Permission": p["consent"]} for p in plan["people"].values()]
    cuts = []
    for segment in plan["segments"]:
        key = plan["clips"][segment["clip"]]["original_key"]
        cuts.append({
            "Include": True, "Source": key, "In (s)": float(segment["in"]), "Out (s)": float(segment["out"]),
            "Role": segment["role"], "People": ", ".join(plan["people"][p]["name"] for p in segment["people"]),
            "Name card": plan["people"][segment["name_card"]]["name"] if segment.get("name_card") else "",
            "Audio": segment.get("audio", "speech"), "Framing": segment.get("frame", "fit"),
            "Pause checked": segment["pause_checked"],
            "Caption": " ".join(c["text"] for c in segment.get("captions", [])),
        })
    return people, cuts


@st.fragment(run_every=8)
def render_status(session, plan_id=None):
    jobs = session.jobs(plan_id)
    if not jobs:
        st.info("No render jobs yet.")
        return
    for job in jobs:
        with st.container(border=True):
            st.markdown(f"**{job['project_id']} · v{job['version']} · {job['format']}**")
            if job["state"] in ("queued", "running"):
                st.info("Queued · Waiting for the render worker" if job["state"] == "queued" else "Rendering · The worker is processing this edit")
            elif job["state"] == "failed":
                st.error(job["error"] or "The worker could not complete this render.")
                if st.button("Retry preview", key=f"editor-retry-{job['id']}"):
                    try:
                        session.retry(job["id"])
                    except PlanError as error:
                        st.warning(str(error))
                    else:
                        st.rerun(scope="app")
            else:
                st.success("Preview ready · Review the result")
                if job["output"].get("review_required"):
                    st.warning("Manual review still required · " + ", ".join(job["output"]["review_required"]))
                if st.button("Open preview", key=f"editor-open-{job['id']}"):
                    link = session.preview_link(job["id"])
                    st.link_button("Play or download in Google Drive", link)
                    st.caption("The file stays private under your Drive folder's existing permissions.")


def edit_plan(session):
    st.title("Build the edit")
    st.write("Select real footage, confirm the people on screen, and choose the cuts for a preview.")
    st.caption("Cuts and captions are reviewed manually in this milestone. Automatic transcription and agent drafting come next.")
    assets = session.assets()
    footage = [a for a in assets if a["kind"] == "footage"]
    logos = [a for a in assets if a["kind"] == "logo"]
    fonts = [a for a in assets if a["kind"] == "font"]
    if not all((footage, logos, fonts)):
        st.info("Add footage, a PNG logo, and a licensed font under Footage and style files first.")
        return
    previous = st.session_state.get("editor_revision")
    if previous and st.button("Start a new project"):
        st.session_state.pop("editor_revision")
        st.rerun()
    defaults = previous or {}
    style = defaults.get("style", {})
    project = st.text_input("Project ID", value=defaults.get("project_id", ""), placeholder="event-recap")
    left, right = st.columns(2)
    version = left.number_input("Version", 1, 100000, defaults.get("version", 1))
    template_name = right.selectbox("Template", list(TEMPLATES), index=list(TEMPLATES.values()).index(defaults.get("template", "event_recap")))
    title = st.text_input("Confirmed event or product title", value=defaults.get("title", ""), max_chars=200)
    cta = st.text_input("Confirmed call to action", value=defaults.get("cta", ""), max_chars=200)
    formats = st.multiselect("Output formats", list(FORMATS), default=defaults.get("formats", ["9:16", "4:5"]))
    target = st.number_input("Maximum finished length (seconds)", 5, 95, int(defaults.get("target_seconds", 60)))
    st.caption("The total includes a two-second title and a three-second end card.")
    with st.expander("Project style", expanded=True):
        preset = st.selectbox("Starting palette", list(PALETTES))
        colours = st.columns(3)
        background = colours[0].color_picker("Background", style.get("background", PALETTES[preset][0]))
        text = colours[1].color_picker("Text", style.get("text", PALETTES[preset][1]))
        accent = colours[2].color_picker("Accent", style.get("accent", PALETTES[preset][2]))
        logo = select_asset("Logo", logos, style.get("logo_key"))
        font = select_asset("Font", fonts, style.get("font_key"))
    people_rows, cut_rows = initial_tables(defaults)
    by_key = {a["object_key"]: str(a["id"]) for a in footage}
    for row in cut_rows:
        row["Source"] = by_key.get(row["Source"], row["Source"])
    st.subheader("Confirm speakers")
    st.caption("Use the exact names, titles, and companies you want displayed. Record permission for every visible person.")
    people = st.data_editor(people_rows, num_rows="dynamic", hide_index=True, key=f"editor-people-{project}-{version}")
    st.subheader("Review cuts")
    st.caption("Rows play in this order. Add a row to append a cut. List all visible people by name, separated by commas.")
    labels = {str(a["id"]): a["original_name"] + " · " + str(a["id"])[-6:] for a in footage}
    for row in cut_rows:
        row["Source"] = labels.get(row["Source"], next(iter(labels.values())))
    cuts = st.data_editor(cut_rows, num_rows="dynamic", hide_index=True, key=f"editor-cuts-{project}-{version}",
        column_config={
            "Source": st.column_config.SelectboxColumn("Source", options=list(labels.values()), required=True),
            "Role": st.column_config.SelectboxColumn("Role", options=["ask", "answer", "session", "broll"]),
            "Audio": st.column_config.SelectboxColumn("Audio", options=["speech", "mute"]),
            "Framing": st.column_config.SelectboxColumn("Framing", options=["fit", "center_crop"]),
            "In (s)": st.column_config.NumberColumn("In (s)", min_value=0),
            "Out (s)": st.column_config.NumberColumn("Out (s)", min_value=0),
        })
    if st.button("Validate and queue preview", type="primary"):
        try:
            selected_cuts = copy.deepcopy(cuts)
            sources = {label: asset_id for asset_id, label in labels.items()}
            for row in selected_cuts:
                row["Source"] = sources.get(row["Source"], "")
            plan = build_plan(session.workspace, project, int(version), TEMPLATES[template_name], title, cta,
                              target, formats, {"background": background, "text": text, "accent": accent,
                                               "font_key": font["object_key"], "font_licensed": font["confirmed_rights"],
                                               "logo_key": logo["object_key"]}, assets, people, selected_cuts)
            jobs = session.preview(plan)
        except PlanError as error:
            st.error(str(error))
        except Exception:
            st.error("The preview could not be queued. Check the workspace connection and whether this version already has a different plan.")
        else:
            st.success(f"Queued {len(jobs)} preview render(s). Follow progress in Library.")
            st.session_state.editor_revision = plan


def library(session):
    st.title("Library")
    plans = session.plans()
    if not plans:
        st.info("No saved edit plans yet. Build an edit and submit its first preview.")
        return
    plan = st.selectbox("Project and version", plans, format_func=lambda row: f"{row['project_id']} · v{row['version']}")
    if st.button("Revise this version"):
        revision = copy.deepcopy(plan["plan"])
        revision["version"] = max(p["version"] for p in plans if p["project_id"] == revision["project_id"]) + 1
        st.session_state.editor_revision = revision
        st.session_state.editor_page = "Build the edit"
        st.rerun()
    st.download_button("Download edit plan", json.dumps(plan["plan"], indent=2), "edit-plan.json", "application/json")
    render_status(session, plan["id"])


def main():
    st.set_page_config(page_title="SuperAgentK Video Editor", page_icon="🎬", layout="wide")
    config = settings()
    if any(not config[key] for key in REQUIRED):
        setup(config)
        return
    if not unlock(str(config["EDITOR_ACCESS_PASSWORD"])):
        return
    try:
        session = EditorSession(config)
        workspace_name = session.check()
        if "editor_page" in st.session_state:
            st.session_state.editor_navigation = st.session_state.pop("editor_page")
        with st.sidebar:
            st.title("Video Editor")
            st.caption(workspace_name)
            selected = st.radio("Workspace", ["Footage and style files", "Build the edit", "Library", "Usage"],
                                key="editor_navigation", index=0)
            if st.button("Sign out"):
                st.session_state.clear()
                st.rerun()
        if selected == "Footage and style files":
            uploads(session)
        elif selected == "Build the edit":
            edit_plan(session)
        elif selected == "Library":
            library(session)
        else:
            st.title("Usage")
            st.caption("Render time and estimates recorded by the worker for this workspace.")
            st.dataframe(session.usage(), hide_index=True)
    except PlanError as error:
        st.error(str(error))
    except Exception:
        st.error("The editor could not reach its workspace. Check the Drive folder, authorization, and render worker.")


if __name__ == "__main__":
    main()
