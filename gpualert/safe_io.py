"""
gpualert.safe_io — Open a file for attaching or parsing only if it is safe.

The artifact scan decides which files belong to the job, but the notifier,
the zip packer and the metrics reader open those files again later, by
path. Anyone who can write to a shared run directory can swap a file for a
symlink, or for a named pipe, in between (D15). So every later read goes
through open_verified, which checks the file it actually opened rather
than the path it was given:

  * the final component is not followed if it is a symlink (O_NOFOLLOW);
  * the open does not block, so a FIFO cannot hang the wrapper (O_NONBLOCK);
  * it must be a regular file owned by the user running the wrapper;
  * when allowed roots are given, the opened file's real location must be
    inside one of them. On Linux and macOS that location is read from the
    open descriptor itself, so swapping an intermediate directory for a
    symlink is caught too, not just the last component.
"""

from __future__ import annotations

import os
import stat
import sys
from typing import BinaryIO, Iterable, List, Optional


class UnsafeFileError(OSError):
    """The file was refused. str(exc) says why, in words fit for a report.

    An OSError subclass, so code that already treats "could not open" as a
    skip handles a refusal the same way.
    """


def _fd_realpath(fd: int) -> Optional[str]:
    if sys.platform.startswith("linux"):
        try:
            return os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            return None
    if sys.platform == "darwin":
        try:
            import fcntl

            raw = fcntl.fcntl(fd, getattr(fcntl, "F_GETPATH", 50), b"\0" * 1024)
            return os.fsdecode(raw.split(b"\0", 1)[0])
        except (OSError, ImportError):
            return None
    return None


def _within(path: str, roots: List[str]) -> bool:
    path = os.path.normcase(path)
    for root in roots:
        root = os.path.normcase(root)
        if path == root or path.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def resolve_roots(candidates: Iterable[str]) -> List[str]:
    """Real paths of the non-empty candidates, for open_verified(roots=...)."""
    out: List[str] = []
    for c in candidates:
        if not c:
            continue
        try:
            out.append(os.path.realpath(c))
        except OSError:
            continue
    return out


def open_verified(
    path: str, roots: Optional[List[str]] = None, check_owner: bool = True
) -> BinaryIO:
    """Open path for binary reading after the checks in the module docstring.

    Raises UnsafeFileError when a check fails and OSError when the file
    cannot be opened at all (a symlink as the last component surfaces as
    ELOOP and is reported as UnsafeFileError).
    """
    flags = os.O_RDONLY
    for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC", "O_BINARY"):
        flags |= getattr(os, name, 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if os.path.islink(path):
            raise UnsafeFileError("it is a symlink") from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafeFileError("it is not a regular file")
        geteuid = getattr(os, "geteuid", None)
        if check_owner and geteuid is not None and st.st_uid != geteuid():
            raise UnsafeFileError("another user owns it")
        if roots:
            real = _fd_realpath(fd) or os.path.realpath(path)
            if not _within(real, roots):
                raise UnsafeFileError("it resolves outside the run and log directories")
        if hasattr(os, "set_blocking"):
            os.set_blocking(fd, True)
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_verified(
    path: str,
    max_bytes: int,
    roots: Optional[List[str]] = None,
    check_owner: bool = True,
) -> bytes:
    """open_verified, then read at most max_bytes. Larger files are refused."""
    with open_verified(path, roots=roots, check_owner=check_owner) as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UnsafeFileError(f"it is larger than {max_bytes} bytes")
    return data
