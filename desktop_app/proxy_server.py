from __future__ import annotations

import base64
import hmac
import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Callable

from flask import Flask, Response, jsonify, request
from werkzeug.serving import BaseWSGIServer, make_server

from .config import AppConfig
from .events import EventLog
from .sap_client import SAPClient, SapError, SapResponse

logger = logging.getLogger("sap_proxy")


class BoundedRateLimiter:
    def __init__(self, limit: int, max_keys: int = 2048):
        self.limit = limit
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        with self._lock:
            hits = self._hits.pop(key, deque())
            while hits and hits[0] < cutoff:
                hits.popleft()
            allowed = len(hits) < self.limit
            if allowed:
                hits.append(now)
            self._hits[key] = hits
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)
            return allowed


class ProxyServer:
    def __init__(
        self,
        config: AppConfig,
        sap_client: SAPClient,
        events: EventLog,
        public_url_provider: Callable[[], str],
    ):
        self.config = AppConfig.from_dict(config.to_dict())
        self.sap_client = sap_client
        self.events = events
        self.public_url_provider = public_url_provider
        self._server: BaseWSGIServer | None = None
        self._thread: threading.Thread | None = None
        self._authenticated_limiter = BoundedRateLimiter(300)
        self._unauthenticated_limiter = BoundedRateLimiter(30)
        self.app = self._build_app()

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.config.local_port}"

    def start(self) -> None:
        if self.running:
            return
        self._server = make_server("127.0.0.1", self.config.local_port, self.app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, name="proxy-server", daemon=True)
        self._thread.start()
        self.events.info(f"Local proxy listening on {self.local_url}")

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server:
            server.shutdown()
            server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
        self.events.info("Local proxy stopped")

    def _build_app(self) -> Flask:
        app = Flask(__name__)
        app.config.update(JSON_SORT_KEYS=False, PROPAGATE_EXCEPTIONS=False)

        @app.after_request
        def security_headers(response: Response) -> Response:
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            return response

        @app.get("/health")
        def health() -> Response:
            return jsonify({"status": "ok", "proxy": self.running})

        @app.route("/api/<path:entity>", methods=["GET", "HEAD"])
        @app.route("/<entity>", methods=["GET", "HEAD"])
        def proxy_entity(entity: str) -> Response:
            request_id = uuid.uuid4().hex[:12]
            provided_key = self._extract_api_key()
            if not self._valid_api_key(provided_key):
                identity = request.remote_addr or "unknown"
                if not self._unauthenticated_limiter.allow(identity):
                    return jsonify({"error": "Rate limit exceeded"}), 429
                return jsonify({"error": "Unauthorized"}), 401

            key_identity = provided_key[-12:]
            if not self._authenticated_limiter.allow(key_identity):
                return jsonify({"error": "Rate limit exceeded"}), 429

            try:
                query = [(key, value) for key, values in request.args.lists() for value in values]
                sap_response = self.sap_client.get(entity, query)
                content = self._rewrite_links(sap_response)
                self.events.info(f"GET {entity} -> SAP {sap_response.status}")
                response = Response(content, status=sap_response.status)
                for name, value in sap_response.headers.items():
                    response.headers[name] = value
                response.headers["X-Proxy-Request-Id"] = request_id
                return response
            except SapError as exc:
                self.events.error(f"Request {request_id} failed: {exc}")
                return jsonify({"error": str(exc), "request_id": request_id}), exc.status
            except Exception:
                self.events.error(f"Request {request_id} failed with an unexpected proxy error")
                return jsonify({"error": "Proxy error", "request_id": request_id}), 500

        return app

    def _extract_api_key(self) -> str:
        header_key = request.headers.get("X-API-Key", "").strip()
        if header_key:
            return header_key

        authorization = request.headers.get("Authorization", "").strip()
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        if authorization.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
                _, password = decoded.split(":", 1)
                return password
            except (ValueError, UnicodeDecodeError):
                return ""
        return ""

    def _valid_api_key(self, provided: str) -> bool:
        return bool(provided) and hmac.compare_digest(provided, self.config.api_key)

    def _rewrite_links(self, response: SapResponse) -> bytes:
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower() and "odata" not in content_type.lower():
            return response.content

        public_url = self.public_url_provider().rstrip("/")
        if not public_url:
            public_url = self.local_url
        source = f"{self.config.sap_base_url}/b1s/v1/"
        target = f"{public_url}/api/"
        return (
            response.content.replace(source.encode(), target.encode())
            .replace(source.replace("/", r"\/").encode(), target.replace("/", r"\/").encode())
        )
