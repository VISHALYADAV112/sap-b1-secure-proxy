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

    def test_initial_state_does_not_expose_any_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            controller = AppController(Path(temporary))
            controller.config.sap_password = "synthetic-password"
            controller.config.api_key = "a" * 40
            controller.config.ngrok_authtoken = "synthetic-authtoken"
            api = DesktopApi(controller)

            result = api.get_initial_state()
            config = result["config"]

            self.assertTrue(result["ok"])
            self.assertNotIn("sap_password", config)
            self.assertNotIn("api_key", config)
            self.assertNotIn("ngrok_authtoken", config)
            self.assertTrue(config["sap_password_saved"])
            self.assertTrue(config["api_key_saved"])
            self.assertTrue(config["ngrok_authtoken_saved"])

    def test_api_key_is_returned_only_by_explicit_api_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            controller = AppController(Path(temporary))
            controller.config.api_key = "a" * 40
            api = DesktopApi(controller)

            result = api.get_api_key()

            self.assertTrue(result["ok"])
            self.assertEqual(result["api_key"], "a" * 40)

    def test_blank_secret_fields_preserve_stored_values_without_returning_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            controller = AppController(Path(temporary))
            controller.config.sap_password = "synthetic-password"
            controller.config.api_key = "a" * 40
            controller.config.ngrok_authtoken = "synthetic-authtoken"
            api = DesktopApi(controller)

            result = api.save_config(
                {
                    "sap_password": "",
                    "api_key": "",
                    "ngrok_authtoken": "",
                }
            )

            self.assertTrue(result["ok"])
            self.assertEqual(controller.config.sap_password, "synthetic-password")
            self.assertEqual(controller.config.api_key, "a" * 40)
            self.assertEqual(controller.config.ngrok_authtoken, "synthetic-authtoken")
            self.assertNotIn("sap_password", result["config"])
            self.assertNotIn("api_key", result["config"])
            self.assertNotIn("ngrok_authtoken", result["config"])

    def test_rotate_api_key_does_not_return_the_new_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            controller = AppController(Path(temporary))
            previous = controller.config.api_key
            api = DesktopApi(controller)

            result = api.generate_api_key()

            self.assertTrue(result["ok"])
            self.assertTrue(result["rotated"])
            self.assertNotIn("api_key", result)
            self.assertNotEqual(controller.config.api_key, previous)


if __name__ == "__main__":
    unittest.main()
