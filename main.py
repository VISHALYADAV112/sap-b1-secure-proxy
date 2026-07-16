#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from desktop_app.api import DesktopApi
from desktop_app.controller import AppController
from desktop_app.paths import resource_path
from desktop_app.tray import TrayIcon

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    filename="/tmp/sap_proxy.log",
    filemode="a",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SAP Business One desktop proxy")
    parser.add_argument("--autostart", action="store_true", help="Start proxy services after launch")
    parser.add_argument("--minimized", action="store_true", help="Start with the desktop window hidden")
    parser.add_argument("--headless", action="store_true", help="Run without the desktop GUI")
    parser.add_argument("--debug", action="store_true", help="Enable PyWebView debug tools")
    return parser.parse_args()


def run_headless(controller: AppController, autostart: bool) -> int:
    stopped = threading.Event()

    def stop_handler(*_: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    if autostart:
        controller.start()
    try:
        while not stopped.wait(0.5):
            pass
    finally:
        controller.shutdown()
    return 0


def run_desktop(controller: AppController, args: argparse.Namespace) -> int:
    try:
        import webview
    except ImportError:
        print("PyWebView is not installed. Install requirements-desktop.txt.", file=sys.stderr)
        return 2

    api = DesktopApi(controller)
    window = webview.create_window(
        "SAP B1 Proxy",
        str(resource_path("web/index.html")),
        js_api=api,
        width=1180,
        height=780,
        min_size=(900, 620),
        background_color="#14181b",
        hidden=args.minimized,
    )
    api.bind_window(window)
    tray = TrayIcon(controller)
    tray_available = tray.start()
    if args.minimized and not tray_available:
        window.show()

    def open_window() -> None:
        window.show()
        try:
            window.restore()
        except Exception:
            pass

    def exit_app() -> None:
        controller.shutdown()
        tray.stop()
        window.destroy()

    controller.set_window_actions(open_window, exit_app)

    def on_closing() -> bool:
        if controller.exit_requested:
            return True
        if tray_available:
            window.hide()
            return False
        controller.shutdown()
        return True

    window.events.closing += on_closing

    if args.autostart:
        def delayed_start() -> None:
            time.sleep(1)
            try:
                controller.start()
            except Exception as exc:
                controller.events.error(str(exc))

        threading.Thread(target=delayed_start, name="auto-start", daemon=True).start()

    try:
        webview.start(debug=args.debug)
    finally:
        if not controller.exit_requested:
            controller.shutdown()
        tray.stop()
    return 0


def main() -> int:
    args = parse_args()
    controller = AppController()
    if args.headless:
        return run_headless(controller, args.autostart)
    return run_desktop(controller, args)


if __name__ == "__main__":
    raise SystemExit(main())
