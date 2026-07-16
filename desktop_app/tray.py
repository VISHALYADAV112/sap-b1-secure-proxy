from __future__ import annotations

from typing import Any

from .controller import AppController


class TrayIcon:
    def __init__(self, controller: AppController):
        self.controller = controller
        self.icon: Any = None

    def start(self) -> bool:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            self.controller.events.warning("System tray support is unavailable")
            return False

        image = Image.new("RGBA", (64, 64), (20, 24, 27, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=10, fill=(35, 191, 155, 255))
        draw.rectangle((18, 18, 29, 46), fill=(12, 28, 26, 255))
        draw.rectangle((35, 18, 46, 46), fill=(12, 28, 26, 255))
        draw.rectangle((24, 27, 40, 37), fill=(12, 28, 26, 255))

        self.icon = pystray.Icon(
            "sap-b1-proxy",
            image,
            "SAP B1 Proxy",
            menu=pystray.Menu(
                pystray.MenuItem("Open", lambda: self.controller.open_window(), default=True),
                pystray.MenuItem("Start services", lambda: self._ignore_errors(self.controller.start)),
                pystray.MenuItem("Stop services", lambda: self._ignore_errors(self.controller.stop)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", lambda: self.controller.request_exit()),
            ),
        )
        self.icon.run_detached()
        return True

    def stop(self) -> None:
        if self.icon:
            self.icon.stop()
            self.icon = None

    def _ignore_errors(self, action: Any) -> None:
        try:
            action()
        except Exception as exc:
            self.controller.events.error(str(exc))
