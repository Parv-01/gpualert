"""Tests for gpualert.secrets — Feature 1 resolver (added 0.1.4).

Priority: env → keyring → Fernet file. Plaintext-migration test lives
here too because it exercises the same store_secret/load_secret pair.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from gpualert import crypto, secrets


def _isolate(monkeypatch, tmp_path):
    d = tmp_path / ".gpualert"
    d.mkdir()
    monkeypatch.setattr(crypto, "CONFIG_DIR", d)
    monkeypatch.setattr(crypto, "KEY_FILE", d / "key.bin")
    monkeypatch.setattr(crypto, "SALT_FILE", d / "salt.bin")
    monkeypatch.setattr(secrets, "SECRET_FILE", d / "secret.enc")
    monkeypatch.setattr(crypto, "_raw_machine_id", lambda: "test-machine")
    # env is not set by default
    monkeypatch.delenv("GPUALERT_EMAIL_PASSWORD", raising=False)
    return d


def _install_fake_keyring(monkeypatch, *, get_value=None, set_fails=False):
    """Inject a fake `keyring` module so tests don't touch the real OS."""
    store = {}

    class _FakeKeyring:
        errors = MagicMock(NoKeyringError=Exception)

        @staticmethod
        def set_password(service, user, password):
            if set_fails:
                raise Exception("simulated NoKeyringError")
            store[(service, user)] = password

        @staticmethod
        def get_password(service, user):
            if get_value is not None:
                return get_value
            return store.get((service, user))

        @staticmethod
        def delete_password(service, user):
            store.pop((service, user), None)

    monkeypatch.setitem(sys.modules, "keyring", _FakeKeyring)
    return store


# ── SecretStr masks ───────────────────────────────────────────────────────
class TestSecretStr:
    def test_repr_and_str_mask(self):
        s = secrets.SecretStr("hunter2!")
        assert "hunter2" not in repr(s)
        assert "hunter2" not in str(s)
        assert "*" in repr(s)

    def test_get_secret_value_returns_real(self):
        s = secrets.SecretStr("hunter2!")
        assert s.get_secret_value() == "hunter2!"

    def test_equality(self):
        assert secrets.SecretStr("x") == secrets.SecretStr("x")
        assert secrets.SecretStr("x") != secrets.SecretStr("y")


# ── Priority order ────────────────────────────────────────────────────────
class TestResolutionOrder:
    def test_env_wins_over_everything(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        _install_fake_keyring(monkeypatch, get_value="from-keyring")
        secrets.SECRET_FILE.write_bytes(crypto.encrypt_secret("from-file"))
        monkeypatch.setenv("GPUALERT_EMAIL_PASSWORD", "from-env")

        got = secrets.load_secret("u@x")
        assert got is not None
        assert got.get_secret_value() == "from-env"

    def test_keyring_wins_over_file(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        _install_fake_keyring(monkeypatch, get_value="from-keyring")
        secrets.SECRET_FILE.write_bytes(crypto.encrypt_secret("from-file"))

        got = secrets.load_secret("u@x")
        assert got is not None
        assert got.get_secret_value() == "from-keyring"

    def test_file_used_when_keyring_absent(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        # No keyring module → import fails inside secrets._keyring_get.
        monkeypatch.setitem(sys.modules, "keyring", None)
        secrets.SECRET_FILE.write_bytes(crypto.encrypt_secret("from-file"))

        got = secrets.load_secret("u@x")
        assert got is not None
        assert got.get_secret_value() == "from-file"

    def test_returns_none_when_nothing_set(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        _install_fake_keyring(monkeypatch)  # store is empty
        assert secrets.load_secret("u@x") is None


# ── store_secret decides backend ──────────────────────────────────────────
class TestStoreSecret:
    def test_prefers_keyring_when_available(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        store = _install_fake_keyring(monkeypatch)

        backend = secrets.store_secret("u@x", "pw123")
        assert backend == "keyring"
        assert store[("gpualert", "u@x")] == "pw123"
        assert not secrets.SECRET_FILE.exists(), "file must not be created on keyring path"

    def test_falls_back_to_file_when_keyring_fails(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        _install_fake_keyring(monkeypatch, set_fails=True)

        backend = secrets.store_secret("u@x", "pw123")
        assert backend == "file"
        assert secrets.SECRET_FILE.exists()
        # And it round-trips via load_secret (env not set → keyring returns None
        # because set failed → file path used).
        got = secrets.load_secret("u@x")
        assert got.get_secret_value() == "pw123"

    def test_stored_secret_never_readable_as_plaintext(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        _install_fake_keyring(monkeypatch, set_fails=True)

        secrets.store_secret("u@x", "toxic-secret-123")
        raw = secrets.SECRET_FILE.read_bytes()
        assert b"toxic-secret-123" not in raw


# ── Purge paths ───────────────────────────────────────────────────────────
class TestPurge:
    def test_purge_file_secret_removes_all_material(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        # Seed all three files.
        crypto._write_private(secrets.SECRET_FILE, b"cipher")
        crypto._write_private(crypto.KEY_FILE, b"key")
        crypto._write_private(crypto.SALT_FILE, b"salt")

        secrets.purge_file_secret()
        assert not secrets.SECRET_FILE.exists()
        assert not crypto.KEY_FILE.exists()
        assert not crypto.SALT_FILE.exists()

    def test_purge_keyring_calls_delete(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        store = _install_fake_keyring(monkeypatch)
        store[("gpualert", "u@x")] = "pw"

        secrets.purge_keyring("u@x")
        assert ("gpualert", "u@x") not in store

    def test_purge_idempotent(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        _install_fake_keyring(monkeypatch)
        # Nothing exists — must not raise.
        secrets.purge_file_secret()
        secrets.purge_keyring("u@x")


# ── Silent migration in config.load_config ────────────────────────────────
class TestSilentMigration:
    def test_plaintext_password_moved_to_secret_store(self, tmp_path, monkeypatch, capsys):
        _isolate(monkeypatch, tmp_path)
        _install_fake_keyring(monkeypatch)

        # Force config.py to think ~/.gpualert is tmp_path.
        monkeypatch.setenv("HOME", str(tmp_path))
        from gpualert import config as cfg_mod

        # Seed a legacy plaintext config.toml.
        cfg_dir = tmp_path / ".gpualert"
        cfg_path = cfg_dir / "config.toml"
        cfg_path.write_text(
            '[smtp]\nserver = "smtp.gmail.com"\nport = 587\n'
            'use_tls = true\nusername = "u@example.com"\n'
            'password = "legacy-plaintext-pw"\n'
            "[email]\n"
            'from_address = "u@example.com"\n'
            'to_addresses = ["dest@example.com"]\n'
        )

        cfg = cfg_mod.load_config()

        # In-memory password must be scrubbed and backend recorded.
        assert cfg.smtp.password == ""
        assert cfg.smtp.password_backend in ("keyring", "file")
        # And the secret is retrievable via the resolver.
        got = secrets.load_secret("u@example.com")
        assert got is not None
        assert got.get_secret_value() == "legacy-plaintext-pw"
        # config.toml must no longer contain the plaintext.
        assert "legacy-plaintext-pw" not in cfg_path.read_text()
        # One-line stdout notice fired.
        captured = capsys.readouterr()
        assert "Migrated SMTP password" in captured.out

    def test_migration_idempotent(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        _install_fake_keyring(monkeypatch)
        monkeypatch.setenv("HOME", str(tmp_path))
        from gpualert import config as cfg_mod

        (tmp_path / ".gpualert").mkdir(exist_ok=True)
        cfg_path = tmp_path / ".gpualert" / "config.toml"
        # Already-migrated config (no password on disk).
        cfg_path.write_text(
            '[smtp]\nusername = "u@x"\npassword = ""\npassword_backend = "keyring"\n'
            "[email]\n"
            'from_address = "u@x"\n'
            'to_addresses = ["d@x"]\n'
        )

        cfg = cfg_mod.load_config()
        assert cfg.smtp.password_backend == "keyring"
        # No plaintext to migrate — no stdout notice, no rewrites.
        assert cfg.smtp.password == ""


# ── Never in logs ─────────────────────────────────────────────────────────
class TestSecretConfinement:
    def test_secret_never_appears_in_repr_of_wrapper(self):
        s = secrets.SecretStr("KEEP-ME-OUT-OF-LOGS")
        for r in (repr(s), str(s), f"{s}", f"{s!r}"):
            assert "KEEP-ME-OUT-OF-LOGS" not in r
