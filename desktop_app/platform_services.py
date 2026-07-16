from __future__ import annotations

import os
import platform
import plistlib
import subprocess
from pathlib import Path

from .events import EventLog
from .paths import APP_NAME, app_data_dir, executable_command


TASK_NAME = "SAPB1Proxy_AutoStart"
LAUNCH_AGENT_ID = "com.optima.sapb1proxy"


class StartupError(RuntimeError):
    pass


class StartupService:
    def __init__(self, events: EventLog):
        self.events = events

    def is_enabled(self) -> bool:
        return False

    def enable(self) -> None:
        raise StartupError(f"Startup integration is not supported on {platform.system()}")

    def disable(self) -> None:
        raise StartupError(f"Startup integration is not supported on {platform.system()}")


class WindowsStartupService(StartupService):
    def is_enabled(self) -> bool:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        return result.returncode == 0

    def enable(self) -> None:
        action = subprocess.list2cmdline(executable_command(["--autostart", "--minimized"]))
        result = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                TASK_NAME,
                "/TR",
                action,
                "/SC",
                "ONLOGON",
                "/DELAY",
                "0000:30",
                "/RL",
                "LIMITED",
                "/IT",
                "/F",
            ],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if result.returncode != 0:
            raise StartupError((result.stderr or result.stdout or "Task Scheduler error").strip())
        self.events.info("Windows startup task enabled")

    def disable(self) -> None:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if result.returncode not in {0, 1}:
            raise StartupError((result.stderr or result.stdout or "Task Scheduler error").strip())
        self.events.info("Windows startup task removed")


class MacStartupService(StartupService):
    def __init__(self, events: EventLog):
        super().__init__(events)
        self.path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_ID}.plist"

    def is_enabled(self) -> bool:
        return self.path.exists()

    def enable(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        logs_dir = app_data_dir() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": LAUNCH_AGENT_ID,
            "ProgramArguments": executable_command(["--autostart", "--minimized"]),
            "RunAtLoad": True,
            "ProcessType": "Interactive",
            "StandardOutPath": str(logs_dir / "startup.log"),
            "StandardErrorPath": str(logs_dir / "startup-error.log"),
        }
        self.path.write_bytes(plistlib.dumps(payload))
        os.chmod(self.path, 0o600)
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(self.path)], capture_output=True, check=False)
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(self.path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise StartupError((result.stderr or result.stdout or "launchctl error").strip())
        self.events.info("macOS login startup enabled")

    def disable(self) -> None:
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(self.path)], capture_output=True, check=False)
        self.path.unlink(missing_ok=True)
        self.events.info("macOS login startup removed")


def get_startup_service(events: EventLog) -> StartupService:
    system = platform.system()
    if system == "Windows":
        return WindowsStartupService(events)
    if system == "Darwin":
        return MacStartupService(events)
    return StartupService(events)
