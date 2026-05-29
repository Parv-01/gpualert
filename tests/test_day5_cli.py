"""Day 5 tests — Typer CLI commands."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from gpualert.cli import app

runner = CliRunner()


def _mock_email_ok(result, attachments):
    from gpualert.types import NotificationResult

    return NotificationResult(
        success=True,
        notifier_type="email",
        message="Email sent to test@example.com",
    )


def _mock_email_fail(result, attachments):
    from gpualert.types import NotificationResult

    return NotificationResult(
        success=False,
        notifier_type="email",
        message="SMTP authentication failed",
    )


# ── gpualert version ────────────────────────────────────────────────────────
def test_version_prints_version_string():
    from gpualert import __version__

    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert __version__ in res.output


# ── gpualert run ────────────────────────────────────────────────────────────
class TestRunCommand:
    def test_successful_command_exits_zero(self):
        with patch("gpualert.cli.get_notifier") as nf:
            nf.return_value.send = _mock_email_ok
            res = runner.invoke(app, ["run", "echo", "hello"])
        assert res.exit_code == 0
        assert "SUCCESS" in res.output

    def test_failed_command_exits_nonzero(self):
        # `--` separates gpualert options from the wrapped command's options
        with patch("gpualert.cli.get_notifier") as nf:
            nf.return_value.send = _mock_email_ok
            res = runner.invoke(app, ["run", "--", "false"])
        assert res.exit_code == 1
        assert "FAILED" in res.output

    def test_log_paths_always_shown(self):
        with patch("gpualert.cli.get_notifier") as nf:
            nf.return_value.send = _mock_email_ok
            res = runner.invoke(app, ["run", "echo", "test"])
        # Rich may word-wrap long paths, so check for the header + dir name
        assert "Log files written" in res.output
        assert ".gpualert" in res.output

    def test_dry_run_does_not_call_real_smtp(self):
        res = runner.invoke(app, ["run", "--dry-run", "echo", "hi"])
        assert res.exit_code == 0
        assert "DRY RUN" in res.output

    def test_no_notify_skips_email(self):
        res = runner.invoke(app, ["run", "--no-notify", "echo", "hi"])
        assert res.exit_code == 0
        assert "skipped" in res.output.lower()

    def test_notification_failure_still_shows_logs(self):
        with patch("gpualert.cli.get_notifier") as nf:
            nf.return_value.send = _mock_email_fail
            res = runner.invoke(app, ["run", "echo", "test"])
        assert "Log files written" in res.output
        assert ".gpualert" in res.output
        assert "Notification failed" in res.output


# ── gpualert config ─────────────────────────────────────────────────────────
class TestConfigCommand:
    def test_show_prints_config(self):
        res = runner.invoke(app, ["config", "--show"])
        assert res.exit_code == 0
        assert "smtp" in res.output.lower()

    def test_check_on_unconfigured_exits_one(self):
        from gpualert.config import GPUAlertConfig

        with patch("gpualert.cli.load_config", return_value=GPUAlertConfig()):
            res = runner.invoke(app, ["config", "--check"])
        assert res.exit_code == 1
        assert "problems" in res.output.lower()

    def test_check_on_valid_config_exits_zero(self):
        from gpualert.config import EmailConfig, GPUAlertConfig, SMTPConfig

        cfg = GPUAlertConfig()
        cfg.smtp = SMTPConfig(username="parv@example.com", password="x")
        cfg.email = EmailConfig(
            from_address="parv@example.com",
            to_addresses=["dst@example.com"],
        )
        with patch("gpualert.cli.load_config", return_value=cfg):
            res = runner.invoke(app, ["config", "--check"])
        assert res.exit_code == 0
        assert "valid" in res.output.lower()


# ── gpualert test-email ─────────────────────────────────────────────────────
class TestTestEmailCommand:
    def test_unconfigured_exits_one(self):
        from gpualert.config import GPUAlertConfig

        with patch("gpualert.cli.load_config", return_value=GPUAlertConfig()):
            res = runner.invoke(app, ["test-email"])
        assert res.exit_code == 1

    def test_configured_sends_via_notifier(self):
        from gpualert.config import EmailConfig, GPUAlertConfig, SMTPConfig

        cfg = GPUAlertConfig()
        cfg.smtp = SMTPConfig(username="parv@example.com", password="x")
        cfg.email = EmailConfig(
            from_address="parv@example.com",
            to_addresses=["dst@example.com"],
        )
        with (
            patch("gpualert.cli.load_config", return_value=cfg),
            patch("gpualert.cli.get_notifier") as nf,
        ):
            nf.return_value.send = _mock_email_ok
            res = runner.invoke(app, ["test-email"])
        assert res.exit_code == 0
        assert "sent" in res.output.lower()


# ── gpualert logs ───────────────────────────────────────────────────────────
def test_logs_command_runs_without_error():
    res = runner.invoke(app, ["logs"])
    assert res.exit_code == 0


# ── gpualert slurm (without slurm available) ────────────────────────────────
def test_slurm_without_slurm_prints_clear_error():
    with patch("gpualert.cli.is_slurm_available", return_value=False):
        res = runner.invoke(app, ["slurm", "12345"])
    assert res.exit_code == 1
    assert "sacct" in res.output.lower() or "not found" in res.output.lower()
