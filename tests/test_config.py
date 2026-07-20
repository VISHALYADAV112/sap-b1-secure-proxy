import json
import tempfile
import unittest
from pathlib import Path

from desktop_app.config import AppConfig, ConfigError, ConfigStore, normalize_domain, normalize_server


class MemorySecretStore:
    def __init__(self):
        self.values = {}

    def load(self):
        return dict(self.values)

    def save(self, values):
        self.values = dict(values)


class ConfigTest(unittest.TestCase):
    def test_config_round_trip_keeps_secrets_out_of_config_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            secrets = MemorySecretStore()
            store = ConfigStore(Path(temporary), secrets)
            config = AppConfig(
                sap_server="sap.internal",
                sap_company_db="COMPANY",
                sap_username="manager",
                sap_password="secret-password",
                api_key="a" * 40,
                ngrok_authtoken="ngrok-secret",
            )

            store.save(config)
            loaded = store.load()

            public_data = json.loads((Path(temporary) / "config.json").read_text())
            self.assertNotIn("sap_password", public_data)
            self.assertNotIn("api_key", public_data)
            self.assertNotIn("ngrok_authtoken", public_data)
            self.assertEqual(loaded.sap_password, "secret-password")
            self.assertEqual(loaded.api_key, "a" * 40)
            self.assertEqual(loaded.ngrok_authtoken, "ngrok-secret")

    def test_load_generates_api_key_for_new_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ConfigStore(Path(temporary), MemorySecretStore())
            config = store.load()
            self.assertGreaterEqual(len(config.api_key), 32)

    def test_normalizes_server_and_domain(self):
        self.assertEqual(normalize_server("https://sap.internal/"), "sap.internal")
        self.assertEqual(normalize_domain("https://Demo.Ngrok-Free.App/"), "demo.ngrok-free.app")

    def test_rejects_server_with_embedded_port(self):
        with self.assertRaises(ConfigError):
            normalize_server("sap.internal:50000")

    def test_rejects_invalid_embedded_port_cleanly(self):
        with self.assertRaises(ConfigError):
            normalize_server("sap.internal:not-a-port")

    def test_requires_tunnel_token_when_starting(self):
        config = AppConfig(
            sap_server="sap.internal",
            sap_company_db="COMPANY",
            sap_username="manager",
            sap_password="password",
            api_key="a" * 40,
            start_tunnel=True,
        )
        with self.assertRaises(ConfigError):
            config.validate(require_connection=True)

    def test_rejects_missing_numeric_setting_cleanly(self):
        with self.assertRaisesRegex(ConfigError, "SAP port is required"):
            AppConfig.from_dict({"sap_port": None})

    def test_rejects_invalid_numeric_setting_cleanly(self):
        with self.assertRaisesRegex(ConfigError, "Local proxy port must be an integer"):
            AppConfig.from_dict({"local_port": "not-a-port"})


if __name__ == "__main__":
    unittest.main()
