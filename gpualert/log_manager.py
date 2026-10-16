"""
gpualert.log_manager — The log file guarantee.

Every execution creates log files at ~/.gpualert/logs/<date>_<job_id_short>/,
or under $GPUALERT_LOG_DIR when that is set. Files are created BEFORE any
subprocess starts, so they exist on disk even if the job crashes or the
process is killed. When the usual directory cannot be written (a full or
read-only home is normal on HPC), the launcher falls back to a private
temporary directory rather than failing; see create_fallback_log_dir.

All write_to_log calls are best-effort: they catch every exception so a
logging failure can never mask the underlying job result.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOG_DIR_ENV = "GPUALERT_LOG_DIR"


def log_dir_label() -> str:
    """Where logs are meant to go, for display. Touches nothing on disk."""
    return os.environ.get(LOG_DIR_ENV, "").strip() or "~/.gpualert/logs/"


def get_log_dir() -> Path:
    """Returns the log base directory, creating it if missing.

    $GPUALERT_LOG_DIR wins when set (point it at scratch on a cluster with a
    small home quota). Otherwise ~/.gpualert/logs with permissions 700. A
    directory the user chose is left with whatever permissions it has, since
    it may be deliberately shared with a group.
    """
    override = os.environ.get(LOG_DIR_ENV, "").strip()
    if override:
        d = Path(override).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d
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


def create_fallback_log_dir(job_id: str) -> Path:
    """A private per-job log directory under the system temp dir.

    mkdtemp creates it with mode 700 and an unpredictable name, so another
    user on a shared /tmp can neither guess it in advance nor read it.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir = Path(tempfile.mkdtemp(prefix=f"gpualert-{stamp}_{(job_id or 'no-id')[:8]}-"))
    for fname in ("stdout.log", "stderr.log", "combined.log"):
        with open(job_dir / fname, "a"):
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


# get_tail reads backwards in blocks of this size and gives up after
# _TAIL_MAX_BYTES, so its memory cost does not depend on the file size.
_TAIL_BLOCK = 64 * 1024
_TAIL_MAX_BYTES = 2 * 1024 * 1024


def _split_lines(text: str) -> List[str]:
    # The same lines readlines() gives in text mode: \r\n and bare \r both
    # end a line and come back as \n. str.splitlines would also split on
    # form feeds and Unicode separators, which readlines never did.
    parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def get_tail(log_path: str, n_lines: int = 50, max_bytes: int = _TAIL_MAX_BYTES) -> str:
    """Return the last n_lines of log_path. Returns '' on any error.

    Reads from the end of the file in blocks rather than loading it, so a
    multi-GB log costs at most max_bytes of memory (D6). If the last line
    alone is longer than max_bytes, its final max_bytes are returned behind
    a "[...]" marker.
    """
    try:
        if n_lines <= 0 or not log_path or not os.path.isfile(log_path):
            return ""
        blocks: List[bytes] = []
        breaks = 0
        with open(log_path, "rb") as f:
            pos = f.seek(0, os.SEEK_END)
            budget = max_bytes
            # Stop once the blocks hold n_lines + 1 line breaks: the extra one
            # marks where the first wanted line starts.
            while pos > 0 and budget > 0 and breaks <= n_lines:
                step = min(_TAIL_BLOCK, pos, budget)
                pos -= step
                f.seek(pos)
                block = f.read(step)
                breaks += block.count(b"\n") + block.count(b"\r") - block.count(b"\r\n")
                if blocks and block.endswith(b"\r") and blocks[-1].startswith(b"\n"):
                    breaks -= 1  # a \r\n pair split across two blocks
                blocks.append(block)
                budget -= len(block)
        data = b"".join(reversed(blocks))
        lines = _split_lines(data.decode("utf-8", errors="replace"))
        at_start = pos == 0
        if not at_start and len(lines) > 1:
            lines = lines[1:]  # began mid-line
        elif not at_start and lines:
            lines[0] = "[...]" + lines[0]
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
