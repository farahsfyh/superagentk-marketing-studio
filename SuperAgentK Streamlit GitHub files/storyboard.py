"""Queue independent scene jobs and assemble their clips in storyboard order."""
import os
import shutil
import subprocess
import time
import uuid

import studio

DIMENSIONS = {
    "16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720),
    "4:5": (720, 900), "4:3": (960, 720), "3:4": (720, 960), "21:9": (1260, 540),
}


def ffmpeg():
    return os.environ.get("STUDIO_FFMPEG") or shutil.which("ffmpeg")


def max_scene_seconds(model):
    if model.startswith("dreamina-seedance-2-0-"):
        return 15
    return 12


def list_projects():
    with studio.database() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM storyboards ORDER BY created DESC")]


def load(project_id):
    with studio.database() as conn:
        project = conn.execute("SELECT * FROM storyboards WHERE id=?", (project_id,)).fetchone()
        if project is None:
            return None, []
        scenes = conn.execute("""SELECT jobs.*, scenes.position, scenes.label, scenes.duration, attempts.claimed
            FROM storyboard_scenes AS scenes JOIN jobs ON jobs.id=scenes.job_id
            JOIN storyboard_attempts AS attempts ON attempts.job_id=jobs.id
            WHERE scenes.storyboard_id=? ORDER BY scenes.position""", (project_id,)).fetchall()
        return dict(project), [dict(row) for row in scenes]


def insert_scene_job(conn, prompt, ratio, provider, settings, cost):
    job_id = uuid.uuid4().hex
    conn.execute("""INSERT INTO jobs (id, title, prompt, model, provider, region, ratio, status, cents, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, prompt[:74], prompt, settings["model"] if provider == "Seedance" else "Mock",
         provider, settings["region"] if provider == "Seedance" else "", ratio,
         "Waiting" if provider == "Seedance" else "Ready", cost, time.time()))
    conn.execute("INSERT INTO storyboard_attempts (job_id) VALUES (?)", (job_id,))
    return job_id


def check_cap(conn, provider, cost, cap):
    if cap is not None and studio.monthly_spend(conn, provider) + cost > cap:
        raise studio.StudioError("This exceeds the monthly estimated spend cap.")


def create(title, scenes, ratio, provider, settings, rate, cap):
    if not ffmpeg():
        raise studio.StudioError("FFmpeg is required to export a storyboard.")
    limit = max_scene_seconds(settings["model"]) if provider == "Seedance" else 15
    ratios = studio.LIVE_RATIOS if provider == "Seedance" else studio.RATIOS
    if not title.strip() or ratio not in ratios or not 2 <= len(scenes) <= 5:
        raise studio.StudioError("Enter a title and choose a format and two to five scenes.")
    if provider == "Seedance" and not (settings["key"] and rate and cap):
        raise studio.StudioError("Configure the Seedance key, rate, and cap before submitting.")
    if any(not label.strip() or not prompt.strip() or len(prompt) > 6000 or not 4 <= duration <= limit
           for label, prompt, duration in scenes):
        raise studio.StudioError(f"Give each scene a name, a prompt, and a duration from 4 to {limit} seconds.")
    project_id = uuid.uuid4().hex
    with studio.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        check_cap(conn, provider, sum(duration for _, _, duration in scenes) * rate, cap)
        conn.execute("INSERT INTO storyboards (id, title, provider, ratio, created) VALUES (?, ?, ?, ?, ?)",
                     (project_id, title.strip(), provider, ratio, time.time()))
        for position, (label, prompt, duration) in enumerate(scenes, start=1):
            job_id = insert_scene_job(conn, prompt.strip(), ratio, provider, settings, duration * rate)
            conn.execute("INSERT INTO storyboard_scenes VALUES (?, ?, ?, ?, ?)",
                         (project_id, position, label.strip(), duration, job_id))
    return project_id


def retry(project_id, job_id, prompt, settings, rate, cap):
    if not (settings["key"] and rate and cap) or not prompt.strip() or len(prompt) > 6000:
        raise studio.StudioError("Configure Seedance and enter a scene prompt before retrying.")
    with studio.database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        scene = conn.execute("""SELECT scenes.*, jobs.ratio, jobs.model, jobs.region FROM storyboard_scenes AS scenes
            JOIN jobs ON jobs.id=scenes.job_id WHERE scenes.storyboard_id=? AND jobs.id=? AND jobs.status='Failed'""",
            (project_id, job_id)).fetchone()
        if scene is None:
            raise studio.StudioError("This scene is no longer eligible to retry.")
        cost = scene["duration"] * rate
        check_cap(conn, "Seedance", cost, cap)
        new_id = insert_scene_job(conn, prompt.strip(), scene["ratio"], "Seedance",
                                  {**settings, "model": scene["model"], "region": scene["region"]}, cost)
        conn.execute("UPDATE storyboard_scenes SET job_id=? WHERE storyboard_id=? AND job_id=?",
                     (new_id, project_id, job_id))


def submit_waiting(scene, settings):
    if not settings["key"]:
        raise studio.StudioError("Configure the Seedance key to continue submitting scenes.")
    with studio.database() as conn:
        changed = conn.execute("UPDATE jobs SET status='Queued' WHERE id=? AND status='Waiting'",
                               (scene["id"],)).rowcount
        if not changed:
            return
        conn.execute("UPDATE storyboard_attempts SET claimed=? WHERE job_id=?", (time.time(), scene["id"]))
    studio.submit_seedance(scene["id"], scene["prompt"], scene["ratio"],
                           {**settings, "model": scene["model"], "region": scene["region"]}, scene["duration"])


def export_path(project_id):
    return studio.DATA / f"storyboard-{project_id}.mp4"


def stitch(project, scenes):
    executable = ffmpeg()
    if not executable:
        raise studio.StudioError("FFmpeg is unavailable on this server.")
    width, height = DIMENSIONS[project["ratio"]]
    command = [executable, "-hide_banner", "-loglevel", "error", "-y", "-filter_complex_threads", "1"]
    filters = []
    for index, scene in enumerate(scenes):
        media = studio.media_path(scene)
        if not media.is_file():
            raise studio.StudioError(f"Scene {scene['position']} is missing its video file.")
        if scene["provider"] == "Mock":
            command.extend(["-stream_loop", "-1", "-t", str(scene["duration"])])
        command.extend(["-threads", "1", "-i", str(media)])
        filters.append(f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p,"
                       f"setpts=PTS-STARTPTS[v{index}]")
    labels = "".join(f"[v{index}]" for index in range(len(scenes)))
    filters.append(f"{labels}concat=n={len(scenes)}:v=1:a=0[out]")
    final = export_path(project["id"])
    temp = final.with_suffix(f".{uuid.uuid4().hex}.mp4")
    command.extend(["-filter_complex", ";".join(filters), "-map", "[out]", "-an",
                    "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-movflags", "+faststart", str(temp)])
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=150)
        temp.replace(final)
    except (subprocess.SubprocessError, OSError):
        raise studio.StudioError("FFmpeg could not assemble the scenes. Your scene clips are still saved.") from None
    finally:
        temp.unlink(missing_ok=True)


def retry_export(project_id):
    with studio.database() as conn:
        conn.execute("UPDATE storyboards SET render_error=NULL WHERE id=? AND render_error IS NOT NULL", (project_id,))


def advance(project, scenes, settings):
    waiting = next((scene for scene in scenes if scene["status"] == "Waiting"), None)
    if waiting:
        submit_waiting(waiting, settings)
        return
    for scene in scenes:
        if scene["status"] in studio.ACTIVE:
            studio.poll(scene, settings, queued_at=scene["claimed"])
    if any(scene["status"] != "Ready" for scene in scenes):
        return
    project_id = project["id"]
    if export_path(project_id).is_file() or project["render_error"]:
        return
    now = time.time()
    with studio.database() as conn:
        claimed = conn.execute("""UPDATE storyboards SET render_started=?
            WHERE id=? AND render_error IS NULL AND (render_started IS NULL OR render_started < ?)""",
            (now, project_id, now - 180)).rowcount
    if not claimed:
        return
    error = None
    try:
        stitch(project, scenes)
    except studio.StudioError as exc:
        error = str(exc)
    finally:
        with studio.database() as conn:
            conn.execute("UPDATE storyboards SET render_started=NULL, render_error=? WHERE id=? AND render_started=?",
                         (error, project_id, now))
