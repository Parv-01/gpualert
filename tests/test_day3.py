"""Day 3 tests — artifact scanner and Slurm monitor."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


# ── artifacts ───────────────────────────────────────────────────────────────
class TestArtifacts:
    def test_find_artifacts_by_pattern(self, tmp_path):
        from gpualert.artifacts import find_artifacts

        start = datetime.now() - timedelta(seconds=1)
        (tmp_path / "metrics.csv").write_text("a,b\n1,2")
        (tmp_path / "loss.png").write_bytes(b"\x89PNG fake")
        (tmp_path / "model.pt").write_bytes(b"fake model")

        found = find_artifacts(start, cwd=str(tmp_path), patterns=["*.csv", "*.png"])
        names = [a.filename() for a in found]
        assert "metrics.csv" in names
        assert "loss.png" in names
        assert "model.pt" not in names

    def test_size_filtering(self, tmp_path):
        from gpualert.artifacts import find_artifacts

        start = datetime.now() - timedelta(seconds=1)
        (tmp_path / "small.csv").write_text("a,b")
        big = tmp_path / "huge.csv"
        big.write_bytes(b"x" * (30 * 1024 * 1024))  # 30 MB

        found = find_artifacts(start, cwd=str(tmp_path), max_single_mb=5.0)
        names = [a.filename() for a in found]
        assert "small.csv" in names
        assert "huge.csv" not in names

    def test_files_before_start_excluded(self, tmp_path):
        from gpualert.artifacts import find_artifacts

        (tmp_path / "old.csv").write_text("old")
        time.sleep(0.05)
        start = datetime.now()
        time.sleep(0.05)
        (tmp_path / "new.csv").write_text("new")

        found = find_artifacts(start, cwd=str(tmp_path), patterns=["*.csv"])
        names = [a.filename() for a in found]
        assert "new.csv" in names
        assert "old.csv" not in names

    def test_max_depth_limits_recursion(self, tmp_path):
        from gpualert.artifacts import find_artifacts

        start = datetime.now() - timedelta(seconds=1)
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "buried.csv").write_text("deep")
        (tmp_path / "surface.csv").write_text("top")

        found = find_artifacts(
            start, cwd=str(tmp_path), patterns=["*.csv"], max_depth=2
        )
        names = [a.filename() for a in found]
        assert "surface.csv" in names
        assert "buried.csv" not in names

    def test_prepare_attachments_always_includes_logs_on_failure(self, tmp_path):
        from gpualert.artifacts import prepare_attachments

        log_path = str(tmp_path / "stderr.log")
        (tmp_path / "stderr.log").write_text("error output")

        to_attach, _skipped = prepare_attachments(
            artifacts=[],
            log_files=[log_path],
            job_failed=True,
            attach_logs=False,  # ignored when job_failed=True
        )
        assert log_path in to_attach

    def test_prepare_attachments_logs_skipped_on_success_when_disabled(
        self, tmp_path
    ):
        from gpualert.artifacts import prepare_attachments

        log_path = str(tmp_path / "stdout.log")
        (tmp_path / "stdout.log").write_text("ok")

        to_attach, _ = prepare_attachments(
            artifacts=[],
            log_files=[log_path],
            job_failed=False,
            attach_logs=False,
        )
        assert log_path not in to_attach

    def test_budget_overflow_compressed_into_zip(self, tmp_path):
        from gpualert.artifacts import prepare_attachments
        from gpualert.types import ArtifactFile

        # Two 0.6 MB files; budget 1 MB. One fits, the other overflows → zip.
        a1 = tmp_path / "a.csv"
        a1.write_bytes(b"x" * (600 * 1024))
        a2 = tmp_path / "b.csv"
        a2.write_bytes(b"y" * (600 * 1024))
        log = tmp_path / "stdout.log"
        log.write_text("ok")

        arts = [
            ArtifactFile(path=str(a1), size_bytes=a1.stat().st_size, extension="csv"),
            ArtifactFile(path=str(a2), size_bytes=a2.stat().st_size, extension="csv"),
        ]
        to_attach, skipped = prepare_attachments(
            artifacts=arts,
            log_files=[str(log)],
            job_failed=False,
            max_total_mb=1.0,
            attach_logs=True,
        )
        # Either an overflow zip exists, or the second file was skipped.
        has_zip = any(p.endswith("artifacts_overflow.zip") for p in to_attach)
        assert has_zip or str(a2) in skipped

    def test_summarize_artifacts(self):
        from gpualert.artifacts import summarize_artifacts
        from gpualert.types import ArtifactFile

        arts = [
            ArtifactFile(path="/tmp/metrics.csv", size_bytes=1024, extension="csv"),
            ArtifactFile(path="/tmp/loss.png", size_bytes=2048, extension="png"),
        ]
        summary = summarize_artifacts(arts)
        assert "2 files" in summary
        assert "metrics.csv" in summary
        assert "loss.png" in summary

    def test_summarize_empty(self):
        from gpualert.artifacts import summarize_artifacts

        assert summarize_artifacts([]) == "0 files"


# ── slurm ───────────────────────────────────────────────────────────────────
class TestSlurm:
    def test_slurm_availability_returns_bool(self):
        from gpualert.slurm import is_slurm_available

        assert isinstance(is_slurm_available(), bool)

    def test_poll_raises_when_unavailable(self):
        from gpualert.slurm import SlurmNotAvailableError, poll_job

        with patch("gpualert.slurm.is_slurm_available", return_value=False):
            with pytest.raises(SlurmNotAvailableError):
                poll_job(12345)

    def test_poll_with_mocked_completed_job(self):
        from gpualert.slurm import poll_job
        from gpualert.types import SlurmJobInfo

        info = SlurmJobInfo(
            job_id=12345, state="COMPLETED", exit_code=0, elapsed_seconds=120.0
        )
        with patch("gpualert.slurm.is_slurm_available", return_value=True), \
             patch("gpualert.slurm.get_job_info", return_value=info):
            result = poll_job(12345, interval=0)
        assert result.status == "success"
        assert result.exit_code == 0
        for path in result.log_files():
            assert os.path.isfile(path)

    def test_poll_with_mocked_failed_job(self):
        from gpualert.slurm import poll_job
        from gpualert.types import SlurmJobInfo

        info = SlurmJobInfo(
            job_id=99, state="FAILED", exit_code=1, elapsed_seconds=30.0
        )
        with patch("gpualert.slurm.is_slurm_available", return_value=True), \
             patch("gpualert.slurm.get_job_info", return_value=info):
            result = poll_job(99, interval=0)
        assert result.is_failed()
        for path in result.log_files():
            assert os.path.isfile(path)

    def test_poll_with_oom_killed_job(self):
        from gpualert.slurm import poll_job
        from gpualert.types import SlurmJobInfo

        info = SlurmJobInfo(
            job_id=42, state="OUT_OF_MEMORY", exit_code=137, elapsed_seconds=5.0
        )
        with patch("gpualert.slurm.is_slurm_available", return_value=True), \
             patch("gpualert.slurm.get_job_info", return_value=info):
            result = poll_job(42, interval=0)
        assert result.status == "failed"
        assert "OUT_OF_MEMORY" in result.error_summary

    def test_parse_elapsed(self):
        from gpualert.slurm import _parse_elapsed

        assert _parse_elapsed("00:00:30") == 30.0
        assert _parse_elapsed("01:02:03") == 1 * 3600 + 2 * 60 + 3
        assert _parse_elapsed("2-00:00:00") == 2 * 86400
        assert _parse_elapsed("") == 0.0
        assert _parse_elapsed("garbage") == 0.0

    def test_parse_exit_code(self):
        from gpualert.slurm import _parse_exit_code

        assert _parse_exit_code("0:0") == 0
        assert _parse_exit_code("1:0") == 1
        assert _parse_exit_code("137:9") == 137
        assert _parse_exit_code("") == 0
        assert _parse_exit_code("garbage") == 0

    def test_get_job_info_handles_missing_sacct(self):
        """If sacct returns non-zero, we get UNKNOWN, no exception."""
        from gpualert.slurm import get_job_info

        # Simulate sacct failing
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "sacct: error"
            info = get_job_info(999)
        assert info.state == "UNKNOWN"

    def test_get_job_info_parses_real_output(self):
        """Verify the parser handles realistic sacct --parsable2 output."""
        from gpualert.slurm import get_job_info

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = (
                "COMPLETED|0:0|01:23:45|train.sh|gpu|node007\n"
            )
            info = get_job_info(7654)
        assert info.state == "COMPLETED"
        assert info.exit_code == 0
        assert info.elapsed_seconds == 1 * 3600 + 23 * 60 + 45
        assert info.job_name == "train.sh"
        assert info.partition == "gpu"
        assert info.node_list == "node007"

    def test_on_update_callback_invoked(self):
        from gpualert.slurm import poll_job
        from gpualert.types import SlurmJobInfo

        info = SlurmJobInfo(
            job_id=1, state="COMPLETED", exit_code=0, elapsed_seconds=10.0
        )
        seen = []
        with patch("gpualert.slurm.is_slurm_available", return_value=True), \
             patch("gpualert.slurm.get_job_info", return_value=info):
            poll_job(1, interval=0, on_update=lambda i: seen.append(i.state))
        assert seen == ["COMPLETED"]

    def test_on_update_callback_exception_does_not_crash_poll(self):
        from gpualert.slurm import poll_job
        from gpualert.types import SlurmJobInfo

        info = SlurmJobInfo(
            job_id=1, state="COMPLETED", exit_code=0, elapsed_seconds=10.0
        )

        def boom(i):
            raise RuntimeError("user callback exploded")

        with patch("gpualert.slurm.is_slurm_available", return_value=True), \
             patch("gpualert.slurm.get_job_info", return_value=info):
            result = poll_job(1, interval=0, on_update=boom)
        assert result.status == "success"
