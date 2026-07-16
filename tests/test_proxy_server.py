import base64
import unittest

from desktop_app.config import AppConfig
from desktop_app.events import EventLog
from desktop_app.proxy_server import BoundedRateLimiter, ProxyServer
from desktop_app.sap_client import SapResponse


class FakeSapClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, entity, query):
        self.calls.append((entity, list(query)))
        return self.response


class ProxyServerTest(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            sap_server="sap.internal",
            sap_company_db="COMPANY",
            sap_username="manager",
            sap_password="password",
            api_key="a" * 40,
            start_tunnel=False,
        )
        self.sap = FakeSapClient(
            SapResponse(
                200,
                b'{"@odata.nextLink":"https://sap.internal:50000/b1s/v1/Invoices?$skip=20"}',
                {"Content-Type": "application/json"},
            )
        )
        self.proxy = ProxyServer(
            self.config,
            self.sap,
            EventLog(),
            lambda: "https://demo.ngrok-free.app",
        )
        self.client = self.proxy.app.test_client()

    def test_rejects_missing_api_key(self):
        response = self.client.get("/api/Invoices")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.sap.calls, [])

    def test_forwards_query_and_rewrites_next_link(self):
        response = self.client.get(
            "/api/Invoices?select=DocNum&top=20",
            headers={"X-API-Key": "a" * 40},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.sap.calls,
            [("Invoices", [("select", "DocNum"), ("top", "20")])],
        )
        self.assertIn(b"https://demo.ngrok-free.app/api/Invoices", response.data)
        self.assertNotIn(b"sap.internal", response.data)

    def test_accepts_api_key_as_basic_password(self):
        encoded = base64.b64encode(("powerbi:" + ("a" * 40)).encode()).decode()
        response = self.client.get("/Invoices", headers={"Authorization": f"Basic {encoded}"})
        self.assertEqual(response.status_code, 200)

    def test_rate_limiter_has_bounded_identity_storage(self):
        limiter = BoundedRateLimiter(1, max_keys=3)
        for number in range(10):
            self.assertTrue(limiter.allow(str(number)))
        self.assertLessEqual(len(limiter._hits), 3)


if __name__ == "__main__":
    unittest.main()
