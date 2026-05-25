"""
gpualert.log_manager — The log file guarantee.

Every execution creates log files at ~/.gpualert/logs/<date>_<job_id_short>/.
Files are created BEFORE any subprocess starts, so they exist on disk even
if the job crashes or the process is killed.

All write_to_log calls are best-effort: they catch every exception so a
logging failure can never mask the underlying job result.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def get_log_dir() -> Path:
    """Returns ~/.gpualert/logs (creates it if missing). Permissions 700."""
    d = Path.home() / ".gpualert" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def create_job_log_dir(job_id: str, command: str) -> Path:
    """
    Create ~/.gpualert/logs/{YYYYMMDD_HHMMSS}_{job_id_short}/
    Creates and opens (then closes) stdout.log, stderr.log, combined.log
    so the files exist immediately.
    """
    base = get_log_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_short = (job_id or "no-id")[:8]
    job_dir = base / f"{stamp}_{job_short}"
    job_dir.mkdir(parents=True, exist_ok=True)

    for fname in ("stdout.log", "stderr.log", "combined.log"):
        fp = job_dir / fname
        # touch — create empty file if missing
        with open(fp, "a"):
            pass
    return job_dir


def get_job_log_paths(log_dir: Path) -> Tuple[str, str, str]:
    """Return absolute string paths for (stdout, stderr, combined) logs."""
    return (
        str((log_dir / "stdout.log").resolve()),
        str((log_dir / "stderr.log").resolve()),
        str((log_dir / "combined.log").resolve()),
    )


def setup_job_logger(job_id: str, log_dir: Path, verbose: bool = False) -> logging.Logger:
    """
    Create a Logger named 'gpualert.job.<job_id[:8]>' that writes to
    log_dir/gpualert_internal.log (and stderr if verbose).
    """
    logger = logging.getLogger(f"gpualert.job.{(job_id or 'noid')[:8]}")
    logger.setLevel(logging.DEBUG)
    # avoid duplicate handlers if called twice
    logger.handlers.clear()

    fh = logging.FileHandler(log_dir / "gpualert_internal.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    if verbose:
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(logging.Formatter("[gpualert] %(message)s"))
        logger.addHandler(sh)
    return logger


def write_to_log(log_path: str, text: str, lock: Optional[threading.Lock] = None) -> None:
    """
    Append text to log_path. Thread-safe if lock is provided.
    Never raises — all exceptions swallowed silently.
    """
    try:
        if lock is not None:
            lock.acquire()
        try:
            with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(text)
        finally:
            if lock is not None:
                lock.release()
    except Exception:
        # best-effort logging
        pass


def get_tail(log_path: str, n_lines: int = 50) -> str:
    """Return last n_lines of log_path. Returns '' on any error."""
    try:
        if not log_path or not os.path.isfile(log_path):
            return ""
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n_lines:])
    except Exception:
        return ""


def list_recent_logs(n: int = 10) -> List[Dict]:
    """
    Return list of {'dir': Path, 'created': datetime, 'size_mb': float}
    sorted newest first, limited to n entries.
    """
    base = get_log_dir()
    if not base.exists():
        return []
    entries: List[Dict] = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        try:
            st = d.stat()
            total_bytes = 0
            for f in d.rglob("*"):
                if f.is_file():
                    total_bytes += f.stat().st_size
            entries.append(
                {
                    "dir": d,
                    "created": datetime.fromtimestamp(st.st_mtime),
                    "size_mb": round(total_bytes / (1024 * 1024), 3),
                }
            )
        except OSError:
            continue
    entries.sort(key=lambda e: e["created"], reverse=True)
    return entries[:n]
