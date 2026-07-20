import tempfile
import unittest
import inspect
from pathlib import Path

from desktop_app.api import DesktopApi
from desktop_app.controller import AppController


class DesktopApiTest(unittest.TestCase):
    def test_bridge_has_no_public_object_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            controller = AppController(Path(temporary))
            api = DesktopApi(controller)

            public_attributes = [
                name for name in api.__dict__
                if not name.startswith("_")
            ]
            exposed_methods = [
                name for name, value in inspect.getmembers(api)
                if not name.startswith("_") and callable(value)
            ]

            self.assertEqual(public_attributes, [])
            self.assertNotIn("bind_window", exposed_methods)

    def test_ping_reports_log_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            controller = AppController(Path(temporary))
            api = DesktopApi(controller)

            result = api.ping()

            self.assertTrue(result["ok"])
            self.assertEqual(result["log_path"], str(controller.log_path))


if __name__ == "__main__":
    unittest.main()
