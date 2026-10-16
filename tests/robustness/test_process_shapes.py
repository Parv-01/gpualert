"""Jobs whose process tree is not a single child.

Every real distributed launcher is a tree: torchrun forks per-GPU workers,
mpirun forks per-rank processes, srun forks per-task. A wrapper that only
tracks its direct child controls none of them, and on a shared GPU an
untracked worker is memory nobody can reclaim.
"""

from __future__ import annotations

import time

import pytest

from gpualert.launcher import run_job
from tests.robustness import faults, probes

pytestmark = [pytest.mark.robustness, pytest.mark.slow]


class TestGrandchildren:
    @pytest.mark.xfail(
        strict=True,
        reason="D3: timeout kills only the direct child, so launcher workers "
        "survive and keep holding GPU memory",
    )
    def test_timeout_kills_the_whole_tree(self, isolated_home, workdir, tmp_path):
        pid_file = tmp_path / "worker.pid"
        result = run_job(faults.spawns_grandchild(str(pid_file)), timeout=3)
        worker = probes.read_pid_file(pid_file, timeout=10)

        assert result.status == "timeout"
        assert worker is not None
        assert (
            probes.survivors([worker]) == []
        ), f"worker {worker} outlived the wrapper; its GPU memory is unreclaimable"

    @pytest.mark.xfail(
        strict=True,
        reason="D3: a double-forked worker is reparented to init and never tracked",
    )
    def test_daemonised_worker_is_cleaned_up(self, isolated_home, workdir, tmp_path):
        pid_file = tmp_path / "daemon.pid"
        run_job(faults.double_forks(str(pid_file)), timeout=3)
        daemon = probes.read_pid_file(pid_file, timeout=10)
        assert daemon is not None
        assert probes.survivors([daemon]) == []

    def test_timeout_still_reports_the_right_status(self, isolated_home, workdir, tmp_path):
        result = run_job(faults.spawns_grandchild(str(tmp_path / "w.pid")), timeout=3)
        assert result.status == "timeout"
        assert result.exit_code == -1
        assert probes.has_footer(result.combined_log_path)


class TestShutdownLadder:
    @pytest.mark.xfail(
        strict=True,
        reason="D10: timeout calls proc.kill() (SIGKILL) directly, so a training "
        "script never gets the SIGTERM it needs to flush a final checkpoint",
    )
    def test_timeout_gives_the_child_a_chance_to_clean_up(self, isolated_home, workdir, tmp_path):
        marker = tmp_path / "cleaned_up"
        run_job(faults.cleans_up_on_sigterm(str(marker)), timeout=3)
        assert probes.wait_for(marker.exists, timeout=8), (
            "the child was SIGKILLed without a graceful SIGTERM first; "
            "any final checkpoint write is lost"
        )

    def test_a_child_ignoring_sigterm_is_still_killed(self, isolated_home, workdir):
        # The escalation half of the ladder already works, because the
        # current code skips straight to SIGKILL.
        started = time.monotonic()
        result = run_job(faults.ignores_sigterm(), timeout=3)
        elapsed = time.monotonic() - started
        assert result.status == "timeout"
        assert elapsed < 20, "wrapper hung on a child that ignores SIGTERM"

    def test_a_child_that_exits_immediately_is_fine(self, isolated_home, workdir):
        result = run_job(faults.quick())
        assert result.status == "success"
        assert result.exit_code == 0


class TestResourceHygiene:
    def test_repeated_runs_do_not_leak_descriptors(self, isolated_home, workdir):
        run_job(faults.quick())  # warm up import-time descriptors
        before = probes.open_fd_count()
        for _ in range(15):
            run_job(faults.quick())
        after = probes.open_fd_count()
        assert after - before <= 2, f"leaked {after - before} descriptors over 15 runs"

    def test_repeated_runs_do_not_leak_threads(self, isolated_home, workdir):
        run_job(faults.quick())
        before = probes.thread_count()
        for _ in range(15):
            run_job(faults.quick())
        time.sleep(0.5)
        after = probes.thread_count()
        assert after - before <= 2, f"leaked {after - before} threads over 15 runs"

    def test_reader_threads_do_not_outlive_the_job(self, isolated_home, workdir):
        before = probes.thread_count()
        run_job(faults.chatty(lines=5, interval=0.05))
        assert probes.wait_for(lambda: probes.thread_count() <= before + 1, timeout=5)
