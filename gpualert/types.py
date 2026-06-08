"""
gpualert.types — Shared data structures for GPUAlert.

All log paths in JobResult are ALWAYS written to disk before this object
is returned from any function. Callers can rely on log_files() to get
paths to real files on disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class JobResult:
    # Job identity
    command: str
    job_id: str
    # Timing
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    # Outcome
    status: str = "pending"  # pending | success | failed | timeout | interrupted | error
    exit_code: Optional[int] = None
    # Log paths — ALWAYS populated, even on error
    stdout_log_path: str = ""
    stderr_log_path: str = ""
    combined_log_path: str = ""
    # Content (subset for email body — full content is in log files)
    stdout_tail: str = ""
    stderr_tail: str = ""
    # Parsed intelligence
    error_summary: str = ""
    artifacts: list = None  # type: ignore[assignment]
    # Free-form annotations the CLI attaches before notification (e.g.
    # "Artifact attachment disabled by config"). Rendered as a NOTES
    # section in the email body. (Added in 0.1.2.)
    notes: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.artifacts is None:
            self.artifacts = []
        if self.notes is None:
            self.notes = []

    def is_success(self) -> bool:
        return self.status == "success"

    def is_failed(self) -> bool:
        return self.status in ("failed", "timeout", "interrupted", "error")

    def duration_human(self) -> str:
        """Return '2h 15m 3s' style string."""
        secs = int(self.duration_seconds)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        parts: List[str] = []
        if h:
            parts.append(f"{h}h")
        if m:
            parts.append(f"{m}m")
        parts.append(f"{s}s")
        return " ".join(parts) if parts else "0s"

    def log_files(self) -> List[str]:
        """Return list of all log file paths that exist on disk."""
        paths = [self.stdout_log_path, self.stderr_log_path, self.combined_log_path]
        return [p for p in paths if p and os.path.isfile(p)]


@dataclass
class ArtifactFile:
    path: str
    size_bytes: int = 0
    extension: str = ""

    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 2)

    def filename(self) -> str:
        return os.path.basename(self.path)


@dataclass
class NotificationResult:
    success: bool
    notifier_type: str  # email | slack | dry_run
    message: str = ""
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class SlurmJobInfo:
    job_id: int
    state: str = "UNKNOWN"  # RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT
    exit_code: int = 0
    elapsed_seconds: float = 0.0
    job_name: str = ""
    partition: str = ""
    node_list: str = ""
