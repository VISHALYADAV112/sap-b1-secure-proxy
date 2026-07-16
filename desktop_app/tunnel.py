from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .config import AppConfig
from .events import EventLog
from .paths import app_data_dir


NGROK_DOWNLOADS = {
    ("Windows", "AMD64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip",
    ("Windows", "ARM64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-arm64.zip",
    ("Darwin", "ARM64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-darwin-arm64.zip",
    ("Darwin", "AMD64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-darwin-amd64.zip",
    ("Linux", "AMD64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.zip",
    ("Linux", "ARM64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.zip",
}


class TunnelError(RuntimeError):
    pass


def platform_key() -> tuple[str, str]:
    system = platform.system()
    machine = platform.machine().lower()
    architecture = "ARM64" if machine in {"arm64", "aarch64"} else "AMD64"
    return system, architecture


class TunnelManager:
    def __init__(self, config: AppConfig, events: EventLog, root: Path | None = None):
        self.config = AppConfig.from_dict(config.to_dict())
        self.events = events
        self.root = root or app_data_dir()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        executable_name = "ngrok.exe" if platform.system() == "Windows" else "ngrok"
        self.managed_binary = self.bin_dir / executable_name
        self.ngrok_config = self.root / "ngrok.yml"
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._public_url = ""
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def public_url(self) -> str:
        with self._lock:
            return self._public_url

    def find_binary(self) -> Path | None:
        if self.managed_binary.is_file():
            return self.managed_binary
        discovered = shutil.which("ngrok")
        return Path(discovered) if discovered else None

    def ensure_binary(self) -> Path:
        existing = self.find_binary()
        if existing:
            self.events.info(f"Using ngrok from {existing}")
            return existing

        key = platform_key()
        download_url = NGROK_DOWNLOADS.get(key)
        if not download_url:
            raise TunnelError(f"Automatic ngrok download is not supported on {key[0]} {key[1]}")

        archive_path = self.root / "ngrok-download.zip"
        self.events.info(f"Downloading ngrok for {key[0]} {key[1]}")
        request = urllib.request.Request(download_url, headers={"User-Agent": "SAPB1Proxy/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                expected = response.headers.get("Content-Length")
                if expected and int(expected) > 100 * 1024 * 1024:
                    raise TunnelError("ngrok download is larger than the allowed limit")
                content = response.read(100 * 1024 * 1024 + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise TunnelError(f"ngrok download failed: {exc.__class__.__name__}") from exc
        if len(content) > 100 * 1024 * 1024:
            raise TunnelError("ngrok download is larger than the allowed limit")
        archive_path.write_bytes(content)

        try:
            with zipfile.ZipFile(archive_path) as archive:
                member = next(
                    (name for name in archive.namelist() if Path(name).name == self.managed_binary.name),
                    None,
                )
                if not member:
                    raise TunnelError("ngrok executable was not found in the downloaded archive")
                with archive.open(member) as source, self.managed_binary.open("wb") as target:
                    shutil.copyfileobj(source, target)
        except (OSError, zipfile.BadZipFile) as exc:
            raise TunnelError("Downloaded ngrok archive is invalid") from exc
        finally:
            archive_path.unlink(missing_ok=True)

        current_mode = self.managed_binary.stat().st_mode
        self.managed_binary.chmod(current_mode | stat.S_IXUSR)
        self.events.info("ngrok installed in the application data directory")
        return self.managed_binary

    def configure(self, binary: Path) -> None:
        command = [
            str(binary),
            "config",
            "add-authtoken",
            self.config.ngrok_authtoken,
            "--config",
            str(self.ngrok_config),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=_creation_flags(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip().splitlines()[-1]
            raise TunnelError(f"ngrok authtoken configuration failed: {detail}")
        if self.ngrok_config.exists():
            os.chmod(self.ngrok_config, 0o600)

    def start(self) -> str:
        if self.running:
            return self.public_url
        binary = self.ensure_binary()
        self.configure(binary)

        command = [
            str(binary),
            "http",
            str(self.config.local_port),
            "--config",
            str(self.ngrok_config),
            "--log",
            "stdout",
            "--log-format",
            "json",
        ]
        if self.config.ngrok_domain:
            command.extend(["--url", self.config.ngrok_domain])

        self.events.info("Starting secure ngrok tunnel")
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=_creation_flags(),
        )
        self._reader_thread = threading.Thread(target=self._read_output, name="ngrok-output", daemon=True)
        self._reader_thread.start()
        public_url = self._wait_for_public_url()
        with self._lock:
            self._public_url = public_url
        self.events.info(f"Public tunnel ready at {public_url}")
        return public_url

    def stop(self) -> None:
        process = self._process
        reader = self._reader_thread
        self._process = None
        self._reader_thread = None
        with self._lock:
            self._public_url = ""
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if reader and reader is not threading.current_thread():
            reader.join(timeout=2)
        self.events.info("ngrok tunnel stopped")

    def _wait_for_public_url(self) -> str:
        deadline = time.monotonic() + 25
        inspector_url = "http://127.0.0.1:4040/api/tunnels"
        while time.monotonic() < deadline:
            process = self._process
            if process is None or process.poll() is not None:
                raise TunnelError("ngrok exited before creating a tunnel")
            try:
                with urllib.request.urlopen(inspector_url, timeout=2) as response:
                    payload = json.load(response)
                urls = [
                    tunnel.get("public_url", "")
                    for tunnel in payload.get("tunnels", [])
                    if tunnel.get("proto") == "https"
                ]
                if urls:
                    return urls[0].rstrip("/")
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(0.5)
        self.stop()
        raise TunnelError("Timed out while waiting for ngrok to publish the tunnel")

    def _read_output(self) -> None:
        process = self._process
        if not process or not process.stdout:
            return
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                level = str(payload.get("lvl", "info")).upper()
                message = str(payload.get("msg", "ngrok event"))
                if level in {"EROR", "ERROR", "WARN", "WARNING"}:
                    self.events.add("ERROR" if level in {"EROR", "ERROR"} else "WARNING", f"ngrok: {message}")
            except ValueError:
                if "error" in line.lower():
                    self.events.error(f"ngrok: {line[:240]}")


def _creation_flags() -> int:
    if platform.system() == "Windows":
        return subprocess.CREATE_NO_WINDOW
    return 0
