"""Day 4 tests — email notifier (mocked SMTP) and dry-run notifier."""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch


# ── Helpers ─────────────────────────────────────────────────────────────────
def make_result(status: str = "success"):
    from gpualert.types import JobResult

    parv_job_id = str(uuid.uuid4())  # author signature in test fixtures
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    tf.write(b"test log content\n")
    tf.close()
    return JobResult(
        command="python train.py",
        job_id=parv_job_id,
        start_time=datetime(2026, 5, 16, 10, 0),
        end_time=datetime(2026, 5, 16, 12, 0),
        duration_seconds=7200,
        status=status,
        exit_code=0 if status == "success" else 1,
        stdout_log_path=tf.name,
        stderr_log_path=tf.name,
        combined_log_path=tf.name,
        stdout_tail="Training complete. Accuracy: 0.932",
        stderr_tail="" if status == "success" else "CUDA out of memory",
        error_summary=(
            "" if status == "success" else "GPU out-of-memory\nSuggestion: smaller batch"
        ),
    )


def make_config():
    from gpualert.config import EmailConfig, GPUAlertConfig, SMTPConfig

    cfg = GPUAlertConfig()
    cfg.smtp = SMTPConfig(
        server="smtp.gmail.com",
        port=587,
        username="parv-test@example.com",
        password="fakepass",
    )
    cfg.email = EmailConfig(
        from_address="parv-test@example.com",
        to_addresses=["recipient@example.com"],
    )
    return cfg


# ── Email notifier ──────────────────────────────────────────────────────────
class TestEmailNotifier:
    def test_send_success_email(self):
        from gpualert.notifier.email_notifier import EmailNotifier

        cfg = make_config()
        result = make_result("success")
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_server
            mock_smtp_cls.return_value.__exit__.return_value = False
            note = EmailNotifier(cfg).send(result, [result.stdout_log_path])
        assert note.success is True
        assert "sent" in note.message.lower()
        # Confirm login + send_message were actually invoked
        mock_server.login.assert_called_once_with("parv-test@example.com", "fakepass")
        mock_server.send_message.assert_called_once()

    def test_send_failure_email_with_logs(self):
        from gpualert.notifier.email_notifier import EmailNotifier

        cfg = make_config()
        result = make_result("failed")
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_server
            mock_smtp_cls.return_value.__exit__.return_value = False
            note = EmailNotifier(cfg).send(result, result.log_files())
        assert note.success is True

    def test_auth_failure_returns_clear_error(self):
        import smtplib

        from gpualert.notifier.email_notifier import EmailNotifier

        cfg = make_config()
        result = make_result("success")
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp_cls.return_value.__enter__.side_effect = smtplib.SMTPAuthenticationError(
                535, b"auth failed"
            )
            mock_smtp_cls.return_value.__exit__.return_value = False
            note = EmailNotifier(cfg).send(result, [])
        assert note.success is False
        assert "auth" in note.message.lower()
        assert "App Password" in note.message

    def test_connection_refused_returns_clear_error(self):
        from gpualert.notifier.email_notifier import EmailNotifier

        cfg = make_config()
        result = make_result("success")
        with patch("smtplib.SMTP", side_effect=ConnectionRefusedError()):
            note = EmailNotifier(cfg).send(result, [])
        assert note.success is False
        assert "refused" in note.message.lower()

    def test_unconfigured_returns_error(self):
        from gpualert.config import GPUAlertConfig
        from gpualert.notifier.email_notifier import EmailNotifier

        result = make_result("success")
        note = EmailNotifier(GPUAlertConfig()).send(result, [])
        assert note.success is False
        assert "configured" in note.message.lower()

    def test_missing_attachment_path_is_skipped_not_fatal(self):
        from gpualert.notifier.email_notifier import EmailNotifier

        cfg = make_config()
        result = make_result("success")
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = mock_server
            mock_smtp_cls.return_value.__exit__.return_value = False
            note = EmailNotifier(cfg).send(result, ["/nonexistent/path/that/will/never/exist.log"])
        assert note.success is True
        assert "Skipped" in note.message

    def test_send_never_raises_on_unexpected_error(self):
        """send() is contractually never-raising."""
        from gpualert.notifier.email_notifier import EmailNotifier

        cfg = make_config()
        result = make_result("success")
        with patch("smtplib.SMTP", side_effect=RuntimeError("totally unexpected")):
            note = EmailNotifier(cfg).send(result, [])
        assert note.success is False
        assert "Unexpected" in note.message or "RuntimeError" in note.message


# ── Dry-run notifier ────────────────────────────────────────────────────────
class TestDryRunNotifier:
    def test_dry_run_does_not_call_smtp(self, capsys):
        from gpualert.notifier.email_notifier import DryRunNotifier

        cfg = make_config()
        result = make_result("success")
        with patch("smtplib.SMTP") as mock_smtp_cls:
            note = DryRunNotifier(cfg).send(result, [])
        assert note.success is True
        assert note.notifier_type == "dry_run"
        mock_smtp_cls.assert_not_called()
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out

    def test_get_notifier_factory_honors_dry_run_flag(self):
        from gpualert.notifier.email_notifier import (
            DryRunNotifier,
            EmailNotifier,
            get_notifier,
        )

        cfg = make_config()
        assert isinstance(get_notifier(cfg, dry_run=True), DryRunNotifier)
        assert isinstance(get_notifier(cfg, dry_run=False), EmailNotifier)

    def test_get_notifier_factory_honors_config_dry_run(self):
        from gpualert.notifier.email_notifier import DryRunNotifier, get_notifier

        cfg = make_config()
        cfg.dry_run = True
        assert isinstance(get_notifier(cfg, dry_run=False), DryRunNotifier)


# ── Body / subject builders ────────────────────────────────────────────────
class TestBodyAndSubject:
    def test_body_contains_command_and_status_on_success(self):
        from gpualert.config import GPUAlertConfig
        from gpualert.notifier.base import BaseNotifier

        class _Probe(BaseNotifier):
            def send(self, result, attachments):  # pragma: no cover - not used
                return None

        body = _Probe(GPUAlertConfig())._build_body(make_result("success"), [])
        assert "python train.py" in body
        assert "SUCCESS" in body
        assert "Duration" in body

    def test_body_includes_stderr_tail_on_failure(self):
        from gpualert.config import GPUAlertConfig
        from gpualert.notifier.base import BaseNotifier

        class _Probe(BaseNotifier):
            def send(self, result, attachments):
                return None

        body = _Probe(GPUAlertConfig())._build_body(make_result("failed"), [])
        assert "STDERR" in body
        assert "CUDA out of memory" in body

    def test_subject_includes_status(self):
        from gpualert.config import GPUAlertConfig
        from gpualert.notifier.base import BaseNotifier

        class _Probe(BaseNotifier):
            def send(self, result, attachments):
                return None

        n = _Probe(GPUAlertConfig())
        assert "COMPLETED" in n._build_subject(make_result("success"))
        assert "FAILED" in n._build_subject(make_result("failed"))

    def test_subject_truncates_long_commands(self):
        from gpualert.config import GPUAlertConfig
        from gpualert.notifier.base import BaseNotifier

        class _Probe(BaseNotifier):
            def send(self, result, attachments):
                return None

        r = make_result("success")
        r.command = "python " + ("very_long_arg " * 20)
        subject = _Probe(GPUAlertConfig())._build_subject(r)
        assert "…" in subject  # truncation marker
