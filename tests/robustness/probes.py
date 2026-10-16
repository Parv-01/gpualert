"""Observation helpers for the robustness harness.

Everything here answers a question about a process, a file or the host
that an ordinary unit test does not need to ask: is anything still
running that should be dead, did we leak a descriptor, did the log
survive intact.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Sequence

import psutil

# ── Process observation ──────────────────────────────────────────────────


def descendants(pid: int) -> List[psutil.Process]:
    """Every live descendant of pid, or [] if the process is already gone."""
    try:
        return psutil.Process(pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def alive(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def survivors(pids: Sequence[int], settle: float = 1.5) -> List[int]:
    """PIDs still alive after a settle period.

    A short wait matters: signal delivery and reaping are asynchronous, so
    checking immediately after a kill reports processes that were about to
    exit anyway.
    """
    time.sleep(settle)
    return [pid for pid in pids if alive(pid)]


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.1) -> bool:
    """Poll until predicate() is true. Returns False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def read_pid_file(path: Path, timeout: float = 10.0) -> Optional[int]:
    """Wait for a child to publish its pid, then return it."""
    if not wait_for(lambda: path.exists() and path.read_text().strip().isdigit(), timeout):
        return None
    return int(path.read_text().strip())


# ── Resource accounting ──────────────────────────────────────────────────


def open_fd_count() -> int:
    try:
        return psutil.Process().num_fds()
    except (AttributeError, psutil.Error):
        return len(os.listdir("/proc/self/fd"))


def thread_count() -> int:
    return threading.active_count()


def rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


# ── Running the wrapper as its own process ───────────────────────────────

_WRAPPER_TEMPLATE = """
import json, sys
sys.path.insert(0, {tree!r})
from gpualert.launcher import run_job
result = run_job({cmd!r}, **{kwargs!r})
sys.stdout.write("@@RESULT@@" + json.dumps({{
    "status": result.status,
    "exit_code": result.exit_code,
    "combined_log_path": result.combined_log_path,
    "stdout_log_path": result.stdout_log_path,
    "notes": list(result.notes or []),
}}) + "\\n")
sys.stdout.flush()
"""


class Wrapper:
    """A run_job call hosted in its own process, so it can be signalled.

    Signal handling can only be tested from outside: the harness has to be
    able to send SIGTERM to the wrapper itself, which is impossible when
    run_job runs inside the pytest process.
    """

    def __init__(self, cmd: List[str], tree: str, home: Optional[str] = None, **kwargs):
        source = _WRAPPER_TEMPLATE.format(tree=tree, cmd=cmd, kwargs=kwargs)
        env = dict(os.environ)
        if home is not None:
            # Give the wrapper its own ~/.gpualert so a test can inspect the
            # logs of a wrapper that was killed before it could report.
            env["HOME"] = home
        self.home = home
        self.proc = subprocess.Popen(
            [sys.executable, "-c", source],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=env,
        )
        self.stdout = ""
        self.stderr = ""

    @property
    def pid(self) -> int:
        return self.proc.pid

    def signal(self, signum: int) -> None:
        self.proc.send_signal(signum)

    def collect(self, timeout: float = 30.0):
        """Wait for exit and return the JobResult dict, or None if it died first."""
        try:
            self.stdout, self.stderr = self.proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.stdout, self.stderr = self.proc.communicate()
        import json

        for line in self.stdout.splitlines():
            if line.startswith("@@RESULT@@"):
                return json.loads(line[len("@@RESULT@@") :])
        return None

    def kill_tree(self) -> None:
        for child in descendants(self.pid):
            try:
                child.kill()
            except psutil.Error:
                pass
        try:
            self.proc.kill()
        except OSError:
            pass


# ── Log integrity ────────────────────────────────────────────────────────


def log_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def has_footer(path: str) -> bool:
    """True when the wrapper wrote its completion footer to the log."""
    return "=== Job Complete ===" in log_text(path)


def combined_logs_under(home: str) -> List[Path]:
    """Every combined.log written beneath a wrapper's private home."""
    return sorted(Path(home).glob(".gpualert/logs/*/combined.log"))
