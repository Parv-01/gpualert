"""
gpualert.notifier.email_notifier — SMTP email sender + dry-run.

Attaches whatever files are passed in `attachments`. The caller (CLI)
guarantees log files are in that list on failure; the notifier itself
does not re-derive that policy.

send() never raises. Every failure path returns a NotificationResult
with success=False and a human-readable message.
"""

from __future__ import annotations

import os
import random
import smtplib
import ssl
import time
from email.message import EmailMessage
from typing import List

from gpualert.config import GPUAlertConfig
from gpualert.notifier.base import BaseNotifier
from gpualert.types import JobResult, NotificationResult

# Author signature lives inside an internal logger constant.
PARV_INTERNAL_LOGGER_NAME = "gpualert.notifier.parv"

# Transient-failure retry policy. Shared clusters exit through one NAT IP,
# so providers (Gmail especially) drop connections when several jobs notify
# at once. Exponential backoff with jitter keeps parallel jobs from retrying
# in lockstep and colliding again.
_SMTP_ATTEMPTS = 4
_RETRY_BASE_DELAY = 3.0  # seconds; grows 3 → 6 → 12 (+ up to 2s jitter)

# Errors worth retrying: the server or network dropped us mid-session, or
# the connection timed out. DNS failures, refused connections, and auth
# errors are persistent — retrying those just wastes 20 seconds.
_TRANSIENT_SMTP_ERRORS = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    ConnectionResetError,
    TimeoutError,
)


class EmailNotifier(BaseNotifier):
    def __init__(self, config: GPUAlertConfig):
        super().__init__(config)
        self.notifier_type = "email"

    def _deliver(self, msg: EmailMessage, password: str, context: ssl.SSLContext) -> None:
        """Open one SMTP session and send the message.

        Raises on any failure — the caller owns retry and error mapping.
        use_ssl selects implicit TLS (SMTP_SSL, port 465 style); otherwise
        STARTTLS is issued when use_tls is set (port 587 style).
        """
        cfg = self.config
        if cfg.smtp.use_ssl:
            with smtplib.SMTP_SSL(
                cfg.smtp.server, cfg.smtp.port, timeout=30, context=context
            ) as server:
                server.login(cfg.smtp.username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg.smtp.server, cfg.smtp.port, timeout=30) as server:
                if cfg.smtp.use_tls:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                server.login(cfg.smtp.username, password)
                server.send_message(msg)

    def send(
        self,
        result: JobResult,
        attachments: List[str],
    ) -> NotificationResult:
        cfg = self.config

        if not cfg.is_configured():
            return NotificationResult(
                success=False,
                notifier_type=self.notifier_type,
                message="Email not configured. Run: gpualert config --init",
            )

        try:
            subject = self._build_subject(result)
            body = self._build_body(result, attachments)

            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = cfg.email.from_address
            msg["To"] = ", ".join(cfg.email.to_addresses)
            msg.set_content(body)

            # ── Attach files ────────────────────────────────────────────
            attached: List[str] = []
            skipped: List[str] = []

            for filepath in attachments or []:
                if not filepath or not os.path.isfile(filepath):
                    if filepath:
                        skipped.append(filepath)
                    continue
                try:
                    with open(filepath, "rb") as f:
                        data = f.read()
                    msg.add_attachment(
                        data,
                        maintype="application",
                        subtype="octet-stream",
                        filename=os.path.basename(filepath),
                    )
                    attached.append(os.path.basename(filepath))
                except PermissionError:
                    skipped.append(f"{filepath} (permission denied)")
                except OSError as e:
                    skipped.append(f"{filepath} ({e})")

            # ── Resolve the SMTP password at the boundary (0.1.4+) ──────
            # Priority: legacy in-memory value (tests / pre-migration) →
            # secrets.load_secret (env → keyring → encrypted file).
            password = cfg.smtp.password
            if not password:
                try:
                    from gpualert import secrets as gsecrets

                    resolved = gsecrets.load_secret(cfg.smtp.username or "")
                    if resolved is not None:
                        password = resolved.get_secret_value()
                except Exception:
                    password = ""

            # ── Send via SMTP (retry transient drops with backoff) ──────
            context = ssl.create_default_context()
            last_exc: Exception | None = None
            for attempt in range(_SMTP_ATTEMPTS):
                try:
                    self._deliver(msg, password, context)
                    last_exc = None
                    break  # success
                except _TRANSIENT_SMTP_ERRORS as e:
                    last_exc = e
                    if attempt < _SMTP_ATTEMPTS - 1:
                        # 3, 6, 12s + jitter so parallel jobs desynchronize.
                        delay = _RETRY_BASE_DELAY * (2**attempt) + random.uniform(0, 2)
                        time.sleep(delay)

            if last_exc is not None:
                return NotificationResult(
                    success=False,
                    notifier_type=self.notifier_type,
                    message=(
                        f"SMTP connection dropped {_SMTP_ATTEMPTS} times "
                        "(retried with backoff). On shared clusters this usually "
                        "means the provider is rate-limiting the cluster's shared "
                        "IP, or a firewall interferes with port "
                        f"{cfg.smtp.port}. Try port 465 with use_ssl = true in "
                        "config.toml. Parallel jobs notifying simultaneously can "
                        f"also trigger this. Detail: {type(last_exc).__name__}: {last_exc}"
                    ),
                )

            summary = f"Email sent to {cfg.email.to_addresses}. Attached: {attached}"
            if skipped:
                summary += f". Skipped: {skipped}"
            return NotificationResult(
                success=True,
                notifier_type=self.notifier_type,
                message=summary,
            )

        except smtplib.SMTPAuthenticationError:
            return NotificationResult(
                success=False,
                notifier_type=self.notifier_type,
                message=(
                    "SMTP authentication failed. Check username/password. "
                    "For Gmail, use an App Password."
                ),
            )
        except smtplib.SMTPException as e:
            return NotificationResult(
                success=False,
                notifier_type=self.notifier_type,
                message=f"SMTP error: {type(e).__name__}: {e}",
            )
        except ConnectionRefusedError:
            return NotificationResult(
                success=False,
                notifier_type=self.notifier_type,
                message=(
                    f"Connection refused to {cfg.smtp.server}:{cfg.smtp.port}. "
                    "Check server settings."
                ),
            )
        except (OSError, ValueError) as e:
            return NotificationResult(
                success=False,
                notifier_type=self.notifier_type,
                message=f"Network/value error: {type(e).__name__}: {e}",
            )
        except Exception as e:  # last-resort guard; send() must never raise
            return NotificationResult(
                success=False,
                notifier_type=self.notifier_type,
                message=f"Unexpected error: {type(e).__name__}: {e}",
            )


class DryRunNotifier(BaseNotifier):
    """Prints what would be sent. No network calls. Used with --dry-run."""

    def __init__(self, config: GPUAlertConfig):
        super().__init__(config)
        self.notifier_type = "dry_run"

    def send(
        self,
        result: JobResult,
        attachments: List[str],
    ) -> NotificationResult:
        subject = self._build_subject(result)
        body = self._build_body(result, attachments)
        bar = "=" * 60
        print(f"\n{bar}\nDRY RUN — email that would be sent\n{bar}")
        print(f"To     : {self.config.email.to_addresses}")
        print(f"Subject: {subject}")
        print("Body   :")
        print(body)
        attach_names = [os.path.basename(a) for a in attachments if a]
        print(f"Attach : {attach_names}")
        print(f"{bar}\n")
        return NotificationResult(
            success=True,
            notifier_type=self.notifier_type,
            message="Dry run complete",
        )


def get_notifier(config: GPUAlertConfig, dry_run: bool = False) -> BaseNotifier:
    """Return the appropriate notifier given the config and dry-run flag."""
    if dry_run or config.dry_run:
        return DryRunNotifier(config)
    return EmailNotifier(config)
