import os
import tempfile
import unittest
from pathlib import Path

from desktop_app.config import AppConfig
from desktop_app.events import EventLog
from desktop_app.tunnel import TunnelManager, remove_legacy_ngrok_config


class TunnelManagerTest(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            ngrok_authtoken="synthetic-authtoken-value",
            ngrok_domain="demo.ngrok-free.app",
        )

    def test_process_environment_carries_token_without_mutating_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = TunnelManager(self.config, EventLog(), Path(temporary))
            original = os.environ.get("NGROK_AUTHTOKEN")

            environment = manager._process_environment()

            self.assertEqual(
                environment["NGROK_AUTHTOKEN"],
                self.config.ngrok_authtoken,
            )
            self.assertEqual(os.environ.get("NGROK_AUTHTOKEN"), original)

    def test_removes_legacy_plaintext_ngrok_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = TunnelManager(self.config, EventLog(), Path(temporary))
            manager.legacy_ngrok_config.write_text(
                "version: 2\nauthtoken: synthetic-authtoken-value\n",
                encoding="utf-8",
            )

            manager._remove_legacy_config()

            self.assertFalse(manager.legacy_ngrok_config.exists())

    def test_legacy_cleanup_reports_whether_a_file_was_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "ngrok.yml"
            path.write_text("authtoken: synthetic-authtoken-value\n", encoding="utf-8")

            self.assertTrue(remove_legacy_ngrok_config(root))
            self.assertFalse(remove_legacy_ngrok_config(root))


if __name__ == "__main__":
    unittest.main()
