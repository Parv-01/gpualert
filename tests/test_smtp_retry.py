"""Tests for the transient-error SMTP retry path (reworked 0.1.5).

Behavior under test:
- First attempt raises SMTPServerDisconnected, second attempt succeeds
  → send() returns success after exactly two SMTP() calls.
- All attempts fail → send() returns a NON-raising NotificationResult after
  _SMTP_ATTEMPTS tries, with a message that explains the shared-cluster /
  parallel-job cause and suggests port 465.
- ConnectionResetError and TimeoutError are retried too (they are OSError
  subclasses, not SMTPExceptions — the old loop missed them).
- SMTPAuthenticationError is NOT retried — auth failures are terminal.
- Generic SMTPException surfaces the exception class name in the message.
- use_ssl = True routes through smtplib.SMTP_SSL, never plain SMTP.

All tests mock smtplib and never touch the network. time.sleep is patched
so backoff doesn't actually sleep in CI.
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
        assert mock_smtp.call_count == 2, "SMTP() must be called twice (one retry)"
        # One backoff pause: base 3s plus up to 2s jitter.
        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert 3.0 <= delay <= 5.0, f"first backoff should be 3s + jitter, got {delay}"
        good_server.login.assert_called_once()
        good_server.send_message.assert_called_once()

    def test_all_attempts_fail_returns_cluster_aware_message(self):
        """Every attempt raises → gives up after _SMTP_ATTEMPTS, clear message."""
        from gpualert.notifier.email_notifier import _SMTP_ATTEMPTS, EmailNotifier

        def _always_disconnect(*args, **kwargs):
            ctx = MagicMock()
            ctx.__enter__.side_effect = smtplib.SMTPServerDisconnected("Server not connected")
            return ctx

        with (
            patch("smtplib.SMTP", side_effect=_always_disconnect) as mock_smtp,
            patch("gpualert.notifier.email_notifier.time.sleep") as mock_sleep,
        ):
            note = EmailNotifier(_make_config()).send(_make_result(), [])

        assert note.success is False
        assert mock_smtp.call_count == _SMTP_ATTEMPTS
        assert mock_sleep.call_count == _SMTP_ATTEMPTS - 1, "no sleep after the final attempt"
        # Backoff must grow between attempts.
        delays = [c[0][0] for c in mock_sleep.call_args_list]
        assert delays == sorted(delays), f"backoff should be non-decreasing: {delays}"
        msg = note.message.lower()
        assert "dropped" in msg
        assert "parallel" in msg, f"message should hint at parallel-job cause; got: {note.message}"
        assert "465" in note.message, "message should suggest the SSL fallback"

    def test_connection_reset_is_retried(self):
        """ConnectionResetError (OSError, not SMTPException) must be retried."""
        from gpualert.notifier.email_notifier import EmailNotifier

        good_server = MagicMock()
        good_ctx = MagicMock()
        good_ctx.__enter__.return_value = good_server
        good_ctx.__exit__.return_value = False

        bad_ctx = MagicMock()
        bad_ctx.__enter__.side_effect = ConnectionResetError("Connection reset by peer")

        with (
            patch("smtplib.SMTP", side_effect=[bad_ctx, good_ctx]) as mock_smtp,
            patch("gpualert.notifier.email_notifier.time.sleep"),
        ):
            note = EmailNotifier(_make_config()).send(_make_result(), [])

        assert note.success is True, f"reset should be retried; got: {note.message}"
        assert mock_smtp.call_count == 2

    def test_timeout_is_retried(self):
        """TimeoutError must be retried, not dumped to the OSError handler."""
        from gpualert.notifier.email_notifier import EmailNotifier

        good_server = MagicMock()
        good_ctx = MagicMock()
        good_ctx.__enter__.return_value = good_server
        good_ctx.__exit__.return_value = False

        bad_ctx = MagicMock()
        bad_ctx.__enter__.side_effect = TimeoutError("timed out")

        with (
            patch("smtplib.SMTP", side_effect=[bad_ctx, good_ctx]) as mock_smtp,
            patch("gpualert.notifier.email_notifier.time.sleep"),
        ):
            note = EmailNotifier(_make_config()).send(_make_result(), [])

        assert note.success is True, f"timeout should be retried; got: {note.message}"
        assert mock_smtp.call_count == 2

    def test_use_ssl_routes_through_smtp_ssl(self):
        """use_ssl = True → SMTP_SSL is used; plain SMTP never touched."""
        from gpualert.notifier.email_notifier import EmailNotifier

        cfg = _make_config()
        cfg.smtp.port = 465
        cfg.smtp.use_ssl = True

        server = MagicMock()
        ctx = MagicMock()
        ctx.__enter__.return_value = server
        ctx.__exit__.return_value = False

        with (
            patch("smtplib.SMTP_SSL", return_value=ctx) as mock_ssl,
            patch("smtplib.SMTP") as mock_plain,
        ):
            note = EmailNotifier(cfg).send(_make_result(), [])

        assert note.success is True, f"expected success, got: {note.message}"
        mock_ssl.assert_called_once()
        mock_plain.assert_not_called()
        server.login.assert_called_once()
        server.send_message.assert_called_once()
        # Implicit SSL never issues STARTTLS.
        server.starttls.assert_not_called()

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
