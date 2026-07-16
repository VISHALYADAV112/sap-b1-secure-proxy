import unittest

from desktop_app.config import AppConfig, ConfigError
from desktop_app.powerbi import generate_m_code


class PowerBiTest(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            api_key="k" * 40,
            default_entity="Invoices",
            default_select="DocNum,DocDate",
        )

    def test_generates_static_base_and_relative_path(self):
        code = generate_m_code(self.config, "https://demo.ngrok-free.app/")
        self.assertIn('BaseUrl = "https://demo.ngrok-free.app"', code)
        self.assertIn('RelativePath = "api/Invoices"', code)
        self.assertIn('#"$select" = "DocNum,DocDate"', code)
        self.assertIn('#"x-api-key" = "' + ("k" * 40) + '"', code)

    def test_rejects_invalid_entity(self):
        with self.assertRaises(ConfigError):
            generate_m_code(self.config, "https://demo.ngrok-free.app", "../Login")


if __name__ == "__main__":
    unittest.main()
