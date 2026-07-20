#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import logging
import signal
import sys
import threading
import time

from desktop_app.api import DesktopApi
from desktop_app.controller import AppController
from desktop_app.paths import resource_path
from desktop_app.tray import TrayIcon


def configure_logging() -> None:
    """Keep bootstrap logging console-safe; EventLog owns persistent app logs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def show_renderer_error(message: str) -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.MessageBoxW(0, message, "SAP B1 Proxy", 0x10)
    except Exception:
        pass


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
    controller.events.info("Loading desktop renderer")
    try:
        import webview
    except ImportError:
        controller.events.error("PyWebView is not installed")
        print("PyWebView is not installed. Install requirements-desktop.txt.", file=sys.stderr)
        return 2

    api = DesktopApi(controller)
    controller.events.info("Creating desktop window")
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
    api._bind_window(window)
    tray = TrayIcon(controller)
    tray_available = tray.start()
    controller.events.info(
        "System tray initialized" if tray_available else "Continuing without system tray support"
    )
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

    def on_loaded() -> None:
        controller.events.info("Desktop web interface loaded")

    window.events.loaded += on_loaded

    if args.autostart:
        def delayed_start() -> None:
            time.sleep(1)
            try:
                controller.start()
            except Exception as exc:
                controller.events.error(str(exc))

        threading.Thread(target=delayed_start, name="auto-start", daemon=True).start()

    try:
        renderer = "edgechromium" if sys.platform == "win32" else None
        controller.events.info(
            "Starting Microsoft Edge WebView2 renderer"
            if renderer
            else "Starting native desktop web renderer"
        )
        webview.start(gui=renderer, debug=args.debug, private_mode=True)
    except Exception as exc:
        message = (
            "The desktop renderer could not start. "
            "Install or repair Microsoft Edge WebView2 Runtime, then try again.\n\n"
            f"Details: {exc}"
        )
        controller.events.error(message)
        show_renderer_error(message)
        return 3
    finally:
        if not controller.exit_requested:
            controller.shutdown()
        tray.stop()
    return 0


def main() -> int:
    args = parse_args()
    configure_logging()
    controller = AppController()
    if args.headless:
        return run_headless(controller, args.autostart)
    return run_desktop(controller, args)


if __name__ == "__main__":
    raise SystemExit(main())
