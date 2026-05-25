"""Day 3 tests — Slurm monitor (split out from test_day3.py)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


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
            job_id=12345,
            state="COMPLETED",
            exit_code=0,
            elapsed_seconds=120.0,
        )
        with (
            patch("gpualert.slurm.is_slurm_available", return_value=True),
            patch("gpualert.slurm.get_job_info", return_value=info),
        ):
            result = poll_job(12345, interval=0)
        assert result.status == "success"
        assert result.exit_code == 0
        for path in result.log_files():
            assert os.path.isfile(path)

    def test_poll_with_mocked_failed_job(self):
        from gpualert.slurm import poll_job
        from gpualert.types import SlurmJobInfo

        info = SlurmJobInfo(
            job_id=99,
            state="FAILED",
            exit_code=1,
            elapsed_seconds=30.0,
        )
        with (
            patch("gpualert.slurm.is_slurm_available", return_value=True),
            patch("gpualert.slurm.get_job_info", return_value=info),
        ):
            result = poll_job(99, interval=0)
        assert result.is_failed()
        for path in result.log_files():
            assert os.path.isfile(path)

    def test_poll_with_oom_killed_job(self):
        from gpualert.slurm import poll_job
        from gpualert.types import SlurmJobInfo

        info = SlurmJobInfo(
            job_id=42,
            state="OUT_OF_MEMORY",
            exit_code=137,
            elapsed_seconds=5.0,
        )
        with (
            patch("gpualert.slurm.is_slurm_available", return_value=True),
            patch("gpualert.slurm.get_job_info", return_value=info),
        ):
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
        from gpualert.slurm import get_job_info

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "sacct: error"
            info = get_job_info(999)
        assert info.state == "UNKNOWN"

    def test_get_job_info_parses_real_output(self):
        from gpualert.slurm import get_job_info

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "COMPLETED|0:0|01:23:45|train.sh|gpu|node007\n"
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
            job_id=1,
            state="COMPLETED",
            exit_code=0,
            elapsed_seconds=10.0,
        )
        seen = []
        with (
            patch("gpualert.slurm.is_slurm_available", return_value=True),
            patch("gpualert.slurm.get_job_info", return_value=info),
        ):
            poll_job(1, interval=0, on_update=lambda i: seen.append(i.state))
        assert seen == ["COMPLETED"]

    def test_on_update_callback_exception_does_not_crash_poll(self):
        from gpualert.slurm import poll_job
        from gpualert.types import SlurmJobInfo

        info = SlurmJobInfo(
            job_id=1,
            state="COMPLETED",
            exit_code=0,
            elapsed_seconds=10.0,
        )

        def boom(_):
            raise RuntimeError("user callback exploded")

        with (
            patch("gpualert.slurm.is_slurm_available", return_value=True),
            patch("gpualert.slurm.get_job_info", return_value=info),
        ):
            result = poll_job(1, interval=0, on_update=boom)
        assert result.status == "success"
