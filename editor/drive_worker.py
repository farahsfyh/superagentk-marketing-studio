"""Single render worker for the temporary Drive-backed editor."""
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .drive import DriveStore, now
from .plan import PlanError, require, validate
from .render import checksum, render


@contextmanager
def single_worker(folder):
    name = hashlib.sha256(folder.encode()).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / f"video-editor-worker-{name}.lock"
    with path.open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise PlanError("A worker for this Drive folder is already running on this computer.") from None
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise PlanError("A worker for this Drive folder is already running on this computer.") from None
        yield


def process(store, job):
    require(job["state"] in ("queued", "running") and job["quality"] == "preview", "Only pending previews may be processed.")
    require(job["attempts"] < 3, "This job exhausted its automatic worker attempts.")
    # Only this one worker writes job state. Streamlit creates new jobs to retry.
    previous = [row for row in store.list_records("usage") if row["job_id"] == job["id"] and row["status"] == "running"]
    for usage in previous:
        usage.update(status="interrupted")
        store.update_json(usage, "usage")
    job.update(state="running", attempts=job["attempts"] + 1, error=None)
    store.update_job(job)
    usage = store.create_json("usage", {
        "job_id": job["id"], "attempt": job["attempts"], "status": "running", "created_at": now(),
        "render_minutes": 0, "estimated_usd": 0, "user": "workspace-operator",
    })
    start = time.monotonic()
    try:
        record = store.read_json(job["plan_id"], "plan")
        plan = record["plan"]
        validate(plan)
        require(plan["workspace_id"] == store.workspace and job["format"] in plan["formats"], "Invalid workspace or render format.")
        registered = {row["object_key"] for row in store.list_records("asset")}
        keys = [clip["original_key"] for clip in plan["clips"].values()]
        keys += [plan["style"]["font_key"], plan["style"]["logo_key"]]
        require(all(key in registered for key in keys), "A source file is no longer registered to this workspace.")
        with tempfile.TemporaryDirectory(prefix="video-editor-") as temporary:
            root, files = Path(temporary), {}
            for index, key in enumerate(dict.fromkeys(keys)):
                destination = root / f"input-{index}{Path(key).suffix}"
                store.download(store.key_id(key), destination)
                files[key] = destination
            result = render(plan, files, root / "render", job["format"], "preview")
            with result["path"].open("rb") as data:
                video = store.upload(data, f"{job['project_id']}-v{job['version']}-{job['format'].replace(':','x')}-{job['id']}-a{job['attempts']}.mp4",
                                     "video/mp4", "output")
            store.verify(video["id"], result["checksum"])
            with result["thumbnail"].open("rb") as data:
                thumbnail = store.upload(data, f"thumbnail-{job['id']}-a{job['attempts']}.jpg", "image/jpeg", "output")
            store.verify(thumbnail["id"], checksum(result["thumbnail"]))
            job["output"] = {key: value for key, value in result.items() if key not in ("path", "thumbnail")}
            job["output"].update(file_id=video["id"], thumbnail_id=thumbnail["id"])
            job["state"] = "ready"
    except PlanError as error:
        job.update(state="failed", error=str(error))
    except Exception:
        job.update(state="failed", error="The worker could not finish this preview. Check the worker configuration and Drive authorization.")
    usage.update(status=job["state"], render_minutes=round((time.monotonic() - start) / 60, 6))
    store.update_json(usage, "usage")
    store.update_job(job)


def main():
    config = {
        "GOOGLE_DRIVE_FOLDER_ID": os.environ["GOOGLE_DRIVE_FOLDER_ID"],
        "EDITOR_WORKSPACE_ID": os.environ["EDITOR_WORKSPACE_ID"],
        "GOOGLE_DRIVE_OAUTH": json.loads(Path(os.environ["GOOGLE_DRIVE_OAUTH_FILE"]).read_text(encoding="utf-8-sig")),
    }
    store = DriveStore(config)
    store.check()
    with single_worker(store.folder):
        print("Video Editor worker is watching the configured Drive folder.", flush=True)
        while True:
            try:
                jobs = sorted(store.list_records("job"), key=lambda job: job["created_at"])
                job = next((job for job in jobs if job["state"] in ("queued", "running")), None)
                if job:
                    if job["attempts"] >= 3:
                        job.update(state="failed", error="The worker stopped repeatedly. Check its setup before retrying.")
                        store.update_job(job)
                    else:
                        process(store, job)
                else:
                    time.sleep(8)
            except Exception:
                print("The worker could not update Drive. Retrying shortly.", flush=True)
                time.sleep(15)


if __name__ == "__main__":
    main()
