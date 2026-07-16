from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig, ConfigError, ConfigStore
from .events import EventLog
from .platform_services import StartupError, get_startup_service
from .powerbi import generate_m_code
from .proxy_server import ProxyServer
from .sap_client import SAPClient, SapError
from .tunnel import TunnelError, TunnelManager
from .paths import app_data_dir


class AppController:
    def __init__(self, config_root: Path | None = None):
        log_root = config_root or app_data_dir()
        self.events = EventLog(log_path=log_root / "logs" / "proxy.log")
        self.config_store = ConfigStore(config_root)
        try:
            self.config = self.config_store.load()
        except Exception as exc:
            self.config = AppConfig()
            self.config.ensure_api_key()
            self.events.error(f"Configuration could not be loaded: {exc}")

        self.sap_client: SAPClient | None = None
        self.proxy: ProxyServer | None = None
        self.tunnel: TunnelManager | None = None
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._busy = False
        self._sap_connected = False
        self._last_error = ""
        self._exit_requested = False
        self._open_window: Callable[[], None] | None = None
        self._exit_app: Callable[[], None] | None = None
        self.startup_service = get_startup_service(self.events)
        try:
            self._startup_enabled = self.startup_service.is_enabled()
        except Exception:
            self._startup_enabled = False
        self.events.info("Desktop proxy initialized")

    def set_window_actions(self, open_window: Callable[[], None], exit_app: Callable[[], None]) -> None:
        self._open_window = open_window
        self._exit_app = exit_app

    @property
    def running(self) -> bool:
        return bool(self.proxy and self.proxy.running)

    @property
    def public_url(self) -> str:
        if self.tunnel:
            return self.tunnel.public_url
        return ""

    def initial_state(self) -> dict[str, Any]:
        return {
            **self.state(),
            "config": self.config.to_dict(include_secrets=True),
            "platform": __import__("platform").system(),
        }

    def state(self, since: int = 0) -> dict[str, Any]:
        with self._lock:
            local_url = self.proxy.local_url if self.proxy else f"http://127.0.0.1:{self.config.local_port}"
            return {
                "busy": self._busy,
                "running": self.running,
                "sap_connected": self._sap_connected,
                "tunnel_running": bool(self.tunnel and self.tunnel.running),
                "local_url": local_url,
                "public_url": self.public_url,
                "startup_enabled": self._startup_enabled,
                "last_error": self._last_error,
                "logs": self.events.since(since),
                "latest_log_id": self.events.latest_sequence,
            }

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._busy or self.running:
                raise ConfigError("Stop the proxy before changing settings")
            merged = self.config.to_dict(include_secrets=True)
            merged.update(payload)
            updated = AppConfig.from_dict(merged)
            updated.ensure_api_key()
            updated.validate(require_connection=False)
            self.config_store.save(updated)
            self.config = updated
            self._last_error = ""
            self.events.info("Configuration saved")
            return self.config.to_dict(include_secrets=True)

    def generate_api_key(self) -> str:
        with self._lock:
            if self.running:
                raise ConfigError("Stop the proxy before rotating the API key")
            self.config.api_key = ""
            self.config.ensure_api_key()
            self.config_store.save(self.config)
            self.events.info("Proxy API key rotated")
            return self.config.api_key

    def test_connection(self) -> dict[str, Any]:
        with self._lock:
            if self._busy:
                raise ConfigError("Another operation is already running")
            test_config = copy.deepcopy(self.config)
            test_config.start_tunnel = False
            test_config.validate(require_connection=True)
            self._busy = True
            self._last_error = ""

        def worker() -> None:
            client = SAPClient(test_config, self.events)
            try:
                client.test_connection()
                with self._lock:
                    self._sap_connected = True
                self.events.info("SAP connection test succeeded")
            except Exception as exc:
                self._record_error(exc)
            finally:
                client.close()
                with self._lock:
                    self._busy = False

        self._worker = threading.Thread(target=worker, name="sap-test", daemon=True)
        self._worker.start()
        return {"started": True}

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"started": False, "message": "Proxy is already running"}
            if self._busy:
                raise ConfigError("Another operation is already running")
            self.config.ensure_api_key()
            self.config.validate(require_connection=True)
            self.config_store.save(self.config)
            start_config = copy.deepcopy(self.config)
            self._busy = True
            self._last_error = ""

        self._worker = threading.Thread(
            target=self._start_worker,
            args=(start_config,),
            name="service-start",
            daemon=True,
        )
        self._worker.start()
        return {"started": True}

    def _start_worker(self, config: AppConfig) -> None:
        client: SAPClient | None = None
        proxy: ProxyServer | None = None
        tunnel: TunnelManager | None = None
        try:
            client = SAPClient(config, self.events)
            client.test_connection()
            with self._lock:
                self._sap_connected = True

            tunnel = TunnelManager(config, self.events)
            proxy = ProxyServer(config, client, self.events, lambda: tunnel.public_url)
            proxy.start()
            if config.start_tunnel:
                tunnel.start()

            with self._lock:
                self.sap_client = client
                self.proxy = proxy
                self.tunnel = tunnel
            self.events.info("SAP B1 proxy services are running")
        except Exception as exc:
            if tunnel:
                tunnel.stop()
            if proxy:
                proxy.stop()
            if client:
                client.close()
            with self._lock:
                self._sap_connected = False
            self._record_error(exc)
        finally:
            with self._lock:
                self._busy = False

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._busy:
                raise ConfigError("Wait for the current operation to finish")
            if not self.running and not (self.tunnel and self.tunnel.running):
                return {"stopped": False, "message": "Proxy is already stopped"}
            self._busy = True

        def worker() -> None:
            try:
                self._stop_services()
            finally:
                with self._lock:
                    self._busy = False

        self._worker = threading.Thread(target=worker, name="service-stop", daemon=True)
        self._worker.start()
        return {"stopped": True}

    def _stop_services(self) -> None:
        with self._lock:
            tunnel, proxy, client = self.tunnel, self.proxy, self.sap_client
            self.tunnel = None
            self.proxy = None
            self.sap_client = None
        if tunnel:
            tunnel.stop()
        if proxy:
            proxy.stop()
        if client:
            client.close()
        with self._lock:
            self._sap_connected = False
        self.events.info("All proxy services stopped")

    def set_startup(self, enabled: bool) -> dict[str, Any]:
        try:
            if enabled:
                self.startup_service.enable()
            else:
                self.startup_service.disable()
            self._startup_enabled = self.startup_service.is_enabled()
            return {"startup_enabled": self._startup_enabled}
        except StartupError:
            raise
        except Exception as exc:
            raise StartupError(str(exc)) from exc

    def power_bi_code(self, entity: str, select_fields: str, public_url: str = "") -> str:
        url = public_url.strip() or self.public_url
        if not url and not self.config.start_tunnel:
            url = f"http://127.0.0.1:{self.config.local_port}"
        return generate_m_code(self.config, url, entity, select_fields)

    def open_window(self) -> None:
        if self._open_window:
            self._open_window()

    def request_exit(self) -> None:
        self._exit_requested = True
        if self._exit_app:
            self._exit_app()

    @property
    def exit_requested(self) -> bool:
        return self._exit_requested

    def shutdown(self) -> None:
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=30)
        self._stop_services()

    def _record_error(self, exc: Exception) -> None:
        if isinstance(exc, (ConfigError, SapError, TunnelError, StartupError)):
            message = str(exc)
        else:
            message = f"{exc.__class__.__name__}: {exc}"
        with self._lock:
            self._last_error = message
        self.events.error(message)
