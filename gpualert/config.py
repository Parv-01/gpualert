"""
gpualert.config — User configuration (SMTP, email, artifacts).

Config lives at ~/.gpualert/config.toml with permissions 600.
On first run the file is created with safe defaults. Password is never
printed by safe_repr(); the user is responsible for protecting the file.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import List, Tuple

from pydantic import BaseModel, ConfigDict

log = logging.getLogger("gpualert.config")

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - fallback for 3.10
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w  # noqa: E402


class SMTPConfig(BaseModel):
    server: str = "smtp.gmail.com"
    port: int = 587
    use_tls: bool = True
    username: str = ""
    password: str = ""  # never logged
    model_config = ConfigDict(
        json_schema_extra={"example": {"server": "smtp.gmail.com", "port": 587}}
    )


class EmailConfig(BaseModel):
    from_address: str = ""
    to_addresses: List[str] = []
    subject_prefix: str = "[GPUAlert]"
    notify_on_success: bool = True
    notify_on_failure: bool = True
    attach_logs_on_success: bool = True
    attach_logs_on_failure: bool = True  # ALWAYS true — cannot be disabled


class ArtifactConfig(BaseModel):
    # Master on/off for artifact attachment. When False, no output files
    # are scanned or attached; logs are still attached per
    # email.attach_logs_on_success / attach_logs_on_failure. Default True
    # preserves the 0.1.1 behavior. (Added in 0.1.2.)
    attach_artifacts: bool = True
    patterns: List[str] = [
        "*.csv",
        "*.png",
        "*.jpg",
        "*.txt",
        "*.json",
        "*.log",
        "*.npz",
    ]
    max_single_file_mb: int = 25
    max_total_mb: int = 45
    scan_depth: int = 3


class GPUAlertConfig(BaseModel):
    smtp: SMTPConfig = SMTPConfig()
    email: EmailConfig = EmailConfig()
    artifacts: ArtifactConfig = ArtifactConfig()
    verbose: bool = False
    dry_run: bool = False
    log_dir: str = "~/.gpualert/logs"

    def is_configured(self) -> bool:
        return bool(
            self.smtp.username
            and self.smtp.password
            and self.email.from_address
            and self.email.to_addresses
        )

    def safe_repr(self) -> str:
        """Return config as JSON string with password masked."""
        d = self.model_dump()
        if d.get("smtp", {}).get("password"):
            d["smtp"]["password"] = "***"
        return json.dumps(d, indent=2)


def get_config_path() -> Path:
    """~/.gpualert/config.toml. Creates parent dir if missing."""
    p = Path.home() / ".gpualert" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    return p


def load_config() -> GPUAlertConfig:
    """
    Load config. Creates file with defaults if missing.
    Returns defaults (without crashing) if file is corrupt.
    """
    path = get_config_path()
    if not path.exists():
        cfg = GPUAlertConfig()
        save_config(cfg)
        return cfg
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return GPUAlertConfig(**data)
    except Exception as e:
        log.warning("Could not read config (%s) — falling back to defaults", e)
        return GPUAlertConfig()


def save_config(config: GPUAlertConfig) -> bool:
    """Save config with 600 perms. Returns True on success, never raises."""
    try:
        path = get_config_path()
        data = config.model_dump()
        with open(path, "wb") as f:
            tomli_w.dump(data, f)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return True
    except Exception as e:
        log.error("save_config failed: %s", e)
        return False


def validate_config(config: GPUAlertConfig) -> Tuple[bool, List[str]]:
    """Return (is_valid, [error_messages]). Does not connect to SMTP."""
    errors: List[str] = []
    if not config.smtp.username:
        errors.append("smtp.username is empty")
    if not config.smtp.password:
        errors.append("smtp.password is empty")
    if not (1 <= config.smtp.port <= 65535):
        errors.append(f"smtp.port out of range: {config.smtp.port}")
    if not config.email.from_address:
        errors.append("email.from_address is empty")
    if not config.email.to_addresses:
        errors.append("email.to_addresses is empty")
    for addr in config.email.to_addresses:
        if "@" not in addr or "." not in addr.split("@")[-1]:
            errors.append(f"invalid recipient: {addr}")
    return (len(errors) == 0, errors)
