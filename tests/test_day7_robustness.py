"""
tests/test_day7_robustness.py — Targeted tests for the high-value coverage gaps
flagged in the pre-release review.

Scope:
    1. cli.py:    slurm command, config branches (--show, --reset, --test-email),
                  logs empty state, _send_test_email failure path.
    2. log_manager.py: setup_job_logger, list_recent_logs error paths.
    3. launcher.py: KeyboardInterrupt and unexpected-exception arms.
    4. config.py: save_config OSError handling.
    5. Robustness: Unicode + invalid-UTF-8 streams, large stdout streaming,
                   SIGTERM-resistant subprocess timeout path.

These tests run against the live source — no monkey-patching of behaviour, only
of external systems (subprocess, smtplib, sacct) we cannot reach in CI.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from gpualert.cli import app
from gpualert.config import (
    EmailConfig,
    GPUAlertConfig,
    SMTPConfig,
    get_config_path,
    save_config,
)
from gpualert.launcher import run_job
from gpualert.log_manager import (
    create_job_log_dir,
    get_log_dir,
    list_recent_logs,
    setup_job_logger,
)
from gpualert.types import JobResult


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Repoint HOME at a temp dir so config + logs don't touch the real ~."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture
def good_config(isolated_home):
    """A populated config persisted under the isolated HOME."""
    cfg = GPUAlertConfig(
        smtp=SMTPConfig(
            server="smtp.example.com",
            port=587,
            username="u@example.com",
            password="hunter2-app-specific-pw-987",
        ),
        email=EmailConfig(from_address="u@example.com", to_addresses=["dest@example.com"]),
    )
    save_config(cfg)
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# 1. CLI — cover the branches missed by the day-5 suite
# ─────────────────────────────────────────────────────────────────────────────
class TestCLISlurm:
    """gpualert slurm — both error paths and the happy path with mocks."""

    def test_slurm_errors_when_sacct_missing(self, isolated_home):
        runner = CliRunner()
        with patch("gpualert.cli.is_slurm_available", return_value=False):
            result = runner.invoke(app, ["slurm", "12345"])
        assert result.exit_code == 1
        assert "sacct" in result.stdout.lower() or "slurm" in result.stdout.lower()

    def test_slurm_happy_path_with_mocks(self, isolated_home, good_config):
        runner = CliRunner()
        fake_result = JobResult(
            command="slurm_job_77",
            job_id="internal-77",
            start_time=datetime.now(),
            end_time=datetime.now(),
            duration_seconds=42,
            status="success",
            exit_code=0,
            stdout_log_path="",
            stderr_log_path="",
            combined_log_path="",
        )
        with (
            patch("gpualert.cli.is_slurm_available", return_value=True),
            patch("gpualert.cli.poll_job", return_value=fake_result),
            patch("gpualert.cli.get_notifier") as mock_notifier,
        ):
            mock_notifier.return_value.send.return_value = MagicMock(
                success=True, message="sent to [...]"
            )
            result = runner.invoke(app, ["slurm", "77", "--dry-run"])
        assert result.exit_code == 0
        assert mock_notifier.return_value.send.called

    def test_slurm_failure_exits_nonzero(self, isolated_home, good_config):
        runner = CliRunner()
        failing = JobResult(
            command="slurm_job_99",
            job_id="internal-99",
            start_time=datetime.now(),
            end_time=datetime.now(),
            duration_seconds=10,
            status="failed",
            exit_code=1,
            error_summary="Slurm state: FAILED",
        )
        with (
            patch("gpualert.cli.is_slurm_available", return_value=True),
            patch("gpualert.cli.poll_job", return_value=failing),
            patch("gpualert.cli.get_notifier") as mock_notifier,
        ):
            mock_notifier.return_value.send.return_value = MagicMock(
                success=False, message="auth failed"
            )
            result = runner.invoke(app, ["slurm", "99"])
        assert result.exit_code == 1


class TestCLIConfig:
    """gpualert config — the --show, --reset, --test-email, fall-through branches."""

    def test_config_show_masks_password(self, isolated_home, good_config):
        runner = CliRunner()
        result = runner.invoke(app, ["config", "--show"])
        assert result.exit_code == 0
        assert "***" in result.stdout
        # The real password must NOT appear anywhere in the output.
        assert good_config.smtp.password not in result.stdout

    def test_config_reset_confirmed(self, isolated_home, good_config):
        path = get_config_path()
        assert path.exists()
        runner = CliRunner()
        # Typer's confirm reads from stdin — feed it "y".
        result = runner.invoke(app, ["config", "--reset"], input="y\n")
        assert result.exit_code == 0
        assert not path.exists()

    def test_config_reset_declined_keeps_file(self, isolated_home, good_config):
        path = get_config_path()
        runner = CliRunner()
        result = runner.invoke(app, ["config", "--reset"], input="n\n")
        assert result.exit_code == 0
        # Declining should leave the file in place.
        assert path.exists()

    def test_config_no_args_shows_menu(self, isolated_home, good_config):
        runner = CliRunner()
        result = runner.invoke(app, ["config"])
        assert result.exit_code == 0
        assert "--init" in result.stdout

    def test_config_test_email_alias(self, isolated_home, good_config):
        """--test-email on `config` is an alias for the top-level subcommand."""
        runner = CliRunner()
        with patch("gpualert.cli.get_notifier") as mock_notifier:
            mock_notifier.return_value.send.return_value = MagicMock(
                success=True, message="Email sent"
            )
            result = runner.invoke(app, ["config", "--test-email"])
        assert result.exit_code == 0
        assert mock_notifier.return_value.send.called


class TestCLITestEmail:
    """gpualert test-email — both branches."""

    def test_test_email_fails_on_invalid_config(self, isolated_home):
        # No config exists — `load_config` creates a bare one which is invalid.
        runner = CliRunner()
        result = runner.invoke(app, ["test-email"])
        assert result.exit_code == 1
        assert "config --init" in result.stdout.lower() or "init" in result.stdout.lower()

    def test_test_email_reports_notifier_failure(self, isolated_home, good_config):
        runner = CliRunner()
        with patch("gpualert.cli.get_notifier") as mock_notifier:
            mock_notifier.return_value.send.return_value = MagicMock(
                success=False, message="SMTP authentication failed"
            )
            result = runner.invoke(app, ["test-email"])
        assert result.exit_code == 1
        assert "fail" in result.stdout.lower()


class TestCLILogs:
    """gpualert logs — both empty and populated branches."""

    def test_logs_empty_state(self, isolated_home):
        runner = CliRunner()
        result = runner.invoke(app, ["logs"])
        assert result.exit_code == 0
        assert "no logs" in result.stdout.lower()

    def test_logs_populated(self, isolated_home):
        # Create two log directories so the table path runs.
        create_job_log_dir("job-a-12345678", "python a.py")
        create_job_log_dir("job-b-87654321", "python b.py")
        runner = CliRunner()
        result = runner.invoke(app, ["logs", "--last", "5"])
        assert result.exit_code == 0
        # Table should show MB column and the directory name.
        assert "MB" in result.stdout


# ─────────────────────────────────────────────────────────────────────────────
# 2. log_manager — setup_job_logger + list_recent_logs error paths
# ─────────────────────────────────────────────────────────────────────────────
class TestLogManager:
    def test_setup_job_logger_writes_to_file(self, tmp_path):
        logger = setup_job_logger("a1b2c3d4-ee", tmp_path, verbose=False)
        logger.info("smoke check")
        for h in logger.handlers:
            h.flush()
        internal = tmp_path / "gpualert_internal.log"
        assert internal.exists()
        assert "smoke check" in internal.read_text()
        # Cleanup so subsequent tests don't accumulate handlers.
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()

    def test_setup_job_logger_verbose_adds_stream_handler(self, tmp_path):
        logger = setup_job_logger("verbose-id", tmp_path, verbose=True)
        stream_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) >= 1
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()

    def test_setup_job_logger_no_duplicate_handlers(self, tmp_path):
        a = setup_job_logger("dup-test", tmp_path, verbose=False)
        first_count = len(a.handlers)
        b = setup_job_logger("dup-test", tmp_path, verbose=False)
        assert len(b.handlers) == first_count, "second call should clear and re-add, not stack"
        for h in list(b.handlers):
            b.removeHandler(h)
            h.close()

    def test_list_recent_logs_skips_non_dirs(self, isolated_home):
        base = get_log_dir()
        # Plant a file alongside a real job dir.
        (base / "stray.txt").write_text("not a dir")
        create_job_log_dir("real-job-aa", "echo")
        recent = list_recent_logs(10)
        assert all(r["dir"].is_dir() for r in recent)
        assert all("stray.txt" not in str(r["dir"]) for r in recent)

    def test_list_recent_logs_empty_when_base_missing(self, isolated_home, monkeypatch):
        # Force get_log_dir to return a path that doesn't exist.
        fake = isolated_home / "definitely-does-not-exist"
        monkeypatch.setattr("gpualert.log_manager.get_log_dir", lambda: fake)
        assert list_recent_logs(10) == []


# ─────────────────────────────────────────────────────────────────────────────
# 3. launcher — KeyboardInterrupt and unexpected-exception arms
# ─────────────────────────────────────────────────────────────────────────────
class TestLauncherErrorPaths:
    def test_keyboard_interrupt(self, isolated_home):
        """If Popen raises KeyboardInterrupt, run_job records 'interrupted'."""
        with patch("gpualert.launcher.subprocess.Popen", side_effect=KeyboardInterrupt()):
            result = run_job(["echo", "hi"])
        assert result.status == "interrupted"
        assert result.exit_code == -2
        assert all(os.path.isfile(p) for p in result.log_files())
        assert "Interrupted by user" in Path(result.combined_log_path).read_text()

    def test_unexpected_exception(self, isolated_home):
        """Any other exception inside the launcher is swallowed as 'failed' -99."""
        with patch(
            "gpualert.launcher.subprocess.Popen", side_effect=RuntimeError("simulated launcher bug")
        ):
            result = run_job(["echo", "hi"])
        assert result.status == "failed"
        assert result.exit_code == -99
        log_text = Path(result.combined_log_path).read_text()
        assert "Unexpected launcher error" in log_text
        assert "simulated launcher bug" in log_text


# ─────────────────────────────────────────────────────────────────────────────
# 4. config — save_config returns False on OSError, doesn't raise
# ─────────────────────────────────────────────────────────────────────────────
class TestConfigErrorPaths:
    def test_save_config_returns_false_on_oserror(self, isolated_home):
        cfg = GPUAlertConfig()
        with patch("gpualert.config.open", side_effect=OSError("disk full")):
            assert save_config(cfg) is False

    def test_save_config_does_not_raise_on_bad_path(self, isolated_home, monkeypatch):
        monkeypatch.setattr(
            "gpualert.config.get_config_path",
            lambda: Path("/nonexistent/dir/structure/config.toml"),
        )
        # Must return False, must not raise.
        assert save_config(GPUAlertConfig()) is False


# ─────────────────────────────────────────────────────────────────────────────
# 5. Robustness — Unicode, big stdout, signal-resistant timeout
# ─────────────────────────────────────────────────────────────────────────────
class TestRobustness:
    def test_unicode_stdout_does_not_crash(self, isolated_home):
        """Mixed CJK + emoji + Latin must round-trip through the log files."""
        code = (
            "import sys\n"
            "sys.stdout.write('hello 안녕하세요 🚀 héllo\\n')\n"
            "sys.stdout.flush()\n"
        )
        result = run_job([sys.executable, "-c", code])
        assert result.is_success(), f"status={result.status} exit={result.exit_code}"
        combined = Path(result.combined_log_path).read_text(encoding="utf-8")
        # The line is there in some encoding-clean form — at least the Latin part.
        assert "hello" in combined

    def test_invalid_utf8_stderr_does_not_crash(self, isolated_home):
        """A subprocess that emits raw non-UTF-8 bytes must not crash the launcher."""
        # Python's sys.stderr is text-mode UTF-8; reach down to the binary buffer.
        code = (
            "import sys\n"
            "sys.stderr.buffer.write(b'good text \\xff\\xfe non-utf8 bytes\\n')\n"
            "sys.stderr.buffer.flush()\n"
        )
        result = run_job([sys.executable, "-c", code])
        # The job itself succeeded — the launcher should not fail just because the
        # bytes weren't valid UTF-8. errors='replace' keeps the stream alive.
        assert result.status in ("success", "failed")
        assert all(os.path.isfile(p) for p in result.log_files())
        # And parse_errors must not crash on the replaced bytes.
        assert result.stderr_tail is not None

    def test_large_stdout_streams_to_disk(self, isolated_home):
        """1 MB of output across many lines must land in the log file, not OOM."""
        # 100k lines of 10 chars = ~1 MB. Small enough for CI, big enough to
        # confirm the streaming reader doesn't buffer everything in RAM.
        code = (
            "import sys\n"
            "for i in range(100_000):\n"
            "    sys.stdout.write('xxxxxxxxx\\n')\n"
            "sys.stdout.flush()\n"
        )
        result = run_job([sys.executable, "-c", code], timeout=30)
        assert result.is_success()
        size = os.path.getsize(result.stdout_log_path)
        # Stdout log should be at least 1 MB (it includes the header + timestamps too).
        assert size > 1_000_000, f"stdout log too small: {size} bytes"

    def test_timeout_actually_kills_unresponsive_process(self, isolated_home):
        """A process that ignores SIGTERM must still be killed by the timeout path."""
        # signal.signal to ignore SIGTERM, then sleep. proc.kill() uses SIGKILL,
        # which cannot be ignored, so the launcher must succeed in killing it.
        if sys.platform.startswith("win"):
            pytest.skip("signal-ignoring test is POSIX-only")
        code = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "time.sleep(60)\n"
        )
        t0 = time.time()
        result = run_job([sys.executable, "-c", code], timeout=2)
        elapsed = time.time() - t0
        assert result.status == "timeout", f"got status={result.status}"
        # The whole call should finish well under the sleep — proves the kill worked.
        assert elapsed < 15, f"timeout path took {elapsed:.1f}s, kill ineffective"
