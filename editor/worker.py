"""Durable PostgreSQL render queue with leases and isolated attempt outputs."""
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from .plan import PlanError, require, validate
from .render import checksum, render
from .storage import Storage


def connect():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(os.environ["EDITOR_DATABASE_URL"], row_factory=dict_row, connect_timeout=15)


def enqueue(plan, quality, approved_by=None):
    validate(plan)
    require(quality in ("preview", "master"), "Choose preview or master.")
    require(quality != "master" or approved_by, "Master rendering requires an approver.")
    if approved_by:
        approved_by = str(uuid.UUID(approved_by))
    plan_id = uuid.uuid4()
    with connect() as conn:
        row = conn.execute("""INSERT INTO video_editor.edit_plans
            (id, workspace_id, project_id, version, plan) VALUES (%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (workspace_id, project_id, version) DO NOTHING RETURNING id""",
            (plan_id, plan["workspace_id"], plan["project_id"], plan["version"], json.dumps(plan))).fetchone()
        if row:
            plan_id = row["id"]
        else:
            previous = conn.execute("""SELECT id, plan FROM video_editor.edit_plans
                WHERE workspace_id=%s AND project_id=%s AND version=%s""",
                (plan["workspace_id"], plan["project_id"], plan["version"])).fetchone()
            require(previous["plan"] == plan, "This version already has a different plan. Save a new version.")
            plan_id = previous["id"]
        if approved_by:
            member = conn.execute("""SELECT role FROM video_editor.members
                WHERE workspace_id=%s AND user_id=%s""", (plan["workspace_id"], approved_by)).fetchone()
            require(member and member["role"] in ("owner", "editor"), "The approver needs editor access to this workspace.")
        jobs = []
        for format_name in plan["formats"]:
            job_id = uuid.uuid4()
            conn.execute("""INSERT INTO video_editor.render_jobs
                (id,plan_id,workspace_id,format,quality,approved_by,approved_at)
                VALUES (%s,%s,%s,%s,%s,%s,CASE WHEN %s::uuid IS NULL THEN NULL ELSE now() END)""",
                (job_id, plan_id, plan["workspace_id"], format_name, quality, approved_by, approved_by))
            jobs.append(str(job_id))
        return jobs


def claim():
    token = uuid.uuid4()
    with connect() as conn:
        conn.execute("""UPDATE video_editor.render_jobs SET state='failed',
            error='The worker stopped repeatedly. Queue a fresh attempt after checking its configuration.',
            lease_token=NULL, lease_until=NULL
            WHERE state='running' AND lease_until < now() AND attempts >= 3""")
        job = conn.execute("""WITH candidate AS (
            SELECT id FROM video_editor.render_jobs WHERE
            (state='queued' OR (state='running' AND lease_until < now()))
            AND attempts < 3 ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
        ) UPDATE video_editor.render_jobs j SET state='running', lease_token=%s,
          lease_until=now()+interval '120 seconds', attempts=attempts+1, error=NULL
          FROM candidate WHERE j.id=candidate.id RETURNING j.*""", (token,)).fetchone()
        if job:
            conn.execute("UPDATE video_editor.usage_ledger SET status='failed' WHERE job_id=%s AND status='running'", (job["id"],))
            conn.execute("""INSERT INTO video_editor.usage_ledger (job_id,attempt,workspace_id,render_minutes,estimated_usd,status)
                VALUES (%s,%s,%s,0,0,'running')""", (job["id"], job["attempts"], job["workspace_id"]))
        return job


@contextmanager
def heartbeat(job):
    done = threading.Event()
    lost = threading.Event()

    def renew():
        while not done.wait(25):
            try:
                with connect() as conn:
                    updated = conn.execute("""UPDATE video_editor.render_jobs SET lease_until=now()+interval '120 seconds'
                        WHERE id=%s AND lease_token=%s AND state='running' AND lease_until > now()""",
                        (job["id"], job["lease_token"])).rowcount
                    if updated != 1:
                        lost.set()
                        return
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        done.set()
        thread.join(timeout=20)


def finish(job, output, error, minutes):
    state = "failed" if error else "ready"
    rate = Decimal(os.environ.get("EDITOR_RENDER_USD_PER_MINUTE", "0"))
    require(rate.is_finite() and rate >= 0, "Invalid render cost rate.")
    with connect() as conn:
        updated = conn.execute("""UPDATE video_editor.render_jobs SET state=%s, output=%s::jsonb, error=%s,
            lease_token=NULL, lease_until=NULL WHERE id=%s AND lease_token=%s
            AND state='running' AND lease_until > now() RETURNING id""",
            (state, json.dumps(output) if output else None, error, job["id"], job["lease_token"])).fetchone()
        if updated:
            conn.execute("""INSERT INTO video_editor.usage_ledger
                (job_id,attempt,workspace_id,render_minutes,estimated_usd,status)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (job_id,attempt) DO UPDATE SET render_minutes=EXCLUDED.render_minutes,
                estimated_usd=EXCLUDED.estimated_usd,status=EXCLUDED.status""",
                (job["id"], job["attempts"], job["workspace_id"], minutes, minutes * rate, state))
        return bool(updated)


def process(job):
    start = time.monotonic()
    output, error = None, None
    with heartbeat(job) as lost:
        try:
            with connect() as conn:
                row = conn.execute("SELECT plan FROM video_editor.edit_plans WHERE id=%s AND workspace_id=%s",
                                   (job["plan_id"], job["workspace_id"])).fetchone()
            require(row, "The edit plan is unavailable.")
            plan = row["plan"]
            validate(plan)
            require(plan["workspace_id"] == job["workspace_id"], "Workspace mismatch.")
            require(job["quality"] != "master" or job["approved_by"], "The master has no approval.")
            store = Storage()
            with TemporaryDirectory(prefix="video-editor-") as scratch:
                root = Path(scratch)
                keys = [clip["original_key"] for clip in plan["clips"].values()]
                keys.extend((plan["style"]["font_key"], plan["style"]["logo_key"]))
                files = {}
                for index, key in enumerate(dict.fromkeys(keys)):
                    require(key.startswith(plan["workspace_id"] + "/"), "Source objects must belong to this workspace.")
                    suffix = Path(key).suffix.lower()
                    require(suffix in (".mp4", ".mov", ".png", ".jpg", ".jpeg", ".ttf", ".otf"), "Unsupported source file.")
                    files[key] = root / f"input-{index}{suffix}"
                    store.download(key, files[key])
                result = render(plan, files, root / "render", job["format"], job["quality"])
                require(not lost.is_set(), "This render attempt lost its lease.")
                prefix = f"{plan['workspace_id']}/renders/{job['id']}/{job['lease_token']}"
                video_key, thumbnail_key = prefix + "/output.mp4", prefix + "/thumbnail.jpg"
                store.upload(video_key, result["path"], "video/mp4")
                store.verify(video_key, result["checksum"])
                store.upload(thumbnail_key, result["thumbnail"], "image/jpeg")
                store.verify(thumbnail_key, checksum(result["thumbnail"]))
                output = {key: value for key, value in result.items() if key not in ("path", "thumbnail")}
                output.update(video_key=video_key, thumbnail_key=thumbnail_key, approval="technical-review-required")
        except PlanError as exc:
            error = str(exc)
        except Exception:
            # Provider responses, connection strings, and secrets stay out of logs and job errors.
            error = "The worker could not complete this attempt. Check worker configuration and storage access."
        minutes = Decimal(str((time.monotonic() - start) / 60)).quantize(Decimal(".000001"))
        if not lost.is_set():
            finish(job, output, error, minutes)


def main():
    while True:
        try:
            job = claim()
            if job:
                process(job)
            else:
                time.sleep(3)
        except Exception:
            print("Worker cannot reach the render queue. Retrying.", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
