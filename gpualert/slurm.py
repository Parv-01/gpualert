"""
gpualert.slurm — Poll Slurm jobs to completion via sacct/squeue.

This module monitors an existing Slurm job ID. It does not submit jobs;
submission stays the user's responsibility (sbatch, srun, etc.).

If Slurm tooling is missing the module raises SlurmNotAvailableError so
the CLI can fall back to a clear error message rather than hanging.

As in launcher.py, log files are written before polling begins and
populated throughout, so the JobResult always carries real on-disk paths.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from typing import Callable, Optional

from gpualert.log_manager import (
    create_job_log_dir,
    get_job_log_paths,
    write_to_log,
)
from gpualert.types import JobResult, SlurmJobInfo

TERMINAL_STATES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "NODE_FAIL",
    "PREEMPTED",
    "OUT_OF_MEMORY",
}
SUCCESS_STATES = {"COMPLETED"}
RUNNING_STATES = {"RUNNING", "PENDING", "COMPLETING", "CONFIGURING"}

SLURM_STATUS_MAP = {
    "COMPLETED": "success",
    "FAILED": "failed",
    "CANCELLED": "failed",
    "TIMEOUT": "timeout",
    "NODE_FAIL": "failed",
    "PREEMPTED": "failed",
    "OUT_OF_MEMORY": "failed",
}


class SlurmNotAvailableError(RuntimeError):
    """Raised when sacct/squeue cannot be found on PATH."""


def is_slurm_available() -> bool:
    """True iff `sacct` is invocable on PATH."""
    return shutil.which("sacct") is not None


def _parse_elapsed(elapsed: str) -> float:
    """
    Convert Slurm's elapsed format to seconds.
    Accepts D-HH:MM:SS, HH:MM:SS, or MM:SS.
    Returns 0.0 on parse failure.
    """
    if not elapsed:
        return 0.0
    try:
        days = 0
        rest = elapsed.strip()
        if "-" in rest:
            d, rest = rest.split("-", 1)
            days = int(d)
        parts = [int(p) for p in rest.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, s = parts[-3], parts[-2], parts[-1]
        return float(days * 86400 + h * 3600 + m * 60 + s)
    except (ValueError, IndexError):
        return 0.0


def _parse_exit_code(field: str) -> int:
    """Slurm reports ExitCode as 'EXIT:SIGNAL'. Take EXIT."""
    if not field:
        return 0
    try:
        return int(field.split(":")[0])
    except (ValueError, IndexError):
        return 0


def get_job_info(job_id: int) -> SlurmJobInfo:
    """
    Run sacct for `job_id` and parse the first matching row.
    Returns SlurmJobInfo with state='UNKNOWN' on any failure. Never raises.
    """
    fields = "State,ExitCode,Elapsed,JobName,Partition,NodeList"
    cmd = [
        "sacct",
        "-j",
        str(job_id),
        "-X",
        "--parsable2",
        "--noheader",
        f"--format={fields}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            return SlurmJobInfo(job_id=job_id, state="UNKNOWN")
        first = proc.stdout.strip().splitlines()[0]
        cols = first.split("|")
        # Pad missing trailing columns
        while len(cols) < 6:
            cols.append("")
        state_raw = re.split(r"\s+", cols[0].strip())[0]
        return SlurmJobInfo(
            job_id=job_id,
            state=state_raw or "UNKNOWN",
            exit_code=_parse_exit_code(cols[1]),
            elapsed_seconds=_parse_elapsed(cols[2]),
            job_name=cols[3],
            partition=cols[4],
            node_list=cols[5],
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return SlurmJobInfo(job_id=job_id, state="UNKNOWN")


def poll_job(
    job_id: int,
    interval: int = 10,
    timeout: Optional[int] = None,
    on_update: Optional[Callable[[SlurmJobInfo], None]] = None,
) -> JobResult:
    """
    Poll `job_id` every `interval` seconds until a terminal state is reached.

    Args:
        job_id: Slurm job ID
        interval: seconds between sacct calls (use 0 in tests)
        timeout: max wall-clock seconds to poll; None = poll forever
        on_update: optional callback invoked with each fresh SlurmJobInfo

    Returns a JobResult whose log files are real on-disk files containing
    the polling history.

    Raises SlurmNotAvailableError if sacct is missing.
    """
    if not is_slurm_available():
        raise SlurmNotAvailableError(
            "sacct not found in PATH. Is this a Slurm cluster? "
            "On non-Slurm systems use `gpualert run` instead."
        )

    internal_id = str(uuid.uuid4())
    start_time = datetime.now()
    command_str = f"slurm_job_{job_id}"

    log_dir = create_job_log_dir(internal_id, command_str)
    stdout_path, stderr_path, combined_path = get_job_log_paths(log_dir)

    header = (
        "=== GPUAlert Slurm Monitor ===\n"
        f"Slurm Job ID : {job_id}\n"
        f"Internal ID  : {internal_id}\n"
        f"Started      : {start_time.isoformat()}\n"
        f"Poll interval: {interval}s\n"
        "==============================\n\n"
    )
    for p in (stdout_path, stderr_path, combined_path):
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(header)
        except OSError:
            pass

    elapsed = 0
    last_state = "UNKNOWN"
    last_info: Optional[SlurmJobInfo] = None

    while True:
        info = get_job_info(job_id)
        last_info = info
        last_state = info.state

        stamp = datetime.now().strftime("%H:%M:%S")
        write_to_log(
            combined_path,
            f"[{stamp}] state={info.state} elapsed={info.elapsed_seconds:.0f}s "
            f"exit={info.exit_code} node={info.node_list}\n",
        )

        if on_update is not None:
            try:
                on_update(info)
            except Exception:
                pass

        if info.state in TERMINAL_STATES:
            break
        if timeout is not None and elapsed >= timeout:
            write_to_log(combined_path, "[SYSTEM] Poll timeout reached\n")
            last_state = "POLL_TIMEOUT"
            break
        if interval > 0:
            time.sleep(interval)
        elapsed += interval

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    status = SLURM_STATUS_MAP.get(last_state, "failed")
    exit_code = last_info.exit_code if last_info else 0

    footer = (
        "\n=== Slurm Job Complete ===\n"
        f"Final state : {last_state}\n"
        f"Status      : {status}\n"
        f"Exit code   : {exit_code}\n"
        f"Wall time   : {duration:.1f}s\n"
    )
    write_to_log(combined_path, footer)

    return JobResult(
        command=command_str,
        job_id=internal_id,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=duration,
        status=status,
        exit_code=exit_code,
        stdout_log_path=stdout_path,
        stderr_log_path=stderr_path,
        combined_log_path=combined_path,
        error_summary=(f"Slurm state: {last_state}" if status != "success" else ""),
    )
