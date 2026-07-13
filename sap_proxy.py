#!/usr/bin/env python3
"""
Secure SAP B1 Service Layer proxy for Render and Power BI.

Default posture:
- read-only SAP access (GET/HEAD only)
- API key required in X-API-Key or Authorization: Bearer
- SAP credentials only from environment variables
- SAP TLS verification enabled
- API keys stripped before forwarding to SAP
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from requests import Response
from requests.exceptions import RequestException


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sap-proxy")


class ConfigError(Exception):
    pass


class ClientError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Config:
    api_keys: tuple[str, ...]
    sap_server: str
    sap_port: str
    sap_company_db: str
    sap_username: str
    sap_password: str
    sap_language: int
    listen_port: int
    sap_verify_ssl: bool
    sap_ca_bundle: str | None
    allowed_methods: tuple[str, ...]
    allowed_path_prefixes: tuple[str, ...]
    blocked_paths: tuple[str, ...]
    allow_query_api_key: bool
    allowed_origins: tuple[str, ...]
    forwarded_headers: tuple[str, ...]
    request_timeout_seconds: int
    max_body_bytes: int
    max_response_bytes: int
    auth_rate_limit_per_minute: int
    unauth_rate_limit_per_minute: int
    login_on_startup: bool

    @property
    def target(self) -> str:
        return f"https://{self.sap_server}:{self.sap_port}"

    @property
    def verify_arg(self) -> bool | str:
        if self.sap_ca_bundle:
            return self.sap_ca_bundle
        return self.sap_verify_ssl


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _csv_env(name: str, default: str = "") -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _int_env(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < min_value or value > max_value:
        raise ConfigError(f"{name} must be between {min_value} and {max_value}")
    return value


def load_config() -> Config:
    api_keys = _csv_env("API_KEYS") or _csv_env("API_KEY")
    if not api_keys:
        raise ConfigError("Set API_KEYS to one or more strong API keys")

    allow_weak_local_keys = _bool_env("ALLOW_WEAK_API_KEYS_FOR_LOCAL", False)
    weak_keys = [key for key in api_keys if len(key) < 32]
    if weak_keys and not allow_weak_local_keys:
        raise ConfigError("Every API key must be at least 32 characters")

    allowed_methods = tuple(method.upper() for method in _csv_env("SAP_ALLOWED_METHODS", "GET,HEAD"))
    supported_methods = {"GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"}
    unsupported = [method for method in allowed_methods if method not in supported_methods]
    if unsupported:
        raise ConfigError(f"Unsupported SAP_ALLOWED_METHODS entries: {', '.join(unsupported)}")

    sap_port = _required_env("SAP_PORT")
    if not sap_port.isdigit():
        raise ConfigError("SAP_PORT must be numeric")

    return Config(
        api_keys=api_keys,
        sap_server=_required_env("SAP_SERVER"),
        sap_port=sap_port,
        sap_company_db=_required_env("SAP_COMPANY_DB"),
        sap_username=_required_env("SAP_USERNAME"),
        sap_password=_required_env("SAP_PASSWORD"),
        sap_language=_int_env("SAP_LANGUAGE", 23, 1, 99),
        listen_port=_int_env("PORT", 10000, 1, 65535),
        sap_verify_ssl=_bool_env("SAP_VERIFY_SSL", True),
        sap_ca_bundle=os.getenv("SAP_CA_BUNDLE") or None,
        allowed_methods=allowed_methods,
        allowed_path_prefixes=_csv_env("SAP_ALLOWED_PATH_PREFIXES", "/b1s/v1/"),
        blocked_paths=_csv_env("SAP_BLOCKED_PATHS", "/b1s/v1/Login,/b1s/v1/Logout"),
        allow_query_api_key=_bool_env("ALLOW_QUERY_API_KEY", False),
        allowed_origins=_csv_env("ALLOWED_ORIGINS"),
        forwarded_headers=_csv_env(
            "SAP_FORWARDED_HEADERS",
            "Accept,Accept-Language,Prefer,If-Match,If-None-Match",
        ),
        request_timeout_seconds=_int_env("REQUEST_TIMEOUT_SECONDS", 60, 1, 300),
        max_body_bytes=_int_env("MAX_BODY_BYTES", 1024 * 1024, 0, 20 * 1024 * 1024),
        max_response_bytes=_int_env("MAX_RESPONSE_BYTES", 50 * 1024 * 1024, 1024, 500 * 1024 * 1024),
        auth_rate_limit_per_minute=_int_env("AUTH_RATE_LIMIT_PER_MINUTE", 300, 0, 10000),
        unauth_rate_limit_per_minute=_int_env("UNAUTH_RATE_LIMIT_PER_MINUTE", 30, 0, 10000),
        login_on_startup=_bool_env("SAP_LOGIN_ON_STARTUP", True),
    )


class RateLimiter:
    def __init__(self, limit_per_minute: int):
        self.limit = limit_per_minute
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        cutoff = now - 60
        with self._lock:
            hits = [hit for hit in self._hits.get(key, []) if hit >= cutoff]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


class SAPSessionManager:
    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()
        self._session: requests.Session | None = None

    def renew(self) -> None:
        with self._lock:
            session = requests.Session()
            session.verify = self.config.verify_arg
            session.headers.update(
                {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "B1S-CompanyDB": self.config.sap_company_db,
                }
            )
            response = session.post(
                f"{self.config.target}/b1s/v1/Login",
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
            self._session = session
            log.info("SAP session renewed")

    def get_session(self) -> requests.Session:
        if self._session is None:
            self.renew()
        if self._session is None:
            raise RuntimeError("SAP session was not initialized")
        return self._session

    def request(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Response:
        response = self.get_session().request(
            method,
            url,
            headers=headers,
            data=body,
            timeout=self.config.request_timeout_seconds,
            allow_redirects=False,
            stream=True,
        )
        if response.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.MOVED_PERMANENTLY}:
            response.close()
            log.info("SAP session expired; renewing")
            self.renew()
            response = self.get_session().request(
                method,
                url,
                headers=headers,
                data=body,
                timeout=self.config.request_timeout_seconds,
                allow_redirects=False,
                stream=True,
            )
        return response


CONFIG: Config | None = None
SESSION_MANAGER: SAPSessionManager | None = None
AUTH_LIMITER = RateLimiter(300)
UNAUTH_LIMITER = RateLimiter(30)


def configure(config: Config, session_manager: SAPSessionManager | None = None) -> None:
    global CONFIG, SESSION_MANAGER, AUTH_LIMITER, UNAUTH_LIMITER
    CONFIG = config
    SESSION_MANAGER = session_manager or SAPSessionManager(config)
    AUTH_LIMITER = RateLimiter(config.auth_rate_limit_per_minute)
    UNAUTH_LIMITER = RateLimiter(config.unauth_rate_limit_per_minute)


def _config() -> Config:
    if CONFIG is None:
        raise RuntimeError("Proxy is not configured")
    return CONFIG


def _session_manager() -> SAPSessionManager:
    if SESSION_MANAGER is None:
        raise RuntimeError("SAP session manager is not configured")
    return SESSION_MANAGER


def _strip_api_key_query(query: str) -> str:
    pairs = [(key, value) for key, value in parse_qsl(query, keep_blank_values=True) if key.lower() != "api_key"]
    return urlencode(pairs, doseq=True)


def _join_header_values(values: Iterable[str]) -> str:
    return ", ".join(value for value in values if value)


class SecureThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SAPProxy"
    sys_version = ""

    def do_OPTIONS(self) -> None:
        self._send_empty(HTTPStatus.NO_CONTENT, extra_headers={"Allow": self._allow_header()})

    def do_GET(self) -> None:
        self._forward()

    def do_HEAD(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def do_PATCH(self) -> None:
        self._forward()

    def do_PUT(self) -> None:
        self._forward()

    def do_DELETE(self) -> None:
        self._forward()

    def _forward(self) -> None:
        request_id = uuid.uuid4().hex[:12]
        path = urlsplit(self.path).path

        if path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return

        key = self._extract_api_key()
        if not self._valid_api_key(key):
            if not UNAUTH_LIMITER.allow(self._client_ip()):
                self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Rate limit exceeded"})
                return
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
            return

        if not AUTH_LIMITER.allow(self._client_ip()):
            self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Rate limit exceeded"})
            return

        config = _config()
        if self.command not in config.allowed_methods:
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "Method not allowed"},
                extra_headers={"Allow": self._allow_header()},
            )
            return

        try:
            upstream_url = self._upstream_url()
            body = self._read_request_body()
            headers = self._forward_headers()
            response = _session_manager().request(self.command, upstream_url, headers, body)
            content = self._read_upstream_response(response)
            content = self._rewrite_content_links(response, content)
            self._send_upstream_response(response, content)
        except ClientError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except RequestException:
            log.exception("request_id=%s upstream request failed", request_id)
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "Bad gateway", "request_id": request_id})
        except Exception:
            log.exception("request_id=%s unexpected proxy error", request_id)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Proxy error", "request_id": request_id})

    def _extract_api_key(self) -> str:
        authorization = self.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()

        header_key = self.headers.get("X-API-Key", "")
        if header_key:
            return header_key.strip()

        if _config().allow_query_api_key:
            query = urlsplit(self.path).query
            for key, value in parse_qsl(query, keep_blank_values=True):
                if key.lower() == "api_key":
                    return value.strip()
        return ""

    def _valid_api_key(self, provided_key: str) -> bool:
        if not provided_key:
            return False
        return any(hmac.compare_digest(provided_key, expected_key) for expected_key in _config().api_keys)

    def _upstream_url(self) -> str:
        config = _config()
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc:
            raise ClientError(HTTPStatus.BAD_REQUEST, "Absolute URLs are not allowed")

        path = parsed.path or "/"
        if "\x00" in path or "/../" in path or path.endswith("/.."):
            raise ClientError(HTTPStatus.BAD_REQUEST, "Invalid path")

        if not path.startswith(config.allowed_path_prefixes):
            raise ClientError(HTTPStatus.FORBIDDEN, "Path is not allowed")

        normalized_path = path.rstrip("/")
        for blocked_path in config.blocked_paths:
            blocked = blocked_path.rstrip("/")
            if normalized_path == blocked or normalized_path.startswith(f"{blocked}/") or normalized_path.startswith(f"{blocked}("):
                raise ClientError(HTTPStatus.FORBIDDEN, "Path is blocked")

        query = _strip_api_key_query(parsed.query)
        return f"{config.target}{urlunsplit(('', '', path, query, ''))}"

    def _read_request_body(self) -> bytes | None:
        if self.command in {"GET", "HEAD"}:
            return None

        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise ClientError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length") from exc

        if content_length > _config().max_body_bytes:
            raise ClientError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        if content_length <= 0:
            return b""
        return self.rfile.read(content_length)

    def _forward_headers(self) -> dict[str, str]:
        forwarded: dict[str, str] = {}
        for header_name in _config().forwarded_headers:
            value = self.headers.get(header_name)
            if value:
                forwarded[header_name] = value[:2048]
        if self.command not in {"GET", "HEAD"}:
            content_type = self.headers.get("Content-Type")
            if content_type:
                forwarded["Content-Type"] = content_type[:256]
        return forwarded

    def _read_upstream_response(self, response: Response) -> bytes:
        limit = _config().max_response_bytes
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > limit:
                    raise ClientError(HTTPStatus.BAD_GATEWAY, "Upstream response too large")
            except ValueError:
                pass

        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise ClientError(HTTPStatus.BAD_GATEWAY, "Upstream response too large")
                chunks.append(chunk)
        finally:
            response.close()
        return b"".join(chunks)

    def _rewrite_content_links(self, response: Response, content: bytes) -> bytes:
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower() and "odata" not in content_type.lower():
            return content

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return content

        config = _config()
        external_base = self._external_base()
        text = text.replace(config.target, external_base)
        text = text.replace(config.target.replace("/", r"\/"), external_base.replace("/", r"\/"))
        return text.encode("utf-8")

    def _send_upstream_response(self, response: Response, content: bytes) -> None:
        headers: dict[str, str] = {}
        for header_name in ("Content-Type", "OData-Version", "Preference-Applied", "ETag"):
            value = response.headers.get(header_name)
            if value:
                headers[header_name] = value
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/octet-stream"
        self._send_bytes(response.status_code, content, headers)

    def _send_json(self, status: int, data: dict[str, object], extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        self._send_bytes(status, body, headers)

    def _send_empty(self, status: int, extra_headers: dict[str, str] | None = None) -> None:
        self._send_bytes(status, b"", extra_headers or {})

    def _send_bytes(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.close_connection = True
        self.send_response(status)
        self._standard_headers(len(body))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _standard_headers(self, body_length: int) -> None:
        self.send_header("Content-Length", str(body_length if self.command != "HEAD" else 0))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header("Connection", "close")
        self._cors_headers()

    def _cors_headers(self) -> None:
        config = _config()
        origin = self.headers.get("Origin")
        if not origin or not config.allowed_origins:
            return

        if "*" in config.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", "*")
        elif origin in config.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        else:
            return

        self.send_header("Access-Control-Allow-Methods", self._allow_header())
        self.send_header("Access-Control-Allow-Headers", "Authorization, X-API-Key, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def _allow_header(self) -> str:
        return _join_header_values((*_config().allowed_methods, "OPTIONS"))

    def _external_base(self) -> str:
        proto = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip() or "http"
        host = (
            self.headers.get("X-Forwarded-Host")
            or self.headers.get("Host")
            or f"localhost:{_config().listen_port}"
        )
        host = host.split(",")[0].strip()
        return f"{proto}://{host}"

    def _client_ip(self) -> str:
        forwarded_for = self.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return self.client_address[0]

    def _safe_path(self) -> str:
        parsed = urlsplit(self.path)
        query = _strip_api_key_query(parsed.query)
        return urlunsplit(("", "", parsed.path, query, ""))

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        log.info("%s %s %s -> %s", self._client_ip(), self.command, self._safe_path(), code)

    def log_message(self, fmt: str, *args: object) -> None:
        log.info("%s - %s", self._client_ip(), fmt % args)


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        log.error("Invalid configuration: %s", exc)
        return 2

    if not config.sap_verify_ssl:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log.warning("SAP_VERIFY_SSL=false; use SAP_CA_BUNDLE instead for production")

    configure(config)

    if config.login_on_startup:
        try:
            _session_manager().renew()
        except Exception as exc:
            log.warning("Initial SAP login failed; will retry on first request: %s", exc)

    server = SecureThreadingHTTPServer(("0.0.0.0", config.listen_port), ProxyHandler)
    log.info("Proxy ready on 0.0.0.0:%d -> %s", config.listen_port, config.target)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Proxy stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
