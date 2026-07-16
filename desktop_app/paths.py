from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


APP_NAME = "SAPB1Proxy"


def resource_path(relative: str | Path) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def app_data_dir() -> Path:
    override = os.getenv("SAPB1_PROXY_HOME")
    if override:
        root = Path(override).expanduser()
    elif platform.system() == "Windows":
        root = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    elif platform.system() == "Darwin":
        root = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        root = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME.lower()
    root.mkdir(parents=True, exist_ok=True)
    return root


def executable_command(extra_args: list[str] | None = None) -> list[str]:
    args = list(extra_args or [])
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, str(resource_path("main.py")), *args]
