"""
gpualert.artifacts — Detect job output files and prepare email attachments.

Scans the working directory for files created or modified after the job
started. Filters by glob pattern and file size, then enforces a total-size
budget for the email — packing the rest into a zip if needed.

Log files are special: on failure they are ALWAYS attached, regardless of
budget. The budget governs *artifacts* (user output), not logs.
"""
from __future__ import annotations

import fnmatch
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from gpualert.types import ArtifactFile

# Default patterns — covers the usual ML/HPC output shapes.
# Naming this `_PARV_DEFAULT_PATTERNS` keeps the author's signature inside
# the source without leaking into the public API.
_PARV_DEFAULT_PATTERNS: List[str] = [
    "*.csv",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.svg",
    "*.txt",
    "*.json",
    "*.log",
    "*.out",
    "*.npz",
    "*.npy",
    "*.h5",
    "*.hdf5",
    "*.pkl",
    "*.pickle",
    "*.pdf",
    "*.zip",
    "results*",
    "output*",
    "metrics*",
]

DEFAULT_PATTERNS = _PARV_DEFAULT_PATTERNS  # public alias


def _matches_any(filename: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatch(filename, p) for p in patterns)


def find_artifacts(
    start_time: datetime,
    cwd: str = ".",
    patterns: Optional[List[str]] = None,
    max_single_mb: float = 25.0,
    max_depth: int = 3,
) -> List[ArtifactFile]:
    """
    Return ArtifactFile entries for files matching `patterns` that were
    modified after `start_time` under `cwd`.

    Never raises. Returns [] on any error.
    """
    if patterns is None:
        patterns = list(_PARV_DEFAULT_PATTERNS)

    try:
        root = Path(cwd).resolve()
    except Exception:
        return []
    if not root.exists() or not root.is_dir():
        return []

    start_ts = start_time.timestamp()
    max_bytes = int(max_single_mb * 1024 * 1024)
    found: List[ArtifactFile] = []

    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= max_depth:
            dirnames[:] = []  # stop descending
        for fname in filenames:
            if not _matches_any(fname, patterns):
                continue
            fp = Path(dirpath) / fname
            try:
                st = fp.stat()
            except OSError:
                continue
            if st.st_mtime < start_ts:
                continue
            if st.st_size > max_bytes:
                continue
            found.append(
                ArtifactFile(
                    path=str(fp.resolve()),
                    size_bytes=st.st_size,
                    extension=fp.suffix.lstrip(".").lower(),
                )
            )

    found.sort(key=lambda a: a.size_bytes)
    return found


def compress_artifacts(artifacts: List[str], output_path: str) -> Optional[str]:
    """Zip the given files. Returns output_path on success, None on failure."""
    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in artifacts:
                if fp and os.path.isfile(fp):
                    zf.write(fp, arcname=os.path.basename(fp))
        return output_path
    except Exception:
        return None


def prepare_attachments(
    artifacts: List[ArtifactFile],
    log_files: List[str],
    job_failed: bool,
    max_total_mb: float = 45.0,
    attach_logs: bool = True,
) -> Tuple[List[str], List[str]]:
    """
    Decide which files attach to the notification.

    - Log files are ALWAYS included when `job_failed` is True. The
      `attach_logs` flag only governs success-case attachment.
    - Artifacts are added in ascending size order until the total-MB budget
      is exhausted. If artifacts alone exceed the budget, everything past
      that point is compressed into a single zip beside the logs.

    Returns (files_to_attach, skipped_files).
    """
    to_attach: List[str] = []
    skipped: List[str] = []

    # Logs first — they're non-negotiable on failure.
    if job_failed or attach_logs:
        for lf in log_files or []:
            if lf and os.path.isfile(lf):
                to_attach.append(lf)

    budget_bytes = int(max_total_mb * 1024 * 1024)
    used_bytes = sum(
        (os.path.getsize(f) if os.path.isfile(f) else 0) for f in to_attach
    )

    overflow: List[str] = []
    for art in artifacts or []:
        if not art.path or not os.path.isfile(art.path):
            skipped.append(art.path)
            continue
        if used_bytes + art.size_bytes <= budget_bytes:
            to_attach.append(art.path)
            used_bytes += art.size_bytes
        else:
            overflow.append(art.path)

    if overflow:
        # Pack overflow next to the first log file, or in cwd as fallback.
        zip_dir = os.path.dirname(log_files[0]) if log_files else os.getcwd()
        zip_path = os.path.join(zip_dir, "artifacts_overflow.zip")
        packed = compress_artifacts(overflow, zip_path)
        if packed and os.path.isfile(packed):
            packed_size = os.path.getsize(packed)
            if used_bytes + packed_size <= budget_bytes:
                to_attach.append(packed)
            else:
                skipped.append(packed)
                skipped.extend(overflow)
        else:
            skipped.extend(overflow)

    return to_attach, skipped


def summarize_artifacts(artifacts: List[ArtifactFile]) -> str:
    """Short string like '3 files: metrics.csv (1.2 KB), loss.png (45 KB), ...'."""
    if not artifacts:
        return "0 files"
    parts: List[str] = []
    for a in artifacts[:5]:
        size_kb = a.size_bytes / 1024
        if size_kb < 1024:
            parts.append(f"{a.filename()} ({size_kb:.1f} KB)")
        else:
            parts.append(f"{a.filename()} ({a.size_mb():.2f} MB)")
    suffix = "" if len(artifacts) <= 5 else f" ... and {len(artifacts) - 5} more"
    return f"{len(artifacts)} files: " + ", ".join(parts) + suffix
