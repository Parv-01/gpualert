"""
Long-running job with progress output. Use with --timeout to test the
timeout path:

    gpualert run --timeout 5 -- python examples/long_running_job.py 60
    gpualert run --dry-run -- python examples/long_running_job.py 10
"""

from __future__ import annotations

import sys
import time


def main(total_steps: int = 20) -> None:
    print(f"Starting long job: {total_steps} steps (~1s each)")
    for i in range(1, total_steps + 1):
        time.sleep(1)
        progress = i / total_steps * 100
        print(f"Step {i}/{total_steps} ({progress:.0f}%) — processing...", flush=True)
    print("\nAll steps complete.")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    main(n)
