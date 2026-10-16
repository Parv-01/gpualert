"""
gpualert.artifacts — Detect job output files and prepare email attachments.

Scans the job's working directory for files written during the run. A file
qualifies only if it is a regular file, owned by the user running the
wrapper, inside the run directory (an outward symlink does not count), not
in a hidden directory, and modified between the job's start and end. Then a
total-size budget is enforced for the email, packing the rest into a zip if
needed.

Attachments leave the machine, so these rules are a data boundary. On a
shared node another user's output can sit in the same directory (D4); a
named pipe called metrics.json would hang the metrics reader (D11); and a
job started from $HOME would otherwise scan ~/.gpualert and ~/.ssh (D14).

What the scan cannot do is tell two of the same user's concurrent jobs
apart when they write into one directory: nothing short of tracking which
process created each file can, and that needs the process-tree work planned
for 0.2.5. Give concurrent jobs their own directories until then.

Log files are special: on failure they are ALWAYS attached, regardless of
budget. The budget governs *artifacts* (user output), not logs.
"""

from __future__ import annotations

import fnmatch
import os
import stat
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gpualert.types import ArtifactFile

# Author signature in an internal default.
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

DEFAULT_PATTERNS = _PARV_DEFAULT_PATTERNS

# Directories we never recurse into when scanning for job artifacts.
# Standard tool-output / cache dirs the user probably doesn't want emailed.
_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".eggs",
    }
)


# Seconds of slack on the end of the run window. Coarse filesystem
# timestamps (1 s on ext3, 2 s on FAT, NFS attribute caching) can put a file
# the job wrote in its last moment slightly after the recorded end time.
_MTIME_SLACK_S = 2.0

# Reasons a matching file was left out, as counted in find_artifacts(skipped=...).
SKIP_OTHER_OWNER = "other_owner"
SKIP_NOT_REGULAR = "not_regular"
SKIP_OUTSIDE_RUN_DIR = "outside_run_dir"
SKIP_AFTER_END = "after_end"
SKIP_TOO_LARGE = "too_large"


def _matches_any(filename: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatch(filename, p) for p in patterns)


def _current_uid() -> Optional[int]:
    geteuid = getattr(os, "geteuid", None)
    return geteuid() if geteuid is not None else None


def _count(skipped: Optional[Dict[str, List[str]]], reason: str, name: str) -> None:
    if skipped is not None:
        skipped.setdefault(reason, []).append(name)


def find_artifacts(
    start_time: datetime,
    cwd: str = ".",
    patterns: Optional[List[str]] = None,
    max_single_mb: float = 25.0,
    max_depth: int = 3,
    end_time: Optional[datetime] = None,
    skipped: Optional[Dict[str, List[str]]] = None,
) -> List[ArtifactFile]:
    """Return the files this job plausibly wrote. Never raises.

    end_time bounds the run window from above when given. skipped, when a
    dict is passed, collects the files that matched a pattern but were left
    out, keyed by reason (the SKIP_* constants), as paths relative to cwd,
    so the caller can say what was excluded instead of dropping it silently.
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
    end_ts = end_time.timestamp() + _MTIME_SLACK_S if end_time is not None else None
    max_bytes = int(max_single_mb * 1024 * 1024)
    uid = _current_uid()
    # Hidden files are only considered when the user asked for them by name.
    want_hidden = any(p.startswith(".") for p in patterns)
    found: List[ArtifactFile] = []

    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        # Hidden directories hold tool state and credentials (.gpualert,
        # .ssh, .aws, .git), not training outputs.
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")]
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= max_depth:
            dirnames[:] = []
        for fname in filenames:
            if fname.startswith(".") and not want_hidden:
                continue
            if not _matches_any(fname, patterns):
                continue
            fp = Path(dirpath) / fname
            rel = str(fp.relative_to(root))
            # Never follow a symlink out of the run directory: a link named
            # results.json could otherwise attach any readable file.
            if fp.is_symlink():
                try:
                    fp.resolve().relative_to(root)
                except (ValueError, OSError):
                    _count(skipped, SKIP_OUTSIDE_RUN_DIR, rel)
                    continue
            try:
                st = fp.stat()
            except OSError:
                continue
            if st.st_mtime < start_ts:
                continue
            if not stat.S_ISREG(st.st_mode):
                _count(skipped, SKIP_NOT_REGULAR, rel)
                continue
            if uid is not None and st.st_uid != uid:
                _count(skipped, SKIP_OTHER_OWNER, rel)
                continue
            if end_ts is not None and st.st_mtime > end_ts:
                _count(skipped, SKIP_AFTER_END, rel)
                continue
            if st.st_size > max_bytes:
                _count(skipped, SKIP_TOO_LARGE, rel)
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


def describe_skipped(skipped: Dict[str, List[str]], max_single_mb: float = 25.0) -> List[str]:
    """Report lines for files find_artifacts left out.

    Other users' files are counted, never named: their names are theirs.
    """
    lines: List[str] = []

    def _names(items: List[str]) -> str:
        shown = ", ".join(items[:5])
        return shown + (f" and {len(items) - 5} more" if len(items) > 5 else "")

    if skipped.get(SKIP_TOO_LARGE):
        items = skipped[SKIP_TOO_LARGE]
        lines.append(
            f"Not attached, larger than the {max_single_mb:g} MB per-file limit: {_names(items)}."
        )
    if skipped.get(SKIP_OTHER_OWNER):
        n = len(skipped[SKIP_OTHER_OWNER])
        lines.append(
            f"{n} file(s) matching the artifact patterns were not attached because another "
            "user owns them."
        )
    if skipped.get(SKIP_AFTER_END):
        items = skipped[SKIP_AFTER_END]
        lines.append(f"Not attached, modified after the job ended: {_names(items)}.")
    if skipped.get(SKIP_OUTSIDE_RUN_DIR):
        items = skipped[SKIP_OUTSIDE_RUN_DIR]
        lines.append(f"Not attached, symlinks pointing outside the run directory: {_names(items)}.")
    if skipped.get(SKIP_NOT_REGULAR):
        items = skipped[SKIP_NOT_REGULAR]
        lines.append(f"Not attached, not regular files (pipes, sockets, devices): {_names(items)}.")
    return lines


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
    """Decide which files to attach. Logs always included on failure."""
    to_attach: List[str] = []
    skipped: List[str] = []

    if job_failed or attach_logs:
        for lf in log_files or []:
            if lf and os.path.isfile(lf):
                to_attach.append(lf)

    budget_bytes = int(max_total_mb * 1024 * 1024)
    used_bytes = sum((os.path.getsize(f) if os.path.isfile(f) else 0) for f in to_attach)

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
    """'3 files: metrics.csv (1.2 KB), loss.png (45 KB), ...'."""
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
