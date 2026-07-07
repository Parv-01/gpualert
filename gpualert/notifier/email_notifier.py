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


class EmailNotifier(BaseNotifier):
    def __init__(self, config: GPUAlertConfig):
        super().__init__(config)
        self.notifier_type = "email"

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

            # ── Send via SMTP (retry once on dropped connection) ────────
            context = ssl.create_default_context()
            last_exc: Exception | None = None
            for attempt in range(2):
                try:
                    with smtplib.SMTP(cfg.smtp.server, cfg.smtp.port, timeout=30) as server:
                        if cfg.smtp.use_tls:
                            server.ehlo()
                            server.starttls(context=context)
                            server.ehlo()
                        server.login(cfg.smtp.username, password)
                        server.send_message(msg)
                    last_exc = None
                    break  # success
                except smtplib.SMTPServerDisconnected as e:
                    last_exc = e
                    if attempt == 0:
                        time.sleep(3)  # brief pause before retry

            if last_exc is not None:
                return NotificationResult(
                    success=False,
                    notifier_type=self.notifier_type,
                    message=(
                        "SMTP server disconnected before the message was sent "
                        "(retried once). This can happen when parallel jobs notify "
                        "simultaneously — the server dropped one connection. "
                        f"Detail: {last_exc}"
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
