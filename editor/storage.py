"""Private Supabase object storage; credentials stay in the worker environment."""
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .plan import PlanError, object_key, require


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Storage:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/")
        parsed = urlsplit(self.url)
        require(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
                and parsed.path in ("", "/") and not parsed.query and not parsed.fragment, "Use an HTTPS Supabase origin.")
        self.secret = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        self.bucket = os.environ.get("EDITOR_BUCKET", "video-editor")
        self.client = build_opener(NoRedirect())

    def request(self, route, data=None, method="GET", content_type=None):
        headers = {"apikey": self.secret, "Authorization": "Bearer " + self.secret}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            return self.client.open(Request(self.url + "/storage/v1/" + route, data=data,
                                            headers=headers, method=method), timeout=120)
        except (HTTPError, URLError, OSError) as error:
            if isinstance(error, HTTPError):
                error.close()
            raise PlanError("Private storage could not complete this operation.") from None

    def key_route(self, key):
        require(object_key(key), "Invalid object storage key.")
        return quote(self.bucket, safe="") + "/" + quote(key, safe="/")

    def download(self, key, path):
        path = Path(path)
        maximum = int(os.environ.get("EDITOR_MAX_SOURCE_BYTES", str(2 * 1024**3)))
        size = 0
        with self.request("object/authenticated/" + self.key_route(key)) as source, path.open("wb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                require(size <= maximum, "The source file exceeds the worker's configured input limit.")
                output.write(chunk)

    def upload(self, key, path, mime):
        path = Path(path)
        # urllib can stream a file when Content-Length is supplied.
        headers = {"apikey": self.secret, "Authorization": "Bearer " + self.secret,
                   "Content-Type": mime, "Content-Length": str(path.stat().st_size)}
        try:
            with path.open("rb") as data:
                request = Request(self.url + "/storage/v1/object/" + self.key_route(key),
                                  data=data, headers=headers, method="POST")
                with self.client.open(request, timeout=180) as response:
                    response.read(65536)
        except (HTTPError, URLError, OSError) as error:
            if isinstance(error, HTTPError):
                error.close()
            raise PlanError("The output could not be saved to private storage.") from None

    def verify(self, key, expected):
        digest = hashlib.sha256()
        with self.request("object/authenticated/" + self.key_route(key)) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        require(digest.hexdigest() == expected, "The saved output checksum did not match.")

    def sign(self, key):
        with self.request("object/sign/" + self.key_route(key), json.dumps({"expiresIn": 300}).encode(),
                          "POST", "application/json") as response:
            result = json.load(response)
        return self.url + "/storage/v1" + result["signedURL"]
