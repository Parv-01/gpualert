"""
gpualert.launcher — Subprocess runner with the log file guarantee.

run_job(cmd) always:
  1. Creates log files BEFORE the process starts (so files exist on crash)
  2. Streams stdout/stderr to disk in real time via background threads
  3. Returns a JobResult with absolute log paths and a status

It never raises. Every error path writes a [SYSTEM] message to the logs
and returns a JobResult with is_failed() == True.
"""

from __future__ import annotations

import os
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from gpualert.capture import LogSink, StreamReader, sink_notes, stream_encoding
from gpualert.log_manager import (
    create_fallback_log_dir,
    create_job_log_dir,
    get_job_log_paths,
    get_tail,
    log_dir_label,
    write_to_log,
)
from gpualert.parse_errors import extract_success_metrics, parse_errors
from gpualert.types import JobResult

# How long to wait for the reader threads after the child exits. They finish
# as soon as the pipes reach EOF, which is immediate unless a grandchild
# inherited them.
_READER_JOIN_S = 5.0


def _format_duration(seconds: float) -> str:
    secs = int(seconds)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    parts: List[str] = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts) if parts else "0s"


def _safe_getcwd() -> str:
    # os.getcwd() raises once the directory has been removed under us, which
    # happens when a scratch dir is cleaned while a shell still sits in it.
    try:
        return os.getcwd()
    except OSError as exc:
        return f"<unavailable: {type(exc).__name__}>"


def _safe_abspath(path: str) -> str:
    try:
        return os.path.abspath(path)
    except OSError:  # relative path while our own cwd is gone
        return path


def _write_header(paths: Tuple[str, str, str], header: str) -> None:
    for path in paths:
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)


def _open_logs(
    job_id: str, command_str: str, header: str
) -> Tuple[Tuple[str, str, str], List[str]]:
    """Create the three log files and write the header, never raising.

    Order: the usual directory, then a private temp directory, then memory
    only. A directory that can be created but not written to (a home over
    quota) counts as a failure, which is why the header write is part of
    the attempt. Each fallback is disclosed in the returned notes.
    """
    try:
        paths = get_job_log_paths(create_job_log_dir(job_id, command_str))
        _write_header(paths, header)
        return paths, []
    except Exception as exc:
        primary = f"{log_dir_label()} ({type(exc).__name__}: {exc})"
    try:
        paths = get_job_log_paths(create_fallback_log_dir(job_id))
        _write_header(paths, header)
        where = os.path.dirname(paths[0])
        return paths, [
            f"Log directory {primary} could not be used; this job's logs are in {where}."
        ]
    except Exception as exc:
        return ("", "", ""), [
            f"Log directory {primary} could not be used, and neither could a temporary "
            f"directory ({type(exc).__name__}: {exc}). Output was kept in memory only; "
            "the tails in this report are all that was saved."
        ]


def run_job(
    cmd: List[str],
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
    env: Optional[Dict[str, str]] = None,
    verbose: bool = False,
) -> JobResult:
    """Run a command and return a JobResult. Log files always exist on disk."""
    job_id = str(uuid.uuid4())
    start_time = datetime.now()
    command_str = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)

    # ── STEP 1: Create log directory and files BEFORE starting process ─────
    launch_cwd = _safe_abspath(cwd) if cwd else _safe_getcwd()
    header = (
        "=== GPUAlert Job Log ===\n"
        f"Job ID  : {job_id}\n"
        f"Command : {command_str}\n"
        f"Started : {start_time.isoformat()}\n"
        f"CWD     : {launch_cwd}\n"
        "========================\n\n"
    )
    (stdout_path, stderr_path, combined_path), log_notes = _open_logs(job_id, command_str, header)

    # ── STEP 2: Build initial JobResult ────────────────────────────────────
    result = JobResult(
        command=command_str,
        job_id=job_id,
        start_time=start_time,
        stdout_log_path=stdout_path,
        stderr_log_path=stderr_path,
        combined_log_path=combined_path,
        status="pending",
        cwd="" if launch_cwd.startswith("<") else launch_cwd,
    )

    lock = threading.Lock()
    proc: Optional[subprocess.Popen] = None
    status = "failed"
    exit_code: Optional[int] = None

    sink = LogSink({"stdout": stdout_path, "stderr": stderr_path, "combined": combined_path}, lock)
    encoding = stream_encoding()
    echo = (lambda text: print(text, end="", flush=True)) if verbose else None
    readers = [
        StreamReader("stdout", "OUT", "stdout", sink, encoding, echo),
        StreamReader("stderr", "ERR", "stderr", sink, encoding, echo),
    ]
    threads: List[threading.Thread] = []

    def join_readers(timeout_s: float) -> None:
        for t in threads:
            t.join(timeout=timeout_s)

    # ── STEP 3: Start the process ──────────────────────────────────────────
    try:
        # Binary pipes, unbuffered: StreamReader decodes, and a raw read
        # returns as soon as any bytes are available instead of waiting to
        # fill a buffer.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            bufsize=0,
        )
        write_to_log(
            combined_path,
            f"[SYSTEM] Process started with PID {proc.pid}\n",
            lock,
        )

        # ── STEP 4: Stream output in real-time ──────────────────────────────
        for reader, pipe in zip(readers, (proc.stdout, proc.stderr)):
            t = threading.Thread(target=reader.run, args=(pipe,), daemon=True)
            t.start()
            threads.append(t)

        # ── STEP 5: Wait for process (with optional timeout) ────────────────
        try:
            proc.wait(timeout=timeout)
            join_readers(_READER_JOIN_S)
            exit_code = proc.returncode
            status = "success" if exit_code == 0 else "failed"
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            join_readers(_READER_JOIN_S)
            exit_code = -1
            status = "timeout"
            write_to_log(
                combined_path,
                f"\n[SYSTEM] Process killed after {timeout}s timeout\n",
                lock,
            )

    except FileNotFoundError:
        status = "failed"
        exit_code = 127
        msg = f"[SYSTEM] ERROR: Command not found: {cmd[0] if cmd else '?'}\n"
        for path in (stdout_path, stderr_path, combined_path):
            write_to_log(path, msg, lock)

    except KeyboardInterrupt:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
            join_readers(2)
        status = "interrupted"
        exit_code = -2
        write_to_log(combined_path, "\n[SYSTEM] Interrupted by user (Ctrl+C)\n", lock)

    except Exception as e:
        status = "failed"
        exit_code = -99
        msg = f"[SYSTEM] Unexpected launcher error: {type(e).__name__}: {e}\n"
        for path in (stdout_path, stderr_path, combined_path):
            write_to_log(path, msg, lock)

    # A pipe still open here means a background process inherited it and
    # outlived the job. Stop logging it: anything after the footer would be
    # misread as part of this job.
    still_open = [r.name for r, t in zip(readers, threads) if t.is_alive()]
    sink.close()

    notes: List[str] = list(log_notes)
    for reader in readers:
        notes.extend(reader.notes())
    notes.extend(
        sink_notes(
            sink, {"stdout": "stdout.log", "stderr": "stderr.log", "combined": "combined.log"}
        )
    )
    if still_open:
        notes.append(
            f"{' and '.join(still_open)} stayed open after the job exited, held by a "
            "background process it started; output after that point is not in the logs."
        )

    # ── STEP 6: Finalize JobResult ─────────────────────────────────────────
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    # With no log file the in-memory ring is the only record left.
    stdout_tail = get_tail(stdout_path, 50) if stdout_path else readers[0].tail(50)
    stderr_tail = get_tail(stderr_path, 50) if stderr_path else readers[1].tail(50)

    error_summary = ""
    if status != "success":
        error_summary = parse_errors(stdout_tail, stderr_tail, exit_code or 0)
    success_metrics = ""
    if status == "success":
        success_metrics = extract_success_metrics(stdout_tail)

    footer_lines = [
        "\n=== Job Complete ===",
        f"Status   : {status.upper()}",
        f"Exit code: {exit_code}",
        f"Duration : {_format_duration(duration)}",
        f"Ended    : {end_time.isoformat()}",
    ]
    if error_summary:
        footer_lines.append(f"Error    : {error_summary.splitlines()[0]}")
    for note in notes:
        footer_lines.append(f"Note     : {note}")
    write_to_log(combined_path, "\n".join(footer_lines) + "\n", lock)

    result.end_time = end_time
    result.duration_seconds = duration
    result.status = status
    result.exit_code = exit_code
    result.stdout_tail = stdout_tail
    result.stderr_tail = stderr_tail
    result.error_summary = error_summary if error_summary else success_metrics
    result.notes.extend(notes)
    return result
