import os
import secrets
import unittest
from unittest.mock import patch

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


@unittest.skipUnless(AppTest, "Install Streamlit to run the UI tests.")
class EditorUITests(unittest.TestCase):
    def test_unconfigured_editor_explains_setup(self):
        from editor.app import REQUIRED
        from pathlib import Path
        entrypoint = str(Path(__file__).resolve().parents[2] / "SuperAgentK Streamlit GitHub files/streamlit_app.py")
        with patch.dict(os.environ, {"EDITOR_UI_ENABLED": "true"}), patch("editor.app.settings", return_value={key: "" for key in REQUIRED}):
            app = AppTest.from_file(entrypoint, default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.title[0].value, "Set up Video Editor")

    def test_private_workspace_pages_and_version_navigation(self):
        from pathlib import Path
        from test_drive import DriveSessionTests
        case = DriveSessionTests()
        case.setUp()
        session = case.session
        session.preview(case.plan())
        password = secrets.token_urlsafe(24)
        config = {"EDITOR_ACCESS_PASSWORD": password, "EDITOR_WORKSPACE_ID": "test",
                  "GOOGLE_DRIVE_FOLDER_ID": "12345678900", "GOOGLE_DRIVE_OAUTH": {"configured": True}}
        entrypoint = str(Path(__file__).resolve().parents[2] / "SuperAgentK Streamlit GitHub files/streamlit_app.py")
        with patch.dict(os.environ, {"EDITOR_UI_ENABLED": "true"}), patch("editor.app.settings", return_value=config), patch("editor.app.EditorSession", return_value=session):
            app = AppTest.from_file(entrypoint, default_timeout=30).run()
            self.assertEqual(app.title[0].value, "Video Editor")
            app.text_input[0].set_value(password)
            app.button[0].click().run()
            self.assertFalse(app.exception)
            for page in ("Build the edit", "Library", "Usage"):
                app.radio(key="editor_navigation").set_value(page).run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                self.assertEqual(app.title[0].value, page)
            app.radio(key="editor_navigation").set_value("Library").run()
            next(button for button in app.button if button.label == "Revise this version").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.title[0].value, "Build the edit")
