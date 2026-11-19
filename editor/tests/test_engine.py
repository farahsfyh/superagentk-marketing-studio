import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from editor.plan import PlanError, validate
from editor.render import checksum, probe, render
from editor.worker import enqueue


def fixture():
    return {
        "workspace_id": "test", "project_id": "reference-fixture", "version": 1,
        "template": "interview_reel", "formats": ["16:9", "9:16", "4:5", "1:1"],
        "target_seconds": 7, "title": "Render test", "cta": "End of test",
        "style": {"background": "#10263D", "text": "#FFFFFF", "accent": "#66B6EF",
                  "font_key": "test/font.ttf", "font_licensed": True, "logo_key": "test/logo.png"},
        "clips": {"clip1": {"original_key": "test/clip.mp4"}},
        "people": {"p1": {"name": "Test participant", "title": "Test role", "company": "Test workspace",
                          "consent": True, "confirmed": True}},
        "segments": [
            {"clip": "clip1", "in": .5, "out": 1.5, "role": "answer", "people": ["p1"],
             "pause_checked": True, "name_card": "p1",
             "captions": [{"start": 0, "end": .8, "text": "Confirmed fixture text"}]},
            {"clip": "clip1", "in": 1.7, "out": 2.7, "role": "session", "people": [], "audio": "mute", "pause_checked": True}
        ]
    }


class PlanTests(unittest.TestCase):
    def test_cards_count_toward_target(self):
        plan = fixture()
        self.assertEqual(validate(plan), 7)
        plan["target_seconds"] = 6.9
        with self.assertRaises(PlanError):
            validate(plan)

    def test_requires_facts_consent_and_pause_confirmation(self):
        for path, value in ((("people", "p1", "company"), ""), (("people", "p1", "consent"), False),
                            (("people", "p1", "confirmed"), False), (("segments", 0, "pause_checked"), False)):
            plan = fixture()
            target = plan
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.assertRaises(PlanError):
                validate(plan)

    def test_steady_shot_and_caption_boundaries(self):
        for mutate in (lambda p: p["segments"][1].update(frame="center_crop"),
                       lambda p: p["segments"][0]["captions"][0].update(end=2)):
            plan = fixture()
            mutate(plan)
            with self.assertRaises(PlanError):
                validate(plan)

    def test_blocks_paths_and_bad_types(self):
        for key in ("../logo.png", "https://example.test/logo.png", "a/../b", "/etc/passwd"):
            plan = fixture()
            plan["style"]["logo_key"] = key
            with self.assertRaises(PlanError):
                validate(plan)
        plan = fixture()
        plan["segments"][0]["in"] = float("nan")
        with self.assertRaises(PlanError):
            validate(plan)

    def test_master_needs_approval_before_database_connection(self):
        with self.assertRaises(PlanError):
            enqueue(fixture(), "master")


def executable(name):
    return os.environ.get(name.upper()) or shutil.which(name)


@unittest.skipUnless(executable("ffmpeg") and executable("ffprobe"), "Install FFmpeg and ffprobe for the media integration test.")
class RenderTests(unittest.TestCase):
    def test_real_media_all_formats_and_preserved_plan(self):
        font = Path(os.environ.get("EDITOR_TEST_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
        self.assertTrue(font.is_file(), "Set EDITOR_TEST_FONT to an installed TTF for the media test.")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clip = root / "clip.mp4"
            subprocess.run([executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
                            "-i", "testsrc2=s=640x360:r=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
            logo = root / "logo.png"
            Image.new("RGBA", (120, 60), (0, 150, 200, 255)).save(logo)
            files = {"test/clip.mp4": clip, "test/font.ttf": font, "test/logo.png": logo}
            plan = fixture()
            before = copy.deepcopy(plan)
            for format_name in plan["formats"]:
                output = render(plan, files, root / format_name.replace(":", "-"), format_name, "preview")
                self.assertLessEqual(abs(output["duration"] - 7), .04)
                self.assertEqual(output["checksum"], checksum(output["path"]))
                self.assertTrue(probe(output["path"])["audio"])
                self.assertTrue(output["thumbnail"].is_file())
                self.assertIn("audio sync within 3 ms", output["review_required"])
                width, height = map(int, format_name.split(":"))
                self.assertAlmostEqual(output["width"] / output["height"], width / height, places=2)
            self.assertEqual(plan, before)

    def test_cut_past_end_fails(self):
        # Probe is exercised by the complete render integration test.
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (10, 10)).save(root / "logo.png")
            plan = fixture()
            files = {"test/clip.mp4": root / "clip.mp4", "test/font.ttf": root / "font.ttf", "test/logo.png": root / "logo.png"}
            with patch("editor.render.probe", return_value={"duration": .7, "audio": True, "video": {}}):
                with self.assertRaises(PlanError):
                    render(plan, files, root / "out", "16:9", "preview")


if __name__ == "__main__":
    unittest.main()
