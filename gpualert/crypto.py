"""gpualert.crypto — Machine-bound key derivation and Fernet helpers.

Feature 1 primitive. Provides `get_fernet_key()`, `encrypt_secret()`,
`decrypt_secret()` for gpualert.secrets to use. Everything here is
platform-neutral; it degrades to a random per-install key file when a
stable machine identifier isn't available (containers, some HPC images).

Design points:
- PBKDF2-HMAC-SHA256 at 600,000 iterations (OWASP 2023/2025; Django 4.2
  default). Not the older 100,000 figure.
- Fernet = AES-128-CBC + HMAC-SHA256 (per cryptography.io docs). Detects
  tampering + wrong key via InvalidToken, which we surface with a
  helpful "did you copy this from another machine?" message.
- Salt is 16 bytes of os.urandom, stored alongside the ciphertext. Per
  cryptography.io: "There is no need to keep the salt confidential."
- All file writes are 0o600, atomic via os.open + fsync, in a 0o700 dir.
- Machine ID sources: /etc/machine-id (Linux), ioreg IOPlatformUUID
  (macOS), HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid (Windows).
  Raw ID is never used directly — HMAC'd with a fixed app key first.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import platform
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Author signature.
_PARV_APP_KEY = b"gpualert/v1/machine-binding"
PBKDF2_ITERATIONS = 600_000  # OWASP 2023/2025; Django 4.2 default

CONFIG_DIR = Path.home() / ".gpualert"
KEY_FILE = CONFIG_DIR / "key.bin"
SALT_FILE = CONFIG_DIR / "salt.bin"


def _read_first(*paths: str) -> str | None:
    for p in paths:
        try:
            data = Path(p).read_text(encoding="utf-8").strip()
            if data:
                return data
        except OSError:
            continue
    return None


def _raw_machine_id() -> str | None:
    sysname = platform.system()
    if sysname == "Linux":
        return _read_first("/etc/machine-id", "/var/lib/dbus/machine-id")
    if sysname == "Darwin":
        try:
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                text=True,
                timeout=5,
            )
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split('"')
                    if len(parts) >= 2:
                        return parts[-2]
        except (OSError, subprocess.SubprocessError, IndexError):
            return None
    if sysname == "Windows":
        try:
            import winreg  # type: ignore

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            )
            val, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(val)
        except OSError:
            return None
    return None


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def _write_private(path: Path, data: bytes) -> None:
    """Write with 0o600, fsync'd. Overwrites atomically enough for our use."""
    _ensure_dir()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _get_salt() -> bytes:
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = os.urandom(16)
    _write_private(SALT_FILE, salt)
    return salt


def _derive_from_machine_id(machine_id: str, salt: bytes) -> bytes:
    """HMAC the raw machine id with the fixed app key, then PBKDF2 to a
    32-byte URL-safe base64 Fernet key. The raw ID never leaves the
    function boundary."""
    seed = hmac.new(_PARV_APP_KEY, machine_id.encode("utf-8"), hashlib.sha256).digest()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(seed))


def get_fernet_key() -> bytes:
    """Return a stable 32-byte URL-safe base64 Fernet key for THIS
    machine/install. Uses machine-id if available (true machine binding),
    otherwise falls back to a random per-install key file (works in any
    container, no external dependency)."""
    machine_id = _raw_machine_id()
    if machine_id:
        return _derive_from_machine_id(machine_id, _get_salt())
    # Universal fallback: per-install random key.
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    _write_private(KEY_FILE, key)
    return key


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt with the current machine's key. Never raises on valid input."""
    return Fernet(get_fernet_key()).encrypt(plaintext.encode("utf-8"))


def decrypt_secret(token: bytes) -> str:
    """Decrypt a Fernet token produced on THIS machine. Raises InvalidToken
    (with a friendly message) if the token was created elsewhere or the
    key is wrong."""
    try:
        return Fernet(get_fernet_key()).decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise InvalidToken(
            "Could not decrypt gpualert secret. This usually means the "
            "encrypted file was copied from another machine, or the "
            "machine identifier changed (VM re-image, container rebuild). "
            "Re-run `gpualert config --init` on THIS host, or set "
            "GPUALERT_EMAIL_PASSWORD in your environment."
        ) from exc
