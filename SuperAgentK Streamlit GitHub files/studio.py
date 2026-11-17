"""Server-side ModelArk calls and durable job records for one private studio."""
import csv
import io
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("STUDIO_DATA_DIR", ROOT / "studio-data"))
ASSETS = ROOT / "assets"
REGIONS = {
    "ap-southeast-1": "ark.ap-southeast.bytepluses.com",
    "eu-west-1": "ark.eu-west.bytepluses.com",
}
RATIOS = ("16:9", "9:16", "1:1", "4:5")
LIVE_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
ACTIVE = ("Queued", "Generating", "Finishing")
ERROR_HINTS = {
    "ModelNotOpen": "Activate this model in BytePlus ModelArk.",
    "InvalidEndpointOrModel.ModelIDAccessDisabled": "Use an authorized inference endpoint ID.",
    "InvalidEndpointOrModel": "Check the model or endpoint ID and its region.",
    "AccountOverdueError": "Check your BytePlus account balance.",
    "AuthenticationError": "Check the ModelArk API key and its region.",
    "AccessDenied": "This key may lack access to the selected endpoint.",
    "InvalidParameter": "Check whether the endpoint supports five-second, 720p text-to-video.",
}


class StudioError(Exception):
    def __init__(self, message, rejected=False):
        super().__init__(message)
        self.rejected = rejected


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP = build_opener(NoRedirect())


def provider_error(error, request_id=None):
    code = error.get("code")
    request_id = error.get("request_id") or request_id
    code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z][\w.-]{0,79}", code) else None
    request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9-]{12,100}", request_id) else None
    info = [ERROR_HINTS.get(code, "BytePlus could not complete the request.")]
    if code:
        info.append(f"BytePlus code: {code}.")
    if request_id:
        info.append(f"Request ID: {request_id}.")
    return " ".join(info)


def ark(settings, suffix="", payload=None):
    host = REGIONS.get(settings["region"])
    if not host:
        raise StudioError("Choose a supported BytePlus region.")
    url = f"https://{host}/api/v3/contents/generations/tasks{suffix}"
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, headers={
        "Authorization": "Bearer " + settings["key"],
        "Content-Type": "application/json",
    })
    try:
        with HTTP.open(request, timeout=25) as response:
            return json.loads(response.read(1_000_000))
    except HTTPError as exc:
        try:
            error = json.loads(exc.read(65536)).get("error", {})
            if not isinstance(error, dict):
                error = {}
        except (ValueError, AttributeError):
            error = {}
        finally:
            exc.close()
        if not error.get("code"):
            error["code"] = exc.headers.get("x-error-code")
        if 300 <= exc.code < 400:
            raise StudioError("BytePlus redirected the API request. Check the configured region.", True) from None
        raise StudioError(provider_error(error, exc.headers.get("x-request-id")), 400 <= exc.code < 500) from None
    except (URLError, OSError, ValueError):
        raise StudioError("The server could not complete its connection to BytePlus.") from None


@contextmanager
def database():
    DATA.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DATA / "studio.sqlite3", timeout=20)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL, model TEXT NOT NULL,
            provider TEXT NOT NULL, region TEXT NOT NULL, ratio TEXT NOT NULL, status TEXT NOT NULL,
            cents INTEGER NOT NULL, created REAL NOT NULL, provider_id TEXT, error TEXT)""")
        conn.execute("CREATE TABLE IF NOT EXISTS presets (name TEXT PRIMARY KEY, notes TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS storyboards (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, provider TEXT NOT NULL, ratio TEXT NOT NULL,
            created REAL NOT NULL, render_started REAL, render_error TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS storyboard_scenes (
            storyboard_id TEXT NOT NULL, position INTEGER NOT NULL, label TEXT NOT NULL,
            duration INTEGER NOT NULL, job_id TEXT NOT NULL, PRIMARY KEY (storyboard_id, position))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS storyboard_attempts (
            job_id TEXT PRIMARY KEY, claimed REAL)""")
        yield conn


def all_jobs():
    with database() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM jobs ORDER BY created DESC")]


def single_jobs():
    with database() as conn:
        return [dict(row) for row in conn.execute("""SELECT * FROM jobs WHERE NOT EXISTS (
            SELECT 1 FROM storyboard_attempts WHERE job_id=jobs.id) ORDER BY created DESC""")]


def latest_job_id():
    with database() as conn:
        row = conn.execute("""SELECT id FROM jobs WHERE NOT EXISTS (
            SELECT 1 FROM storyboard_attempts WHERE job_id=jobs.id) ORDER BY created DESC LIMIT 1""").fetchone()
        return row["id"] if row else None


def get_job(job_id):
    with database() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def presets():
    with database() as conn:
        return dict(conn.execute("SELECT name, notes FROM presets ORDER BY name").fetchall())


def save_preset(name, notes):
    with database() as conn:
        conn.execute("INSERT OR REPLACE INTO presets VALUES (?, ?)", (name, notes))


def monthly_spend(conn, provider):
    now = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return conn.execute(
        "SELECT COALESCE(SUM(cents), 0) FROM jobs WHERE created >= ? AND provider = ?",
        (now.timestamp(), provider),
    ).fetchone()[0]


def update(job_id, status, provider_id=None, error=None, release=False):
    with database() as conn:
        conn.execute("""UPDATE jobs SET status=?, provider_id=COALESCE(?, provider_id),
            error=?, cents=CASE WHEN ? THEN 0 ELSE cents END WHERE id=?""",
            (status, provider_id, error, release, job_id),
        )


def start_job(prompt, ratio, provider, settings, cap, rate, draft=False):
    live = provider == "Seedance"
    allowed = LIVE_RATIOS if live else RATIOS
    if not prompt.strip() or len(prompt) > 6000 or ratio not in allowed:
        raise StudioError("Enter a prompt up to 6,000 characters and choose a supported aspect ratio.")
    if live and not draft and not settings["key"]:
        raise StudioError("Configure BYTEPLUS_API_KEY in Streamlit Secrets before generating.")
    job_id = uuid.uuid4().hex
    cost = 0 if draft else rate * 5
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if cap is not None and monthly_spend(conn, provider) + cost > cap and not draft:
            raise StudioError("This request exceeds the monthly estimated spend cap.")
        conn.execute("""INSERT INTO jobs (id, title, prompt, model, provider, region, ratio, status, cents, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, prompt[:74], prompt, settings["model"] if live else "Mock",
             provider, settings["region"] if live else "", ratio,
             "Draft" if draft else "Queued" if live else "Ready", cost, time.time()),
        )
    if not live or draft:
        return job_id
    submit_seedance(job_id, prompt, ratio, settings, 5)
    return job_id


def submit_seedance(job_id, prompt, ratio, settings, duration):
    parameters = {
        "model": settings["model"], "content": [{"type": "text", "text": prompt}],
        "ratio": ratio, "duration": duration, "resolution": "720p",
        "watermark": True, "execution_expires_after": 3600,
    }
    if not settings["model"].startswith("seedance-1-0-"):
        parameters["generate_audio"] = False
    try:
        result = ark(settings, payload=parameters)
        provider_id = result.get("id") if isinstance(result, dict) else None
        if not isinstance(provider_id, str) or not re.fullmatch(r"[\w-]{1,120}", provider_id):
            raise StudioError("BytePlus did not return a task ID.")
        update(job_id, "Generating", provider_id=provider_id)
    except StudioError as exc:
        detail = str(exc) if exc.rejected else f"{exc} Check ModelArk before trying again. The estimate remains reserved."
        update(job_id, "Failed", error=detail, release=exc.rejected)


def media_path(job):
    if job["provider"] == "Mock":
        return ASSETS / f"sample-{RATIOS.index(job['ratio'])}.mp4"
    return DATA / f"{job['id']}.mp4"


def save_video(job, url):
    parsed = urlsplit(url or "")
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port is not None
            or not host.endswith((".volces.com", ".bytepluses.com"))):
        raise StudioError("BytePlus returned an unexpected video URL.")
    path = media_path(job)
    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        with HTTP.open(Request(url), timeout=35) as response, temp.open("wb") as out:
            total = 0
            for chunk in iter(lambda: response.read(256 * 1024), b""):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise StudioError("The video exceeds the 100 MB studio limit.")
                out.write(chunk)
            if not total:
                raise StudioError("BytePlus returned an empty video.")
        temp.replace(path)
    except HTTPError as exc:
        exc.close()
        if exc.code in (403, 404):
            raise StudioError("The provider download has expired or is unavailable.", True) from None
        raise StudioError("The provider download failed. Check again shortly.") from None
    except (URLError, OSError):
        raise StudioError("The studio could not save the video. Check again shortly.") from None
    finally:
        temp.unlink(missing_ok=True)


def poll(job, settings, queued_at=None):
    if not job["provider_id"]:
        if time.time() - (queued_at or job["created"]) > 120:
            update(job["id"], "Failed", error="Submission was interrupted. Check ModelArk before retrying.")
        return
    if not settings["key"]:
        raise StudioError("Configure BYTEPLUS_API_KEY to check this job.")
    response = ark({**settings, "region": job["region"]}, "/" + quote(job["provider_id"], safe=""))
    status = response.get("status")
    if status == "succeeded":
        update(job["id"], "Finishing")
        try:
            if not media_path(job).is_file():
                save_video(job, (response.get("content") or {}).get("video_url"))
        except StudioError as exc:
            if exc.rejected:
                update(job["id"], "Failed", error=str(exc))
                return
            raise
        update(job["id"], "Ready")
    elif status in {"failed", "expired", "cancelled", "canceled"}:
        error = response.get("error")
        update(job["id"], "Failed", error=provider_error(error) if isinstance(error, dict) else f"BytePlus ended this job with status {status}.")


def usage_csv(jobs):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(("job_id", "provider", "model", "status", "estimate_usd"))
    for job in jobs:
        writer.writerow((job["id"], job["provider"], job["model"], job["status"], f"{job['cents'] / 100:.2f}"))
    return output.getvalue()
