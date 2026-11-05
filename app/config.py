"""Application configuration, loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, no overrides of real env vars."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    portal_base_url: str = "https://urja-ops.flockenergy.tech"
    portal_email: str = ""
    portal_password: str = field(default="", repr=False)  # never printed
    portal_timeout_seconds: float = 20.0
    portal_max_retries: int = 3
    portal_retry_backoff_seconds: float = 2.0
    cache_ttl_seconds: int = 300
    service_api_key: str = field(default="", repr=False)
    log_level: str = "INFO"

    @property
    def has_credentials(self) -> bool:
        return bool(self.portal_email and self.portal_password)


@lru_cache
def get_settings() -> Settings:
    _load_dotenv(Path(os.environ.get("ENV_FILE", ".env")))
    return Settings(
        portal_base_url=os.environ.get("URJA_PORTAL_URL", Settings.portal_base_url).rstrip("/"),
        portal_email=os.environ.get("URJA_PORTAL_EMAIL", ""),
        portal_password=os.environ.get("URJA_PORTAL_PASSWORD", ""),
        portal_timeout_seconds=_float("URJA_PORTAL_TIMEOUT_SECONDS", 20.0),
        portal_max_retries=_int("URJA_PORTAL_MAX_RETRIES", 3),
        portal_retry_backoff_seconds=_float("URJA_PORTAL_RETRY_BACKOFF_SECONDS", 2.0),
        cache_ttl_seconds=_int("CACHE_TTL_SECONDS", 300),
        service_api_key=os.environ.get("SERVICE_API_KEY", ""),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
