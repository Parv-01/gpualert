"""Many wrappers on one node at once.

This is the ordinary condition on a shared GPU box, not a stress case. The
invariants are that jobs do not read each other's state, do not attach each
other's files, and do not collide in the log directory.
"""

from __future__ import annotations

import concurrent.futures as cf
import os
from datetime import datetime

import pytest

from gpualert.artifacts import find_artifacts, prepare_attachments
from gpualert.launcher import run_job
from tests.robustness import faults, probes

pytestmark = pytest.mark.robustness


class TestArtifactScoping:
    @pytest.mark.xfail(
        strict=True,
        reason="D4 (same-user part): two of one user's jobs writing into one "
        "directory cannot be told apart without knowing which process created "
        "each file; planned with the process-tree work in 0.2.5. Other users' "
        "files are excluded since 0.1.6",
    )
    def test_a_job_does_not_attach_another_jobs_output(self, isolated_home, workdir):
        job_a_start = datetime.now()
        (workdir / "jobA_metrics.csv").write_text("mine")
        (workdir / "jobB_other_user_results.json").write_text("theirs")

        attached = {os.path.basename(a.path) for a in find_artifacts(job_a_start, cwd=str(workdir))}
        assert "jobA_metrics.csv" in attached
        assert (
            "jobB_other_user_results.json" not in attached
        ), "job A would email a file it never created"

    @pytest.mark.xfail(
        strict=True,
        reason="D4 (same-user part): a pre-existing file rewritten during the run "
        "looks exactly like the job rewriting its own results.csv; needs "
        "creator attribution, planned for 0.2.5",
    )
    def test_a_file_merely_touched_during_the_run_is_not_claimed(self, isolated_home, workdir):
        stale = workdir / "someone_elses_results.csv"
        stale.write_text("old data")
        job_start = datetime.now()
        # Set the mtime explicitly rather than calling utime(None): a
        # filesystem with one-second timestamp granularity can round "now"
        # down below job_start and pass this test for the wrong reason.
        touched_at = job_start.timestamp() + 5
        os.utime(stale, (touched_at, touched_at))  # another process touches it mid-run

        attached = {os.path.basename(a.path) for a in find_artifacts(job_start, cwd=str(workdir))}
        assert "someone_elses_results.csv" not in attached

    def test_files_older_than_the_job_are_ignored(self, isolated_home, workdir):
        old = workdir / "previous_run.csv"
        old.write_text("old")
        os.utime(old, (1_000_000, 1_000_000))
        attached = {
            os.path.basename(a.path) for a in find_artifacts(datetime.now(), cwd=str(workdir))
        }
        assert "previous_run.csv" not in attached

    def test_logs_are_always_attached_on_failure(self, isolated_home, workdir):
        result = run_job(faults.program("import sys; sys.exit(1)"))
        attach, _skipped = prepare_attachments(
            artifacts=[], log_files=result.log_files(), job_failed=True
        )
        assert len(attach) == 3


class TestParallelWrappers:
    @pytest.mark.slow
    def test_log_directories_do_not_collide(self, isolated_home, workdir):
        def one(index):
            return run_job(faults.quick(f"job {index}")).combined_log_path

        with cf.ThreadPoolExecutor(max_workers=16) as pool:
            paths = list(pool.map(one, range(16)))

        assert len(set(paths)) == 16, "two concurrent jobs shared a log directory"

    @pytest.mark.slow
    def test_every_parallel_job_gets_its_own_output(self, isolated_home, workdir):
        # Markers are delimited on both sides: an undelimited "marker 1"
        # is a substring of "marker 11" and would report a crossover that
        # never happened.
        def one(index):
            result = run_job(faults.program(f"print('<marker:{index}>')"))
            return index, probes.log_text(result.stdout_log_path)

        with cf.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(one, range(12)))

        for index, body in results:
            assert f"<marker:{index}>" in body
            others = [f"<marker:{j}>" for j in range(12) if j != index]
            assert not any(o in body for o in others), "output crossed between jobs"

    @pytest.mark.slow
    def test_parallel_runs_do_not_leak_descriptors(self, isolated_home, workdir):
        run_job(faults.quick())
        before = probes.open_fd_count()

        def one(_index):
            return run_job(faults.quick()).status

        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(one, range(24)))

        after = probes.open_fd_count()
        assert after - before <= 4, f"leaked {after - before} descriptors under concurrency"
