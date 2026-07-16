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
    # Implicit SSL (SMTPS, typically port 465). When True the connection is
    # wrapped in TLS from the first byte via smtplib.SMTP_SSL and use_tls
    # (STARTTLS) is ignored. Useful on HPC clusters whose firewalls interfere
    # with STARTTLS on 587. (Added in 0.1.5.)
    use_ssl: bool = False
    username: str = ""
    # In-memory only. Persisted config.toml never carries a plaintext
    # password after 0.1.4 — the load path migrates any legacy value into
    # the secret store, then this field is left blank on disk. Keeping
    # the field lets test code and older paths still set it directly.
    password: str = ""
    # Records where the live password is stored:
    #   "keyring" — OS keyring (Windows Credential Locker / Keychain / Secret Service)
    #   "file"    — ~/.gpualert/secret.enc (Fernet, machine-bound)
    #   "env"     — GPUALERT_EMAIL_PASSWORD (external, not set by config)
    #   ""        — not yet migrated (0.1.3 or earlier config)
    password_backend: str = ""
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
    # Widened in 0.1.4 to cover the common ML output surface.
    patterns: List[str] = [
        "*.csv",
        "*.tsv",
        "*.xlsx",
        "*.xls",
        "*.parquet",
        "*.json",
        "*.yaml",
        "*.yml",
        "*.toml",
        "*.npy",
        "*.npz",
        "*.pt",
        "*.pth",
        "*.ckpt",
        "*.safetensors",
        "*.h5",
        "*.onnx",
        "*.pkl",
        "*.gguf",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.svg",
        "*.pdf",
        "*.log",
        "*.txt",
        "*.tfevents",
        "events.out.tfevents.*",
    ]
    # Experiment-tracker directories (informational only, not attached).
    tracked_dirs: List[str] = [
        "runs",
        "lightning_logs",
        "wandb",
        "mlruns",
        "outputs",
        "checkpoints",
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
        # A backend (keyring/file/env) OR a legacy in-memory password OR the
        # env var set at runtime is enough. After 0.1.4's silent migration,
        # password lives in the secret store, not on this object.
        has_secret = bool(
            self.smtp.password
            or self.smtp.password_backend
            or os.environ.get("GPUALERT_EMAIL_PASSWORD")
        )
        return bool(
            self.smtp.username
            and has_secret
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

    In 0.1.4+, silently migrate any plaintext SMTP password out of
    config.toml into the OS keyring (or encrypted fallback file), scrub
    the plaintext from disk, and print a one-line stdout notice.
    """
    path = get_config_path()
    if not path.exists():
        cfg = GPUAlertConfig()
        save_config(cfg)
        return cfg
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        cfg = GPUAlertConfig(**data)
    except Exception as e:
        log.warning("Could not read config (%s) — falling back to defaults", e)
        return GPUAlertConfig()

    _migrate_plaintext_password(cfg)
    return cfg


def _migrate_plaintext_password(cfg: GPUAlertConfig) -> None:
    """One-shot silent migration of a plaintext SMTP password into the
    secret store (0.1.4). Idempotent — no-op when nothing to migrate,
    and also skipped when password_backend is already set (indicating
    an intentional configuration by the user or a prior migration).
    """
    # Already migrated / user has explicitly chosen a backend → don't
    # touch the on-disk password, even if the in-memory copy is present.
    if cfg.smtp.password_backend:
        return
    legacy = (cfg.smtp.password or "").strip()
    if not legacy:
        return
    try:
        from gpualert import secrets as gsecrets

        backend = gsecrets.store_secret(cfg.smtp.username or "", legacy)
        cfg.smtp.password = ""
        cfg.smtp.password_backend = backend
        save_config(cfg)
        print(
            f"[gpualert] Migrated SMTP password to encrypted storage "
            f"(backend={backend}). It has been removed from config.toml."
        )
    except Exception as e:  # migration must never crash config load
        log.warning("password migration skipped: %s", e)


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
    _has_password = bool(
        config.smtp.password
        or config.smtp.password_backend
        or os.environ.get("GPUALERT_EMAIL_PASSWORD")
    )
    if not _has_password:
        errors.append("smtp.password is empty (run: gpualert config --init)")
    if not (1 <= config.smtp.port <= 65535):
        errors.append(f"smtp.port out of range: {config.smtp.port}")
    if config.smtp.port == 465 and not config.smtp.use_ssl:
        errors.append(
            "smtp.port is 465 (implicit SSL) but use_ssl is false — "
            "set use_ssl = true, or use port 587 with use_tls"
        )
    if not config.email.from_address:
        errors.append("email.from_address is empty")
    if not config.email.to_addresses:
        errors.append("email.to_addresses is empty")
    for addr in config.email.to_addresses:
        if "@" not in addr or "." not in addr.split("@")[-1]:
            errors.append(f"invalid recipient: {addr}")
    return (len(errors) == 0, errors)
