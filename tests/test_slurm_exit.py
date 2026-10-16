"""`gpualert slurm` exit status and polling (D18, D19, and D5 on the Slurm path)."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from gpualert import log_manager, slurm
from gpualert.cli import app
from gpualert.slurm import _parse_exit_code, poll_job
from gpualert.types import NotificationResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    monkeypatch.delenv(log_manager.LOG_DIR_ENV, raising=False)
    return h


@pytest.mark.parametrize(
    "field, expected",
    [
        ("0:0", 0),
        ("1:0", 1),
        ("137:9", 137),
        ("0:9", -9),
        ("0:15", -15),
        ("2:9", 2),
        ("0:125", 0),
        ("0:0:extra", 0),
        ("", 0),
        ("garbage", 0),
    ],
)
def test_parse_exit_code(field, expected):
    assert _parse_exit_code(field) == expected


def _sacct(row: str):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=row + "\n", stderr="")


@pytest.mark.parametrize(
    "row, expected",
    [
        ("COMPLETED|0:0|00:01:00|train|gpu|n1", 0),
        ("FAILED|1:0|00:01:00|train|gpu|n1", 1),
        ("FAILED|0:9|00:01:00|train|gpu|n1", 137),
        ("CANCELLED by 1000|0:15|00:01:00|train|gpu|n1", 143),
        ("NODE_FAIL|0:0|00:01:00|train|gpu|n1", 1),
        ("PREEMPTED|0:0|00:01:00|train|gpu|n1", 1),
        ("OUT_OF_MEMORY|0:125|00:01:00|train|gpu|n1", 1),
        ("TIMEOUT|0:0|01:00:00|train|gpu|n1", 124),
    ],
)
def test_cli_slurm_exit_status(row, expected):
    ok = NotificationResult(success=True, notifier_type="email", message="ok")
    with (
        patch("gpualert.cli.is_slurm_available", return_value=True),
        patch("gpualert.slurm.is_slurm_available", return_value=True),
        patch("gpualert.slurm.subprocess.run", return_value=_sacct(row)),
        patch("gpualert.cli.get_notifier") as nf,
    ):
        nf.return_value.send.return_value = ok
        res = runner.invoke(app, ["slurm", "4242", "--interval", "1"])
    assert res.exit_code == expected, res.output


def test_cli_rejects_a_zero_interval():
    with patch("gpualert.cli.is_slurm_available", return_value=True):
        res = runner.invoke(app, ["slurm", "4242", "--interval", "0"])
    assert res.exit_code == 2


def test_timeout_is_reached_even_with_interval_zero():
    # 0.1.5 summed `interval` into elapsed, so at interval 0 the timeout was
    # never reached and sacct was called in a tight loop.
    calls = {"n": 0}

    def unknown(job_id):
        calls["n"] += 1
        time.sleep(0.01)
        return slurm.SlurmJobInfo(job_id=job_id, state="UNKNOWN")

    box = {}
    with (
        patch("gpualert.slurm.is_slurm_available", return_value=True),
        patch("gpualert.slurm.get_job_info", side_effect=unknown),
    ):
        # In a thread, so a regression fails the test instead of hanging it.
        worker = threading.Thread(
            target=lambda: box.update(result=poll_job(1, interval=0, timeout=1)), daemon=True
        )
        worker.start()
        worker.join(10)
    assert not worker.is_alive(), "poll_job never reached its timeout"
    result = box["result"]
    assert result.is_failed()
    assert "Poll timeout" in Path(result.combined_log_path).read_text()


def test_slurm_monitor_survives_an_unwritable_home(tmp_path, monkeypatch):
    not_a_dir = tmp_path / "file-home"
    not_a_dir.write_text("")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: not_a_dir))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    info = slurm.SlurmJobInfo(job_id=7, state="COMPLETED")
    with (
        patch("gpualert.slurm.is_slurm_available", return_value=True),
        patch("gpualert.slurm.get_job_info", return_value=info),
    ):
        result = poll_job(7, interval=0)
    assert result.is_success()
    assert len(result.log_files()) == 3
    assert any("could not be used" in n for n in result.notes)


def test_reporting_failure_keeps_the_slurm_status():
    row = "FAILED|0:9|00:01:00|train|gpu|n1"
    with (
        patch("gpualert.cli.is_slurm_available", return_value=True),
        patch("gpualert.slurm.is_slurm_available", return_value=True),
        patch("gpualert.slurm.subprocess.run", return_value=_sacct(row)),
        patch("gpualert.cli.get_notifier", side_effect=RuntimeError("smtp module broke")),
    ):
        res = runner.invoke(app, ["slurm", "4242", "--interval", "1"])
    assert res.exit_code == 137
