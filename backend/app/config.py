from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SSHProfile(BaseModel):
    """Local, non-secret connection metadata; passwords are never configuration fields."""

    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    identity_file: str | None = None
    auth: Literal["auto", "prompt", "keychain"] = "auto"
    keychain_service: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTAL_", env_file=PROJECT_ROOT / ".env", extra="ignore"
    )
    database_url: str = "sqlite:///./data/portal.sqlite3"
    ssh_mode: Literal["mock", "real"] = "mock"
    mock_root: Path = Path("./data/mock-ssh")
    mock_home_base: str = "/mock-cluster/home"
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    cookie_secure: bool = False
    session_hours: int = Field(default=12, ge=1, le=168)
    ssh_allowed_targets: list[str] = ["mock-a:22", "mock-b:22", "mock-c:22"]
    ssh_known_hosts: str = ""
    ssh_timeout_seconds: float = Field(default=10, ge=1, le=60)
    mock_fail_aliases: list[str] = []
    ssh_config_file: str = "~/.ssh/config"
    ssh_profiles: dict[str, SSHProfile] = {}
    # Two separate opt-ins: no remote writes or unrestricted terminal on first probe.
    ssh_write_users: list[str] = []
    terminal_real_users: list[str] = []
    terminal_max_per_user: int = Field(default=2, ge=1, le=10)
    terminal_max_per_account: int = Field(default=8, ge=1, le=50)
    terminal_max_total: int = Field(default=32, ge=1, le=100)
    terminal_idle_seconds: int = Field(default=900, ge=1, le=86400)
    terminal_recheck_seconds: float = Field(default=1, ge=0.05, le=5)
    files_max_upload_bytes: int = Field(default=100 * 1024**3, ge=1)
    files_max_entries: int = Field(default=5000, ge=1, le=10000)
    files_max_per_user: int = Field(default=2, ge=1, le=10)
    files_max_per_account: int = Field(default=4, ge=1, le=20)
    files_max_total: int = Field(default=8, ge=1, le=50)
    files_idle_seconds: float = Field(default=60, ge=1, le=300)


@lru_cache
def get_settings() -> Settings:
    return Settings()
