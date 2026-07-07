"""Tests for gpualert.crypto — Feature 1 (added 0.1.4).

Fernet round-trip, machine-id derivation, and the "copied from another
machine" InvalidToken guard. Machine ID is monkeypatched throughout so
tests don't depend on the host actually having /etc/machine-id.
"""

from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import InvalidToken

from gpualert import crypto


def _isolate_config_dir(monkeypatch, tmp_path):
    """Point crypto.CONFIG_DIR / KEY_FILE / SALT_FILE inside tmp_path so
    tests never touch ~/.gpualert."""
    d = tmp_path / ".gpualert"
    d.mkdir()
    monkeypatch.setattr(crypto, "CONFIG_DIR", d)
    monkeypatch.setattr(crypto, "KEY_FILE", d / "key.bin")
    monkeypatch.setattr(crypto, "SALT_FILE", d / "salt.bin")
    return d


class TestFernetRoundTrip:
    def test_encrypt_decrypt_roundtrip(self, tmp_path, monkeypatch):
        _isolate_config_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(crypto, "_raw_machine_id", lambda: "test-machine-A")
        token = crypto.encrypt_secret("hunter2!")
        assert token != b"hunter2!"
        assert crypto.decrypt_secret(token) == "hunter2!"

    def test_unicode_secret_roundtrips(self, tmp_path, monkeypatch):
        _isolate_config_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(crypto, "_raw_machine_id", lambda: "test-machine-A")
        token = crypto.encrypt_secret("пароль-🔒-2026")
        assert crypto.decrypt_secret(token) == "пароль-🔒-2026"

    def test_key_is_deterministic_for_same_machine(self, tmp_path, monkeypatch):
        _isolate_config_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(crypto, "_raw_machine_id", lambda: "stable-id")
        k1 = crypto.get_fernet_key()
        k2 = crypto.get_fernet_key()
        assert k1 == k2


class TestCopiedFileRejection:
    def test_copied_from_other_machine_fails_with_helpful_message(self, tmp_path, monkeypatch):
        d = _isolate_config_dir(monkeypatch, tmp_path)

        # Encrypt on machine A.
        monkeypatch.setattr(crypto, "_raw_machine_id", lambda: "machine-A")
        token = crypto.encrypt_secret("secret42")

        # Delete the salt so the derived key on B doesn't coincidentally match.
        (d / "salt.bin").unlink(missing_ok=True)

        # Now "move" to machine B.
        monkeypatch.setattr(crypto, "_raw_machine_id", lambda: "machine-B")
        with pytest.raises(InvalidToken) as exc_info:
            crypto.decrypt_secret(token)

        msg = str(exc_info.value)
        assert "another machine" in msg or "gpualert config --init" in msg


class TestUniversalFallback:
    def test_random_key_used_when_no_machine_id(self, tmp_path, monkeypatch):
        _isolate_config_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(crypto, "_raw_machine_id", lambda: None)
        k1 = crypto.get_fernet_key()
        # Subsequent calls return the SAME key from the persisted file.
        k2 = crypto.get_fernet_key()
        assert k1 == k2
        # Now round-trip actually works with the random key too.
        token = crypto.encrypt_secret("fallback-pw")
        assert crypto.decrypt_secret(token) == "fallback-pw"

    def test_key_file_has_600_perms(self, tmp_path, monkeypatch):
        d = _isolate_config_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(crypto, "_raw_machine_id", lambda: None)
        crypto.get_fernet_key()
        import os
        import stat

        mode = stat.S_IMODE(os.stat(d / "key.bin").st_mode)
        # On Windows os.chmod is a no-op — accept any mode.
        import sys

        if sys.platform != "win32":
            assert mode == 0o600, f"key.bin must be 0o600, got {oct(mode)}"


class TestPBKDF2Iterations:
    def test_uses_600k_iterations(self):
        # OWASP 2023/2025 and Django 4.2 default. Locking this against
        # accidental downgrade to the legacy 100,000 figure.
        assert crypto.PBKDF2_ITERATIONS == 600_000


def test_module_imports_cleanly():
    importlib.reload(crypto)
