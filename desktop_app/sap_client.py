from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode

import requests
import urllib3
from requests import Response
from requests.exceptions import RequestException

from .config import AppConfig, ENTITY_PATTERN
from .events import EventLog


QUERY_ALIASES = {
    "select": "$select",
    "$select": "$select",
    "filter": "$filter",
    "$filter": "$filter",
    "expand": "$expand",
    "$expand": "$expand",
    "top": "$top",
    "$top": "$top",
    "skip": "$skip",
    "$skip": "$skip",
    "orderby": "$orderby",
    "$orderby": "$orderby",
    "count": "$count",
    "$count": "$count",
}


class SapError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SapResponse:
    status: int
    content: bytes
    headers: dict[str, str]


class SAPClient:
    def __init__(self, config: AppConfig, events: EventLog):
        self.config = AppConfig.from_dict(config.to_dict())
        self.events = events
        self._lock = threading.RLock()
        self._session = self._create_session()
        self._authenticated = False

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.verify = self.config.sap_ca_bundle or self.config.sap_verify_ssl
        session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "B1S-CompanyDB": self.config.sap_company_db,
            }
        )
        if not self.config.sap_verify_ssl and not self.config.sap_ca_bundle:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return session

    def login(self) -> None:
        with self._lock:
            self.events.info(f"Connecting to SAP at {self.config.sap_server}:{self.config.sap_port}")
            try:
                response = self._session.post(
                    f"{self.config.sap_base_url}/b1s/v1/Login",
                    json={
                        "CompanyDB": self.config.sap_company_db,
                        "UserName": self.config.sap_username,
                        "Password": self.config.sap_password,
                        "Language": self.config.sap_language,
                    },
                    timeout=self.config.request_timeout_seconds,
                    allow_redirects=False,
                )
                response.raise_for_status()
            except RequestException as exc:
                self._authenticated = False
                raise SapError(_request_error_message("SAP login failed", exc)) from exc
            self._authenticated = True
            self.events.info("SAP session established")

    def test_connection(self) -> dict[str, str]:
        self.login()
        return {
            "server": self.config.sap_server,
            "company_db": self.config.sap_company_db,
            "username": self.config.sap_username,
        }

    def get(self, entity: str, query: Iterable[tuple[str, str]]) -> SapResponse:
        if not ENTITY_PATTERN.fullmatch(entity):
            raise SapError("Entity contains unsupported characters", status=400)

        query_pairs: list[tuple[str, str]] = []
        for raw_key, value in query:
            key = raw_key.lower()
            if key in {"api_key", "x-api-key"}:
                continue
            mapped = QUERY_ALIASES.get(key)
            if not mapped:
                raise SapError(f"Unsupported query option: {raw_key}", status=400)
            query_pairs.append((mapped, value))

        suffix = f"?{urlencode(query_pairs, doseq=True)}" if query_pairs else ""
        url = f"{self.config.sap_base_url}/b1s/v1/{entity}{suffix}"

        with self._lock:
            if not self._authenticated:
                self.login()
            response = self._request(url)
            if response.status_code in {301, 401}:
                response.close()
                self.events.warning("SAP session expired; authenticating again")
                self.login()
                response = self._request(url)
            return self._consume_response(response)

    def _request(self, url: str) -> Response:
        try:
            return self._session.get(
                url,
                timeout=self.config.request_timeout_seconds,
                allow_redirects=False,
                stream=True,
            )
        except RequestException as exc:
            raise SapError(_request_error_message("SAP request failed", exc)) from exc

    def _consume_response(self, response: Response) -> SapResponse:
        limit = self.config.max_response_mb * 1024 * 1024
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise SapError("SAP response exceeded the configured size limit")
                chunks.append(chunk)
            selected_headers = {
                name: value
                for name in ("Content-Type", "OData-Version", "ETag", "Preference-Applied")
                if (value := response.headers.get(name))
            }
            return SapResponse(response.status_code, b"".join(chunks), selected_headers)
        finally:
            response.close()

    def close(self) -> None:
        with self._lock:
            if self._authenticated:
                try:
                    self._session.post(
                        f"{self.config.sap_base_url}/b1s/v1/Logout",
                        timeout=min(self.config.request_timeout_seconds, 10),
                        allow_redirects=False,
                    )
                except RequestException:
                    pass
            self._authenticated = False
            self._session.close()


def _request_error_message(prefix: str, exc: RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        return f"{prefix}: HTTP {response.status_code}"
    return f"{prefix}: {exc.__class__.__name__}"
