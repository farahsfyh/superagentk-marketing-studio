import copy
import hashlib
import io
import unittest

from editor.drive import file_id
from editor.forms import build_plan
from editor.plan import PlanError
from editor.service import EditorSession
from test_engine import fixture


class MemoryDrive:
    def __init__(self):
        self.workspace = "test"
        self.records = {}
        self.files = {}
        self.serial = 1000000000

    def check(self):
        return "Test private folder"

    def create_json(self, kind, record):
        self.serial += 1
        result = {**copy.deepcopy(record), "workspace_id": self.workspace, "id": str(self.serial)}
        self.records[result["id"]] = (kind, result)
        return copy.deepcopy(result)

    def list_records(self, kind):
        return [copy.deepcopy(value) for record_kind, value in self.records.values() if record_kind == kind]

    def read_json(self, reference, kind):
        record_kind, value = self.records[reference]
        if kind != record_kind or value["workspace_id"] != self.workspace:
            raise PlanError("Wrong workspace.")
        return copy.deepcopy(value)

    def metadata(self, reference):
        if reference not in self.files:
            raise PlanError("File unavailable in this folder.")
        return {**self.files[reference][0], "id": reference, "size": str(len(self.files[reference][1])),
                "webViewLink": "https://drive.google.com/file/d/" + reference + "/view"}

    def media_key(self, metadata):
        return f"test/assets/{metadata['id']}." + metadata["name"].rsplit(".", 1)[-1]

    def upload(self, source, name, mime, kind):
        self.serial += 1
        reference = str(self.serial)
        data = source.read()
        self.files[reference] = ({"name": name, "mimeType": mime}, data)
        return self.metadata(reference)

    def verify(self, reference, expected):
        if hashlib.sha256(self.files[reference][1]).hexdigest() != expected:
            raise PlanError("Checksum mismatch.")

    def update_json(self, record, kind):
        self.read_json(record["id"], kind)
        self.records[record["id"]] = (kind, copy.deepcopy(record))

    def update_job(self, record):
        self.update_json(record, "job")

    def key_id(self, key):
        return key.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    def download(self, reference, destination):
        destination.write_bytes(self.files[reference][1])


class DriveSessionTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryDrive()
        self.session = EditorSession({"EDITOR_WORKSPACE_ID": "test"}, self.store)
        self.keys = {}
        for kind, filename in (("footage", "source.mp4"), ("logo", "logo.png"), ("font", "font.ttf")):
            self.keys[kind] = self.session.upload(io.BytesIO(b"fixture bytes"), filename, kind, True)

    def plan(self):
        plan = fixture()
        plan["clips"]["clip1"]["original_key"] = self.keys["footage"]
        plan["style"]["logo_key"] = self.keys["logo"]
        plan["style"]["font_key"] = self.keys["font"]
        return plan

    def test_queue_is_persistent_and_repeated_submit_reuses_jobs(self):
        first = self.session.preview(self.plan())
        reopened = EditorSession({"EDITOR_WORKSPACE_ID": "test"}, self.store)
        self.assertEqual(reopened.preview(self.plan()), first)
        self.assertEqual(len(reopened.plans()), 1)
        self.assertEqual(len(reopened.jobs()), 4)

    def test_immutable_plan_version_and_workspace_boundaries(self):
        plan = self.plan()
        self.session.preview(plan)
        plan["title"] = "A revision"
        with self.assertRaises(PlanError):
            self.session.preview(plan)
        plan["workspace_id"] = "other"
        with self.assertRaises(PlanError):
            self.session.preview(plan)

    def test_retry_preserves_previous_job_and_only_adds_one_retry(self):
        job_id = self.session.preview(self.plan())[0]
        job = self.store.read_json(job_id, "job")
        job.update(state="failed", error="fixture")
        self.store.update_job(job)
        retried = self.session.retry(job_id)
        self.assertEqual(retried["retry_of"], job_id)
        self.assertEqual(self.store.read_json(job_id, "job")["state"], "failed")
        with self.assertRaises(PlanError):
            self.session.retry(job_id)

    def test_upload_requires_permission_and_enforces_size(self):
        with self.assertRaises(PlanError):
            self.session.upload(io.BytesIO(b"data"), "new.mp4", "footage", False)
        with self.assertRaises(PlanError):
            self.session.upload(io.BytesIO(b""), "new.mp4", "footage", True)
        with self.assertRaises(PlanError):
            self.session.register("outside00000", "footage", True)

    def test_scene_editor_builds_confirmed_plan_in_row_order(self):
        assets = self.session.assets()
        source = next(asset["id"] for asset in assets if asset["kind"] == "footage")
        people = [{"Name": "Fixture name", "Title": "Fixture title", "Company": "Fixture company", "Confirmed": True, "Permission": True}]
        cuts = [{"Source": source, "In (s)": 0, "Out (s)": 2, "Role": "answer", "People": "Fixture name",
                 "Name card": "Fixture name", "Pause checked": True, "Caption": "Confirmed words"}]
        plan = build_plan("test", "scene-test", 1, "interview_reel", "Title", "CTA", 10, ["9:16"],
                          self.plan()["style"], assets, people, cuts)
        self.assertEqual(plan["segments"][0]["captions"][0]["end"], 2)
        people[0]["Permission"] = False
        with self.assertRaises(PlanError):
            build_plan("test", "scene-test", 1, "interview_reel", "Title", "CTA", 10, ["9:16"],
                       self.plan()["style"], assets, people, cuts)

    def test_file_ids_accept_drive_links_and_reject_non_ids(self):
        self.assertEqual(file_id("https://drive.google.com/file/d/12345678900/view"), "12345678900")
        with self.assertRaises(PlanError):
            file_id("../escape")


if __name__ == "__main__":
    unittest.main()

class DriveWorkerMediaTests(unittest.TestCase):
    def test_queue_to_private_combined_preview(self):
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        from PIL import Image
        from editor.drive_worker import process
        from test_engine import executable
        if not executable("ffmpeg") or not executable("ffprobe"):
            self.skipTest("FFmpeg integration dependencies are unavailable.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / "source.mp4"
            subprocess.run([executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
                            "-i", "testsrc2=s=640x360:r=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
            logo = root / "logo.png"
            Image.new("RGBA", (120, 60), (0, 150, 200, 255)).save(logo)
            font = Path(os.environ.get("EDITOR_TEST_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
            store = MemoryDrive()
            session = EditorSession({"EDITOR_WORKSPACE_ID": "test"}, store)
            keys = {}
            for kind, path in (("footage", clip), ("logo", logo), ("font", font)):
                with path.open("rb") as data:
                    keys[kind] = session.upload(data, path.name, kind, True)
            plan = fixture()
            plan["formats"] = ["9:16"]
            plan["clips"]["clip1"]["original_key"] = keys["footage"]
            plan["style"].update(font_key=keys["font"], logo_key=keys["logo"])
            job_id = session.preview(plan)[0]
            process(store, store.read_json(job_id, "job"))
            finished = session.jobs()[0]
            self.assertEqual(finished["state"], "ready", finished["error"])
            self.assertLessEqual(abs(finished["output"]["duration"] - 7), .04)
            self.assertEqual(len(session.usage()), 1)
            self.assertTrue(session.preview_link(job_id).startswith("https://drive.google.com/"))
            with self.assertRaises(PlanError):
                process(store, finished)
            self.assertEqual(len(session.usage()), 1)


if __name__ == "__main__":
    unittest.main()
