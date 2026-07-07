"""Tests for the SMTPServerDisconnected retry path (added 0.1.2).

Behavior under test:
- First attempt raises SMTPServerDisconnected, second attempt succeeds
  → send() returns success and calls SMTP() exactly twice.
- Both attempts raise SMTPServerDisconnected → send() returns a NON-raising
  NotificationResult whose message mentions "parallel jobs" and the retry.
- SMTPAuthenticationError is NOT retried — auth failures are terminal, no
  point burning a 3-second sleep on them.
- Generic SMTPException surfaces the exception class name in the message
  (so users can distinguish e.g. SMTPRecipientsRefused from SMTPHeloError).

All tests mock `smtplib.SMTP` and never touch the network. time.sleep is
also patched so the retry doesn't actually sleep in CI.
"""

from __future__ import annotations

import smtplib
import tempfile
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


def _make_result():
    from gpualert.types import JobResult

    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    tf.write(b"log body\n")
    tf.close()
    return JobResult(
        command="python train.py",
        job_id=str(uuid.uuid4()),
        start_time=datetime(2026, 6, 7, 10, 0),
        end_time=datetime(2026, 6, 7, 10, 5),
        duration_seconds=300,
        status="success",
        exit_code=0,
        stdout_log_path=tf.name,
        stderr_log_path=tf.name,
        combined_log_path=tf.name,
    )


def _make_config():
    from gpualert.config import EmailConfig, GPUAlertConfig, SMTPConfig

    cfg = GPUAlertConfig()
    cfg.smtp = SMTPConfig(
        server="smtp.gmail.com",
        port=587,
        username="parv-retry@example.com",
        password="fakepw",
    )
    cfg.email = EmailConfig(
        from_address="parv-retry@example.com",
        to_addresses=["dest@example.com"],
    )
    return cfg


class TestSmtpRetry:
    def test_first_disconnect_then_success(self):
        """1st SMTP() raises SMTPServerDisconnected; 2nd succeeds."""
        from gpualert.notifier.email_notifier import EmailNotifier

        good_server = MagicMock()
        good_ctx = MagicMock()
        good_ctx.__enter__.return_value = good_server
        good_ctx.__exit__.return_value = False

        # First call raises inside the `with` block. Simulate by making
        # __enter__ raise, so the code under test sees SMTPServerDisconnected.
        bad_ctx = MagicMock()
        bad_ctx.__enter__.side_effect = smtplib.SMTPServerDisconnected("Server not connected")

        # smtplib.SMTP(...) returns bad_ctx first, then good_ctx.
        smtp_calls = [bad_ctx, good_ctx]

        with (
            patch("smtplib.SMTP", side_effect=smtp_calls) as mock_smtp,
            patch("gpualert.notifier.email_notifier.time.sleep") as mock_sleep,
        ):
            note = EmailNotifier(_make_config()).send(_make_result(), [])

        assert note.success is True, f"expected success, got: {note.message}"
        assert mock_smtp.call_count == 2, "SMTP() must be called twice (retry)"
        # The 3s pause between attempts must fire exactly once.
        mock_sleep.assert_called_once_with(3)
        good_server.login.assert_called_once()
        good_server.send_message.assert_called_once()

    def test_both_attempts_disconnect_returns_parallel_job_message(self):
        """Both attempts raise → clear parallel-job-aware message, no exception."""
        from gpualert.notifier.email_notifier import EmailNotifier

        def _always_disconnect(*args, **kwargs):
            ctx = MagicMock()
            ctx.__enter__.side_effect = smtplib.SMTPServerDisconnected("Server not connected")
            return ctx

        with (
            patch("smtplib.SMTP", side_effect=_always_disconnect) as mock_smtp,
            patch("gpualert.notifier.email_notifier.time.sleep"),
        ):
            note = EmailNotifier(_make_config()).send(_make_result(), [])

        assert note.success is False
        assert mock_smtp.call_count == 2, "must retry exactly once before giving up"
        msg = note.message.lower()
        assert "disconnect" in msg
        assert "parallel" in msg, f"message should hint at parallel-job cause; got: {note.message}"

    def test_auth_error_is_not_retried(self):
        """SMTPAuthenticationError is terminal — one attempt only."""
        from gpualert.notifier.email_notifier import EmailNotifier

        bad_ctx = MagicMock()
        bad_ctx.__enter__.return_value = bad_ctx
        bad_ctx.__exit__.return_value = False
        bad_ctx.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Username and Password not accepted"
        )

        with (
            patch("smtplib.SMTP", return_value=bad_ctx) as mock_smtp,
            patch("gpualert.notifier.email_notifier.time.sleep") as mock_sleep,
        ):
            note = EmailNotifier(_make_config()).send(_make_result(), [])

        assert note.success is False
        assert "authentication failed" in note.message.lower()
        # Auth errors must NOT trigger the disconnect retry / sleep.
        mock_sleep.assert_not_called()
        assert mock_smtp.call_count == 1

    def test_generic_smtp_exception_includes_type_name(self):
        """Users need to know which SMTPException subclass hit them."""
        from gpualert.notifier.email_notifier import EmailNotifier

        bad_ctx = MagicMock()
        bad_ctx.__enter__.return_value = bad_ctx
        bad_ctx.__exit__.return_value = False
        bad_ctx.send_message.side_effect = smtplib.SMTPRecipientsRefused(
            {"dest@example.com": (550, b"Address rejected")}
        )

        with patch("smtplib.SMTP", return_value=bad_ctx):
            note = EmailNotifier(_make_config()).send(_make_result(), [])

        assert note.success is False
        assert (
            "SMTPRecipientsRefused" in note.message
        ), f"type name should appear in message; got: {note.message}"

    def test_send_never_raises_on_unexpected_error(self):
        """send() contract: NEVER propagates exceptions."""
        from gpualert.notifier.email_notifier import EmailNotifier

        with patch("smtplib.SMTP", side_effect=RuntimeError("weird")):
            note = EmailNotifier(_make_config()).send(_make_result(), [])

        assert note.success is False
        assert "unexpected error" in note.message.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
