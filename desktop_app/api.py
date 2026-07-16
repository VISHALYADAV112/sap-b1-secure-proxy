from __future__ import annotations

from pathlib import Path
from typing import Any

from .controller import AppController


class DesktopApi:
    def __init__(self, controller: AppController):
        self.controller = controller
        self.window: Any = None

    def bind_window(self, window: Any) -> None:
        self.window = window

    def get_initial_state(self) -> dict[str, Any]:
        return self._call(self.controller.initial_state)

    def get_state(self, since: int = 0) -> dict[str, Any]:
        return self._call(self.controller.state, int(since or 0))

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call(lambda: {"config": self.controller.save_config(payload)})

    def generate_api_key(self) -> dict[str, Any]:
        return self._call(lambda: {"api_key": self.controller.generate_api_key()})

    def test_connection(self) -> dict[str, Any]:
        return self._call(self.controller.test_connection)

    def start_services(self) -> dict[str, Any]:
        return self._call(self.controller.start)

    def stop_services(self) -> dict[str, Any]:
        return self._call(self.controller.stop)

    def set_startup(self, enabled: bool) -> dict[str, Any]:
        return self._call(self.controller.set_startup, bool(enabled))

    def generate_power_bi_code(
        self,
        entity: str,
        select_fields: str,
        public_url: str = "",
    ) -> dict[str, Any]:
        return self._call(
            lambda: {
                "code": self.controller.power_bi_code(entity, select_fields, public_url),
            }
        )

    def browse_ca_bundle(self) -> dict[str, Any]:
        if not self.window:
            return {"ok": False, "error": "Desktop window is not available"}
        try:
            import webview

            dialog_type = getattr(getattr(webview, "FileDialog", object), "OPEN", None)
            if dialog_type is None:
                dialog_type = webview.OPEN_DIALOG
            result = self.window.create_file_dialog(
                dialog_type,
                allow_multiple=False,
                file_types=("Certificates (*.cer;*.crt;*.pem)", "All files (*.*)"),
            )
            selected = str(Path(result[0])) if result else ""
            return {"ok": True, "path": selected}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def minimize_window(self) -> dict[str, Any]:
        if self.window:
            self.window.hide()
        return {"ok": True}

    def exit_application(self) -> dict[str, Any]:
        self.controller.request_exit()
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
