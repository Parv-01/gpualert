"""gpualert.secrets — Three-tier secret resolver + masking wrapper (0.1.4+).

Resolution priority (read):
  1. GPUALERT_EMAIL_PASSWORD env var       — always wins if set (headless / CI)
  2. OS keyring (Windows Credential Locker / macOS Keychain / Linux Secret
     Service)                              — best when available
  3. Fernet-encrypted file (~/.gpualert/secret.enc, key derived per-machine
     via crypto.get_fernet_key())          — portable HPC fallback

Write path: `store_secret(username, password)` tries keyring first, falls
back to the encrypted file. Returns the backend actually used, which the
caller records in config as `smtp.password_backend`.

`SecretStr` wraps a plaintext value so its repr/str never surfaces the
secret. Only `get_secret_value()` reveals it — always call that at the
SMTP boundary, not before, so tracebacks and log filters can't see it.
"""

from __future__ import annotations

import os
from typing import Optional

from gpualert import crypto

# Author signature.
_PARV_SERVICE = "gpualert"
SERVICE = _PARV_SERVICE
ENV_VAR = "GPUALERT_EMAIL_PASSWORD"
SECRET_FILE = crypto.CONFIG_DIR / "secret.enc"


class SecretStr:
    """A secret value whose repr/str never surface the plaintext.

    Compare with pydantic.SecretStr — same idea, tiny surface: get_secret_value()
    is the only way to read the real value.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "SecretStr('**********')"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "**********"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretStr):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("SecretStr", self._value))


def _keyring_set(username: str, password: str) -> bool:
    try:
        import keyring  # type: ignore

        keyring.set_password(SERVICE, username, password)
        return True
    except Exception:
        # NoKeyringError, backend error, or headless HPC node — silent fallback.
        return False


def _keyring_get(username: str) -> Optional[str]:
    try:
        import keyring  # type: ignore

        return keyring.get_password(SERVICE, username)
    except Exception:
        return None


def _keyring_delete(username: str) -> bool:
    try:
        import keyring  # type: ignore

        keyring.delete_password(SERVICE, username)
        return True
    except Exception:
        return False


def store_secret(username: str, password: str) -> str:
    """Persist `password`. Returns the backend that took it: 'keyring' or 'file'.

    Never returns 'env' — env-var storage is caller-controlled (users export
    GPUALERT_EMAIL_PASSWORD themselves). If the keyring succeeds we also
    purge any stale on-disk secret so read-time can't diverge.
    """
    if not username:
        # No username → can't index into keyring. Fall through to file.
        pass
    elif _keyring_set(username, password):
        purge_file_secret()
        return "keyring"

    token = crypto.encrypt_secret(password)
    crypto._write_private(SECRET_FILE, token)
    return "file"


def load_secret(username: str) -> Optional[SecretStr]:
    """Resolve the secret in env → keyring → file order. Returns None if
    nothing is configured (caller decides how to surface that)."""
    env = os.environ.get(ENV_VAR)
    if env:
        return SecretStr(env)
    if username:
        kr = _keyring_get(username)
        if kr:
            return SecretStr(kr)
    if SECRET_FILE.exists():
        try:
            return SecretStr(crypto.decrypt_secret(SECRET_FILE.read_bytes()))
        except Exception:
            # InvalidToken (copied file, wrong machine) or read error.
            return None
    return None


def purge_file_secret() -> None:
    """Best-effort secure-delete of on-disk secret material.

    Overwrite with random bytes then unlink. On modern journaling/COW
    filesystems and SSDs the overwrite is best-effort, not guaranteed
    (the FS may relocate blocks). The real protection is that the secret
    is never plaintext at rest — this is defense in depth.
    """
    for path in (SECRET_FILE, crypto.KEY_FILE, crypto.SALT_FILE):
        try:
            if path.exists():
                size = max(path.stat().st_size, 16)
                with open(path, "r+b", buffering=0) as fh:
                    fh.write(os.urandom(size))
                    fh.flush()
                    os.fsync(fh.fileno())
                path.unlink()
        except OSError:
            pass


def purge_keyring(username: str) -> None:
    """Remove the OS keyring entry for this user, if present."""
    if username:
        _keyring_delete(username)
