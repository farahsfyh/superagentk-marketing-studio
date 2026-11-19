"""Google Drive persistence for the initial single-workspace, single-worker editor."""
import hashlib
import io
import json
import re
from datetime import datetime, timezone

from .plan import PlanError, require

ID = re.compile(r"[A-Za-z0-9_-]{10,120}")
FIELDS = "id,name,mimeType,parents,size,webViewLink,appProperties,sha256Checksum,createdTime"


def file_id(value):
    match = re.search(r"/(?:d|folders)/([A-Za-z0-9_-]+)", value)
    candidate = match.group(1) if match else value
    require(ID.fullmatch(candidate), "Enter a valid Drive file or folder link.")
    return candidate


def now():
    return datetime.now(timezone.utc).isoformat()


class DriveStore:
    def __init__(self, config, api=None):
        self.folder = file_id(str(config["GOOGLE_DRIVE_FOLDER_ID"]))
        self.workspace = str(config["EDITOR_WORKSPACE_ID"])
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.workspace), "Invalid workspace ID.")
        if api is None:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            info = dict(config["GOOGLE_DRIVE_OAUTH"])
            credentials = Credentials.from_authorized_user_info(info)
            self.api = build("drive", "v3", credentials=credentials, cache_discovery=False)
        else:
            self.api = api

    def execute(self, request):
        try:
            return request.execute(num_retries=2)
        except Exception:
            raise PlanError("Google Drive could not complete the request. Check folder access and authorization.") from None

    def check(self):
        data = self.execute(self.api.files().get(fileId=self.folder, fields="id,name,mimeType", supportsAllDrives=True))
        require(data["mimeType"] == "application/vnd.google-apps.folder", "Choose a Google Drive folder.")
        return data["name"]

    def metadata(self, reference):
        data = self.execute(self.api.files().get(fileId=file_id(reference), fields=FIELDS, supportsAllDrives=True))
        require(self.folder in data.get("parents", []), "Use a file directly inside the configured editor folder.")
        return data

    def list_records(self, kind):
        query = (f"'{self.folder}' in parents and trashed=false and "
                 f"appProperties has {{ key='editor_workspace' and value='{self.workspace}' }} and "
                 f"appProperties has {{ key='editor_kind' and value='{kind}' }}")
        result, page = [], None
        while True:
            response = self.execute(self.api.files().list(q=query, pageToken=page, pageSize=100,
                fields="nextPageToken,files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True))
            result.extend(response.get("files", []))
            page = response.get("nextPageToken")
            if not page:
                break
        return [self.read_json(item["id"], kind) for item in result]

    def read_json(self, reference, kind):
        metadata = self.metadata(reference)
        properties = metadata.get("appProperties", {})
        require(properties.get("editor_workspace") == self.workspace and properties.get("editor_kind") == kind,
                "This record does not belong to this editor workspace.")
        require(int(metadata.get("size", "0")) <= 2 * 1024 * 1024, "The editor record is too large.")
        try:
            data = json.loads(self.execute(self.api.files().get_media(fileId=metadata["id"], supportsAllDrives=True)))
        except (ValueError, TypeError):
            raise PlanError("The editor record could not be read.") from None
        require(isinstance(data, dict) and data.get("workspace_id") == self.workspace, "Invalid workspace record.")
        return {**data, "id": metadata["id"]}

    def upload(self, source, name, mime, kind):
        from googleapiclient.http import MediaIoBaseUpload
        source.seek(0)
        allocated = self.execute(self.api.files().generateIds(count=1, space="drive"))["ids"][0]
        request = self.api.files().create(body={
            "id": allocated, "name": name, "parents": [self.folder],
            "appProperties": {"editor_workspace": self.workspace, "editor_kind": kind},
        }, media_body=MediaIoBaseUpload(source, mimetype=mime, chunksize=8 * 1024 * 1024, resumable=True),
            fields=FIELDS, supportsAllDrives=True)
        result = None
        try:
            while result is None:
                _, result = request.next_chunk(num_retries=2)
        except Exception:
            raise PlanError("Drive upload was interrupted. Check the editor folder before uploading again.") from None
        return result

    def create_json(self, kind, record):
        value = {**record, "workspace_id": self.workspace}
        data = json.dumps(value, allow_nan=False).encode()
        result = self.upload(io.BytesIO(data), f"{kind}-{record.get('project_id', 'editor')}.json", "application/json", kind)
        return {**value, "id": result["id"]}

    def update_job(self, record):
        return self.update_json(record, "job")

    def update_json(self, record, kind):
        from googleapiclient.http import MediaIoBaseUpload
        self.read_json(record["id"], kind)
        value = {**record, "workspace_id": self.workspace}
        body = io.BytesIO(json.dumps(value, allow_nan=False).encode())
        self.execute(self.api.files().update(fileId=record["id"],
                     media_body=MediaIoBaseUpload(body, mimetype="application/json"),
                     fields="id", supportsAllDrives=True))

    def download(self, reference, destination):
        from googleapiclient.http import MediaIoBaseDownload
        metadata = self.metadata(reference)
        require(int(metadata.get("size", "0")) <= 2 * 1024**3, "The worker source exceeds 2 GB.")
        with open(destination, "wb") as target:
            request = MediaIoBaseDownload(target, self.api.files().get_media(fileId=metadata["id"], supportsAllDrives=True),
                                           chunksize=8 * 1024 * 1024)
            done = False
            try:
                while not done:
                    _, done = request.next_chunk(num_retries=2)
            except Exception:
                raise PlanError("Drive could not download the source file.") from None

    def verify(self, reference, expected):
        metadata = self.metadata(reference)
        actual = metadata.get("sha256Checksum")
        if actual is None:
            from googleapiclient.http import MediaIoBaseDownload
            digest = hashlib.sha256()

            class HashSink:
                def write(self, data):
                    digest.update(data)
                    return len(data)

            request = MediaIoBaseDownload(HashSink(), self.api.files().get_media(fileId=metadata["id"], supportsAllDrives=True),
                                         chunksize=8 * 1024 * 1024)
            done = False
            try:
                while not done:
                    _, done = request.next_chunk(num_retries=2)
            except Exception:
                raise PlanError("Drive could not verify the saved file.") from None
            actual = digest.hexdigest()
        require(actual == expected, "Drive's saved file checksum did not match.")

    def media_key(self, metadata, kind="assets"):
        extension = metadata["name"].rsplit(".", 1)[-1].lower()
        return f"{self.workspace}/{kind}/{metadata['id']}.{extension}"

    def key_id(self, key):
        prefix = f"{self.workspace}/assets/"
        require(isinstance(key, str) and key.startswith(prefix), "The source belongs to another workspace.")
        return file_id(key[len(prefix):].rsplit(".", 1)[0])
