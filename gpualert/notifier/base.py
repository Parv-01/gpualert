"""
gpualert.notifier.base — Abstract base for notification backends.

Backends (email, dry-run, future Slack/Discord/etc.) all subclass
BaseNotifier. The send() signature receives `attachments` explicitly so
callers can enforce the log-attachment contract at the call site rather
than relying on each backend to remember it.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import List

from gpualert.config import GPUAlertConfig
from gpualert.types import JobResult, NotificationResult


class BaseNotifier(ABC):
    """Common shell for all notifiers. Subclasses implement send()."""

    def __init__(self, config: GPUAlertConfig):
        self.config = config
        self.notifier_type: str = "base"

    @abstractmethod
    def send(
        self,
        result: JobResult,
        attachments: List[str],
    ) -> NotificationResult:
        """Send a notification and return what happened. Must not raise."""

    # ── Shared subject/body builders ────────────────────────────────────
    def _build_subject(self, result: JobResult) -> str:
        prefix = self.config.email.subject_prefix
        status = "✅ COMPLETED" if result.is_success() else "❌ FAILED"
        cmd = result.command
        cmd_short = cmd if len(cmd) <= 40 else cmd[:40] + "…"
        return f"{prefix} {status}: {cmd_short}"

    def _build_body(self, result: JobResult, attachments: List[str]) -> str:
        sep = "=" * 60
        thin = "─" * 40
        lines: List[str] = [
            sep,
            "GPUAlert Job Report",
            sep,
            "",
            f"Status   : {'SUCCESS' if result.is_success() else 'FAILED'}",
            f"Command  : {result.command}",
            f"Job ID   : {result.job_id}",
            f"Started  : {result.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        if result.end_time:
            lines.append(f"Ended    : {result.end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.extend(
            [
                f"Duration : {result.duration_human()}",
                f"Exit Code: {result.exit_code}",
                "",
            ]
        )

        if result.error_summary and result.is_failed():
            lines += [thin, "ERROR SUMMARY", thin, result.error_summary, ""]
        elif result.error_summary and result.is_success():
            lines += [thin, "METRICS", thin, result.error_summary, ""]

        notes = getattr(result, "notes", None) or []
        if notes:
            lines += [thin, "NOTES", thin]
            for n in notes:
                lines.append(f"  - {n}")
            lines.append("")

        if result.stderr_tail and result.is_failed():
            tail = result.stderr_tail.strip().splitlines()[-15:]
            lines += [thin, "LAST 15 LINES OF STDERR", thin, *tail, ""]

        if attachments:
            lines += [thin, f"ATTACHED FILES ({len(attachments)})", thin]
            for f in attachments:
                size_kb = os.path.getsize(f) / 1024 if os.path.isfile(f) else 0.0
                lines.append(f"  - {os.path.basename(f)} ({size_kb:.1f} KB)")
            lines.append("")

        logs_on_disk = result.log_files()
        if logs_on_disk:
            lines += [thin, "LOG FILES ON DISK", thin]
            for lf in logs_on_disk:
                lines.append(f"  - {os.path.basename(lf)} -> {lf}")
            lines.append("")

        lines += [
            thin,
            "Sent by GPUAlert",
            thin,
        ]
        return "\n".join(lines)
