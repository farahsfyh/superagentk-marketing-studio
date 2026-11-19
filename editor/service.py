"""Drive-backed operations for a private operator workspace."""
import hashlib
import threading
from pathlib import PurePath

from .drive import DriveStore, file_id, now
from .plan import require, validate

WRITE_LOCK = threading.RLock()

MIMES = {
    "footage": {".mp4": "video/mp4", ".mov": "video/quicktime"},
    "logo": {".png": "image/png"},
    "font": {".ttf": "font/ttf", ".otf": "font/otf"},
}


class EditorSession:
    def __init__(self, config, store=None):
        self.workspace = config["EDITOR_WORKSPACE_ID"]
        self.store = store or DriveStore(config)

    def check(self):
        return self.store.check()

    def assets(self):
        return self.store.list_records("asset")

    def register(self, reference, kind, rights):
        with WRITE_LOCK:
            require(rights is True and kind in MIMES, "Confirm the file's usage rights and category.")
            metadata = self.store.metadata(file_id(reference))
            require(metadata["mimeType"] != "application/vnd.google-apps.shortcut", "Use the original file, not a Drive shortcut.")
            extension = PurePath(metadata["name"]).suffix.lower()
            require(extension in MIMES[kind], "This file type is unsupported for the selected category.")
            existing = next((a for a in self.assets() if a["file_id"] == metadata["id"]), None)
            if existing:
                require(existing["kind"] == kind, "This file is already registered in another category.")
                return existing["object_key"]
            record = self.store.create_json("asset", {
                "file_id": metadata["id"], "original_name": metadata["name"],
                "object_key": self.store.media_key(metadata), "kind": kind,
                "byte_size": int(metadata.get("size", "0")), "confirmed_rights": True, "created_at": now(),
            })
            return record["object_key"]

    def upload(self, source, name, kind, rights):
        require(rights is True and kind in MIMES, "Confirm your right to use this file.")
        mime = MIMES[kind].get(PurePath(name).suffix.lower())
        require(mime, "This file type is unsupported.")
        source.seek(0, 2)
        size = source.tell()
        source.seek(0)
        require(0 < size <= 100 * 1024**2, "Upload a non-empty file up to 100 MB.")
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
        source.seek(0)
        metadata = self.store.upload(source, name[:200], mime, "media")
        self.store.verify(metadata["id"], digest.hexdigest())
        return self.register(metadata["id"], kind, rights)

    def plans(self):
        return sorted(self.store.list_records("plan"), key=lambda p: p["created_at"], reverse=True)

    def preview(self, plan):
        with WRITE_LOCK:
            require(plan.get("workspace_id") == self.workspace, "This plan belongs to another workspace.")
            validate(plan)
            registered = {asset["object_key"] for asset in self.assets()}
            keys = [clip["original_key"] for clip in plan["clips"].values()]
            keys += [plan["style"]["font_key"], plan["style"]["logo_key"]]
            require(all(key in registered for key in keys), "Select registered footage and style files.")
            previous = next((p for p in self.plans() if p["project_id"] == plan["project_id"] and p["version"] == plan["version"]), None)
            if previous:
                require(previous["plan"] == plan, "Save these changes as a new version.")
                record = previous
            else:
                record = self.store.create_json("plan", {"project_id": plan["project_id"], "version": plan["version"],
                                                        "plan": plan, "created_at": now()})
            existing = self.jobs(record["id"])
            jobs = []
            for format_name in plan["formats"]:
                job = next((j for j in existing if j["format"] == format_name), None)
                if not job:
                    job = self.new_job(record, format_name)
                jobs.append(job["id"])
            return jobs

    def new_job(self, plan, format_name, retry_of=None):
        return self.store.create_json("job", {
            "plan_id": plan["id"], "project_id": plan["project_id"], "version": plan["version"],
            "format": format_name, "quality": "preview", "state": "queued", "attempts": 0,
            "error": None, "output": None, "retry_of": retry_of, "created_at": now(),
        })

    def jobs(self, plan_id=None):
        values = self.store.list_records("job")
        return sorted((j for j in values if plan_id is None or j["plan_id"] == plan_id),
                      key=lambda j: j["created_at"], reverse=True)

    def retry(self, job_id):
        with WRITE_LOCK:
            job = self.store.read_json(job_id, "job")
            require(job["state"] == "failed", "Only a failed preview can be retried.")
            require(not any(j.get("retry_of") == job_id for j in self.jobs()), "This preview has already been retried.")
            plan = self.store.read_json(job["plan_id"], "plan")
            return self.new_job(plan, job["format"], job_id)

    def preview_link(self, job_id):
        job = self.store.read_json(job_id, "job")
        require(job["state"] == "ready" and job["output"], "This preview is not ready.")
        result = self.store.metadata(job["output"]["file_id"])
        require(result.get("webViewLink"), "Drive has not prepared the video link yet.")
        return result["webViewLink"]

    def usage(self):
        return sorted(self.store.list_records("usage"), key=lambda row: row["created_at"], reverse=True)
