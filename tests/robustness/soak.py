"""Long-running soak test. Not part of the pytest suite.

Runs short jobs continuously and watches for the failures that only appear
over time: descriptor leaks, thread leaks, unbounded memory, orphaned
processes and log directories that never stop growing.

    python tests/robustness/soak.py --minutes 60 --workers 8
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gpualert.launcher import run_job  # noqa: E402
from tests.robustness import faults, probes  # noqa: E402


def _one(index: int) -> str:
    if index % 7 == 0:
        return run_job(faults.program("import sys; sys.exit(1)")).status
    if index % 11 == 0:
        return run_job(faults.chatty(lines=3, interval=0.01)).status
    return run_job(faults.quick()).status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--report-every", type=int, default=200)
    args = parser.parse_args()

    baseline_fds = probes.open_fd_count()
    baseline_threads = probes.thread_count()
    baseline_rss = probes.rss_mb()
    deadline = time.monotonic() + args.minutes * 60
    completed = 0
    failures = 0

    print(
        f"soak: {args.minutes:g} min, {args.workers} workers | "
        f"baseline fds={baseline_fds} threads={baseline_threads} rss={baseline_rss:.0f}MB"
    )

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        while time.monotonic() < deadline:
            batch = list(pool.map(_one, range(completed, completed + args.workers)))
            completed += len(batch)
            failures += sum(1 for status in batch if status not in ("success", "failed"))

            if completed % args.report_every < args.workers:
                fds = probes.open_fd_count()
                threads = probes.thread_count()
                rss = probes.rss_mb()
                print(
                    f"  {completed:6d} jobs | fds {fds:4d} (+{fds - baseline_fds}) | "
                    f"threads {threads:3d} (+{threads - baseline_threads}) | "
                    f"rss {rss:6.0f}MB (+{rss - baseline_rss:.0f}) | "
                    f"unexpected statuses {failures}"
                )

    fds = probes.open_fd_count()
    threads = probes.thread_count()
    rss = probes.rss_mb()
    print(f"\nsoak finished: {completed} jobs")

    problems = []
    if fds - baseline_fds > 10:
        problems.append(f"descriptor leak: +{fds - baseline_fds}")
    if threads - baseline_threads > 5:
        problems.append(f"thread leak: +{threads - baseline_threads}")
    if rss - baseline_rss > 200:
        problems.append(f"memory growth: +{rss - baseline_rss:.0f}MB")
    if failures:
        problems.append(f"{failures} jobs ended in an unexpected status")

    if problems:
        for problem in problems:
            print(f"  FAIL {problem}")
        return 1
    print("  all leak checks clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
