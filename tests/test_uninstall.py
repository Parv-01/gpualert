"""Tests for `gpualert uninstall` / `purge` (Feature 3, added 0.1.4).

Contracts under test:
- Full purge removes ~/.gpualert and every secret file.
- --keep-logs preserves logs/, deletes everything else including secrets.
- Keyring entry is deleted (mocked).
- Idempotent — running when nothing exists does not raise.
- --yes bypasses the prompt.
- `purge` is an alias for `uninstall`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from gpualert.cli import app

runner = CliRunner()


def _seed_gpualert_dir(home: Path) -> Path:
    d = home / ".gpualert"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.toml").write_text("[smtp]\nusername = 'u@example.com'\n")
    (d / "secret.enc").write_bytes(b"fake ciphertext")
    (d / "key.bin").write_bytes(b"fake key")
    (d / "salt.bin").write_bytes(b"fake salt")
    logs = d / "logs"
    logs.mkdir()
    (logs / "20260707_120000_run1").mkdir()
    (logs / "20260707_120000_run1" / "combined.log").write_text("job log\n")
    return d


class TestUninstallCommand:
    def test_full_purge_wipes_tree(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = _seed_gpualert_dir(tmp_path)
        assert d.exists()

        with patch("gpualert.cli.load_config") as mock_load:
            mock_load.return_value.smtp.username = "u@example.com"
            res = runner.invoke(app, ["uninstall", "--yes"])

        assert res.exit_code == 0, res.output
        assert not d.exists(), "config dir must be gone"
        assert "Removed" in res.output or "removed" in res.output.lower()

    def test_keep_logs_preserves_logs_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = _seed_gpualert_dir(tmp_path)
        logs_dir = d / "logs"

        with patch("gpualert.cli.load_config") as mock_load:
            mock_load.return_value.smtp.username = "u@example.com"
            res = runner.invoke(app, ["uninstall", "--yes", "--keep-logs"])

        assert res.exit_code == 0, res.output
        assert logs_dir.exists(), "--keep-logs must preserve the logs directory"
        assert (logs_dir / "20260707_120000_run1" / "combined.log").exists()
        # Secrets and config must still be gone.
        assert not (d / "config.toml").exists()
        assert not (d / "secret.enc").exists()
        assert not (d / "key.bin").exists()

    def test_purge_alias_works(self, tmp_path, monkeypatch):
        """`purge` is an alias for `uninstall` — same behavior."""
        monkeypatch.setenv("HOME", str(tmp_path))
        d = _seed_gpualert_dir(tmp_path)

        with patch("gpualert.cli.load_config") as mock_load:
            mock_load.return_value.smtp.username = "u@example.com"
            res = runner.invoke(app, ["purge", "--yes"])

        assert res.exit_code == 0
        assert not d.exists()

    def test_idempotent_when_nothing_to_delete(self, tmp_path, monkeypatch):
        """Running purge on a fresh machine must not fail."""
        monkeypatch.setenv("HOME", str(tmp_path))
        # No ~/.gpualert dir seeded.

        with patch("gpualert.cli.load_config") as mock_load:
            mock_load.return_value.smtp.username = ""
            res = runner.invoke(app, ["uninstall", "--yes"])

        assert res.exit_code == 0
        assert "no config directory" in res.output.lower() or "purged" in res.output.lower()

    def test_keyring_delete_is_called(self, tmp_path, monkeypatch):
        """The OS keyring entry must be scrubbed for the configured user."""
        monkeypatch.setenv("HOME", str(tmp_path))
        _seed_gpualert_dir(tmp_path)

        with (
            patch("gpualert.cli.load_config") as mock_load,
            patch("gpualert.secrets.purge_keyring") as mock_purge_kr,
            patch("gpualert.secrets.purge_file_secret") as mock_purge_file,
        ):
            mock_load.return_value.smtp.username = "u@example.com"
            res = runner.invoke(app, ["uninstall", "--yes"])

        assert res.exit_code == 0
        mock_purge_kr.assert_called_once_with("u@example.com")
        mock_purge_file.assert_called_once()

    def test_prompt_aborts_without_yes(self, tmp_path, monkeypatch):
        """Without --yes and answering 'n', nothing is deleted."""
        monkeypatch.setenv("HOME", str(tmp_path))
        d = _seed_gpualert_dir(tmp_path)

        with patch("gpualert.cli.load_config") as mock_load:
            mock_load.return_value.smtp.username = "u@example.com"
            res = runner.invoke(app, ["uninstall"], input="n\n")

        # exit 1 because we aborted; tree remains intact.
        assert res.exit_code == 1
        assert d.exists()
        assert (d / "secret.enc").exists()
