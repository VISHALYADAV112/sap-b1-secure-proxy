from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

from .controller import AppController


class DesktopApi:
    def __init__(self, controller: AppController):
        # PyWebView recursively exposes public object attributes. Keep internal
        # references private so bridge generation only includes API methods.
        self._controller = controller
        self._window: Any = None

    def _bind_window(self, window: Any) -> None:
        self._window = window

    def ping(self) -> dict[str, Any]:
        self._controller.events.debug("Desktop bridge handshake received")
        return {
            "ok": True,
            "platform": platform.system(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "log_path": str(self._controller.log_path),
        }

    def get_initial_state(self) -> dict[str, Any]:
        self._controller.events.debug("Desktop interface requested initial state")
        result = self._call(self._controller.initial_state)
        if result.get("ok"):
            self._controller.events.info("Desktop bridge initialization completed")
        else:
            self._controller.events.error(
                f"Desktop bridge initialization failed: {result.get('error', 'unknown error')}"
            )
        return result

    def get_state(self, since: int = 0) -> dict[str, Any]:
        return self._call(self._controller.state, int(since or 0))

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call(lambda: {"config": self._controller.save_config(payload)})

    def get_api_key(self) -> dict[str, Any]:
        return self._call(lambda: {"api_key": self._controller.get_api_key()})

    def generate_api_key(self) -> dict[str, Any]:
        return self._call(lambda: {"rotated": bool(self._controller.generate_api_key())})

    def test_connection(self) -> dict[str, Any]:
        return self._call(self._controller.test_connection)

    def start_services(self) -> dict[str, Any]:
        return self._call(self._controller.start)

    def stop_services(self) -> dict[str, Any]:
        return self._call(self._controller.stop)

    def set_startup(self, enabled: bool) -> dict[str, Any]:
        return self._call(self._controller.set_startup, bool(enabled))

    def generate_power_bi_code(
        self,
        entity: str,
        select_fields: str,
        public_url: str = "",
    ) -> dict[str, Any]:
        return self._call(
            lambda: {
                "code": self._controller.power_bi_code(entity, select_fields, public_url),
            }
        )

    def browse_ca_bundle(self) -> dict[str, Any]:
        if not self._window:
            return {"ok": False, "error": "Desktop window is not available"}
        try:
            import webview

            dialog_type = getattr(getattr(webview, "FileDialog", object), "OPEN", None)
            if dialog_type is None:
                dialog_type = webview.OPEN_DIALOG
            result = self._window.create_file_dialog(
                dialog_type,
                allow_multiple=False,
                file_types=("Certificates (*.cer;*.crt;*.pem)", "All files (*.*)"),
            )
            selected = str(Path(result[0])) if result else ""
            return {"ok": True, "path": selected}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def minimize_window(self) -> dict[str, Any]:
        if self._window:
            self._window.hide()
        return {"ok": True}

    def exit_application(self) -> dict[str, Any]:
        self._controller.request_exit()
        return {"ok": True}

    @staticmethod
    def _call(function: Any, *args: Any) -> dict[str, Any]:
        try:
            result = function(*args)
            if isinstance(result, dict):
                return {"ok": True, **result}
            return {"ok": True, "result": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
