import json
import tempfile
import unittest
from pathlib import Path

from desktop_app.events import EventLog, redact


class EventLogTest(unittest.TestCase):
    def test_redacts_common_secret_labels(self):
        message = "password=secret api_key: token Authorization=BearerValue"
        cleaned = redact(message)
        self.assertNotIn("secret", cleaned)
        self.assertNotIn("token", cleaned)
        self.assertNotIn("BearerValue", cleaned)

    def test_since_returns_only_newer_entries(self):
        events = EventLog()
        first = events.add("INFO", "first")
        events.add("ERROR", "second")
        entries = events.since(first["id"])
        self.assertEqual([entry["message"] for entry in entries], ["second"])

    def test_persists_redacted_json_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "logs" / "proxy.log"
            EventLog(log_path=path).add("INFO", "password=secret")
            entry = json.loads(path.read_text().strip())
            self.assertEqual(entry["message"], "password=[redacted]")


if __name__ == "__main__":
    unittest.main()
