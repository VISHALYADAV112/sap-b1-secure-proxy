from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import secrets
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .paths import app_data_dir


class ConfigError(ValueError):
    pass


ENTITY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\([^/?#]*\))?$")
DOMAIN_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")
SECRET_FIELDS = ("sap_password", "api_key", "ngrok_authtoken")
INTEGER_FIELD_LABELS = {
    "sap_port": "SAP port",
    "sap_language": "SAP language",
    "local_port": "Local proxy port",
    "request_timeout_seconds": "Request timeout",
    "max_response_mb": "Maximum response size",
}


def _coerce_integer(value: Any, label: str) -> int:
    if value is None or isinstance(value, bool) or (
        isinstance(value, str) and not value.strip()
    ):
        raise ConfigError(f"{label} is required")
    try:
        converted = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigError(f"{label} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ConfigError(f"{label} must be an integer")
    return converted


@dataclass
class AppConfig:
    sap_server: str = ""
    sap_port: int = 50000
    sap_company_db: str = ""
    sap_username: str = ""
    sap_password: str = ""
    sap_language: int = 23
    sap_verify_ssl: bool = True
    sap_ca_bundle: str = ""
    local_port: int = 5000
    api_key: str = ""
    ngrok_authtoken: str = ""
    ngrok_domain: str = ""
    start_tunnel: bool = True
    default_entity: str = "Invoices"
    default_select: str = "DocEntry,DocNum,DocDate,CardCode,CardName,DocTotal"
    request_timeout_seconds: int = 60
    max_response_mb: int = 50
    theme: str = "dark"

    def ensure_api_key(self) -> None:
        if not self.api_key:
            self.api_key = secrets.token_urlsafe(32)

    @property
    def sap_base_url(self) -> str:
        return f"https://{self.sap_server}:{self.sap_port}"

    def validate(self, require_connection: bool = False) -> None:
        self.sap_server = normalize_server(self.sap_server)
        self.ngrok_domain = normalize_domain(self.ngrok_domain)
        self.sap_ca_bundle = self.sap_ca_bundle.strip()
        self.default_entity = self.default_entity.strip()
        self.default_select = self.default_select.strip()

        if not 1 <= int(self.sap_port) <= 65535:
            raise ConfigError("SAP port must be between 1 and 65535")
        if not 1 <= int(self.local_port) <= 65535:
            raise ConfigError("Local proxy port must be between 1 and 65535")
        if not 1 <= int(self.sap_language) <= 99:
            raise ConfigError("SAP language must be between 1 and 99")
        if not 5 <= int(self.request_timeout_seconds) <= 300:
            raise ConfigError("Request timeout must be between 5 and 300 seconds")
        if not 1 <= int(self.max_response_mb) <= 500:
            raise ConfigError("Maximum response size must be between 1 and 500 MB")
        if self.api_key and len(self.api_key) < 32:
            raise ConfigError("API key must contain at least 32 characters")
        if self.default_entity and not ENTITY_PATTERN.fullmatch(self.default_entity):
            raise ConfigError("Default entity contains unsupported characters")
        if self.sap_ca_bundle and not Path(self.sap_ca_bundle).expanduser().is_file():
            raise ConfigError("SAP CA certificate file does not exist")
        if self.theme not in {"dark", "light", "system"}:
            self.theme = "dark"

        if require_connection:
            required = {
                "SAP server": self.sap_server,
                "Company database": self.sap_company_db.strip(),
                "SAP username": self.sap_username.strip(),
                "SAP password": self.sap_password,
                "API key": self.api_key,
            }
            missing = [label for label, value in required.items() if not value]
            if missing:
                raise ConfigError(f"Missing required settings: {', '.join(missing)}")
            if self.start_tunnel and not self.ngrok_authtoken:
                raise ConfigError("ngrok authtoken is required when the tunnel is enabled")

    def to_dict(self, include_secrets: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_secrets:
            for key in SECRET_FIELDS:
                data.pop(key, None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        allowed = {field.name for field in fields(cls)}
        values = {key: value for key, value in data.items() if key in allowed}
        for key, label in INTEGER_FIELD_LABELS.items():
            if key in values:
                values[key] = _coerce_integer(values[key], label)
        for key in ("sap_verify_ssl", "start_tunnel"):
            if key in values:
                values[key] = bool(values[key])
        return cls(**values)


def normalize_server(value: str) -> str:
    server = str(value or "").strip().rstrip("/")
    if not server:
        return ""
    parsed = urlsplit(server if "://" in server else f"//{server}")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError("SAP server must be a hostname or IP address")
    try:
        embedded_port = parsed.port
    except ValueError as exc:
        raise ConfigError("SAP server contains an invalid port") from exc
    if embedded_port:
        raise ConfigError("Enter the SAP port in the separate port field")
    host = parsed.hostname or ""
    if not host:
        raise ConfigError("SAP server is invalid")
    return host


def normalize_domain(value: str) -> str:
    domain = str(value or "").strip().lower().rstrip("/")
    if not domain:
        return ""
    parsed = urlsplit(domain if "://" in domain else f"//{domain}")
    host = parsed.hostname or ""
    try:
        embedded_port = parsed.port
    except ValueError as exc:
        raise ConfigError("ngrok domain contains an invalid port") from exc
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or embedded_port:
        raise ConfigError("ngrok domain must contain only the hostname")
    if not DOMAIN_PATTERN.fullmatch(host):
        raise ConfigError("ngrok domain is invalid")
    return host


class SecretStore(Protocol):
    def load(self) -> dict[str, str]:
        ...

    def save(self, values: dict[str, str]) -> None:
        ...


class JsonSecretStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {str(key): str(value) for key, value in data.items()}

    def save(self, values: dict[str, str]) -> None:
        _atomic_write(self.path, json.dumps(values, indent=2).encode("utf-8"))


class WindowsDPAPISecretStore:
    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def __init__(self, path: Path):
        self.path = path

    def _crypt(self, data: bytes, decrypt: bool) -> bytes:
        source_buffer = ctypes.create_string_buffer(data)
        source = self.DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_char)))
        target = self.DataBlob()
        if decrypt:
            success = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
            )
        else:
            success = ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(source), "SAPB1Proxy", None, None, None, 0, ctypes.byref(target)
            )
        if not success:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(target.pbData)

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        plain = self._crypt(self.path.read_bytes(), decrypt=True)
        data = json.loads(plain.decode("utf-8"))
        return {str(key): str(value) for key, value in data.items()}

    def save(self, values: dict[str, str]) -> None:
        plain = json.dumps(values, separators=(",", ":")).encode("utf-8")
        _atomic_write(self.path, self._crypt(plain, decrypt=False))


def default_secret_store(root: Path) -> SecretStore:
    if platform.system() == "Windows":
        return WindowsDPAPISecretStore(root / "secrets.dat")
    return JsonSecretStore(root / "secrets.json")


class ConfigStore:
    def __init__(self, root: Path | None = None, secret_store: SecretStore | None = None):
        self.root = root or app_data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "config.json"
        self.secret_store = secret_store or default_secret_store(self.root)

    def load(self) -> AppConfig:
        values: dict[str, Any] = {}
        if self.path.exists():
            values.update(json.loads(self.path.read_text(encoding="utf-8")))
        values.update(self.secret_store.load())
        config = AppConfig.from_dict(values)
        config.ensure_api_key()
        config.validate(require_connection=False)
        return config

    def save(self, config: AppConfig) -> None:
        config.ensure_api_key()
        config.validate(require_connection=False)
        public = config.to_dict(include_secrets=False)
        private = {key: getattr(config, key) for key in SECRET_FIELDS}
        _atomic_write(self.path, json.dumps(public, indent=2, sort_keys=True).encode("utf-8"))
        self.secret_store.save(private)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(content)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
