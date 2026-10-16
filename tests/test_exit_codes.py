"""Exit-status contract for `gpualert run` (0.1.6+).

`gpualert run -- cmd` must exit with the same status a shell would report
for `cmd`, so it can replace `cmd` in Slurm scripts and pipelines:

    job exited N          -> N
    killed by signal N    -> 128 + N
    command not found     -> 127
    --timeout reached     -> 124
    Ctrl+C                -> 130
    launcher error        -> 1

A failed notification never changes the exit status.
"""

from __future__ import annotations

import sys
from datetime import datetime
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from gpualert.cli import _shell_exit_code, app
from gpualert.types import JobResult, NotificationResult

runner = CliRunner()
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")


def _result(status: str, exit_code):
    return JobResult(
        command="x",
        job_id="t",
        start_time=datetime.now(),
        status=status,
        exit_code=exit_code,
    )


def _send_ok(result, attachments):
    return NotificationResult(success=True, notifier_type="email", message="ok")


def _send_fail(result, attachments):
    return NotificationResult(success=False, notifier_type="email", message="SMTP down")


class TestMapping:
    @pytest.mark.parametrize(
        "status, code, expected",
        [
            ("success", 0, 0),
            ("failed", 1, 1),
            ("failed", 3, 3),
            ("failed", 127, 127),
            ("failed", -9, 137),  # SIGKILL
            ("failed", -11, 139),  # SIGSEGV
            ("failed", -15, 143),  # SIGTERM
            ("timeout", -1, 124),
            ("interrupted", -2, 130),
            ("failed", -99, 1),  # internal launcher error
            ("failed", None, 1),
            ("success", None, 0),
            ("failed", 300, 1),  # out of shell range
        ],
    )
    def test_maps_to_shell_status(self, status, code, expected):
        assert _shell_exit_code(_result(status, code)) == expected


class TestRunPassesStatusThrough:
    def _run(self, *cmd, send=_send_ok):
        with patch("gpualert.cli.get_notifier") as nf:
            nf.return_value.send = send
            return runner.invoke(app, ["run", "--", *cmd])

    def test_custom_exit_code(self):
        res = self._run(sys.executable, "-c", "raise SystemExit(3)")
        assert res.exit_code == 3

    def test_missing_binary_is_127(self, tmp_path):
        res = self._run(str(tmp_path / "does_not_exist"))
        assert res.exit_code == 127

    @posix_only
    def test_sigkill_is_137(self):
        res = self._run(sys.executable, "-c", "import os; os.kill(os.getpid(), 9)")
        assert res.exit_code == 137

    @posix_only
    def test_segfault_is_139(self):
        res = self._run(sys.executable, "-c", "import ctypes; ctypes.string_at(0)")
        assert res.exit_code == 139

    def test_notifier_failure_keeps_job_status(self):
        ok = self._run(sys.executable, "-c", "pass", send=_send_fail)
        bad = self._run(sys.executable, "-c", "raise SystemExit(5)", send=_send_fail)
        assert ok.exit_code == 0
        assert bad.exit_code == 5

    def test_no_notify_path_also_passes_status(self):
        res = runner.invoke(
            app, ["run", "--no-notify", "--", sys.executable, "-c", "raise SystemExit(4)"]
        )
        assert res.exit_code == 4


class TestReportingCannotChangeTheStatus:
    """Nothing after the job may change the exit status (I3)."""

    def _run_with(self, target, exc=RuntimeError("boom")):
        with patch(target, side_effect=exc), patch("gpualert.cli.get_notifier") as nf:
            nf.return_value.send = _send_ok
            return runner.invoke(app, ["run", "--", sys.executable, "-c", "raise SystemExit(6)"])

    def test_artifact_scan_failure(self):
        assert self._run_with("gpualert.cli.find_artifacts").exit_code == 6

    def test_attachment_planning_failure(self):
        assert (
            self._run_with("gpualert.cli.prepare_attachments", OSError("vanished")).exit_code == 6
        )

    def test_result_table_failure(self):
        assert self._run_with("gpualert.cli._print_result_table").exit_code == 6

    def test_ctrl_c_during_notification(self):
        assert self._run_with("gpualert.cli.get_notifier", KeyboardInterrupt()).exit_code == 6

    def test_success_stays_zero_when_reporting_fails(self):
        with patch("gpualert.cli.prepare_attachments", side_effect=RuntimeError("x")):
            res = runner.invoke(app, ["run", "--", sys.executable, "-c", "pass"])
        assert res.exit_code == 0
        assert "Reporting failed" in res.output


@posix_only
def test_closed_stdout_pipe_keeps_the_job_status(tmp_path):
    # `gpualert run ... | head -2`: once head exits, every write to stdout
    # fails. Rich used to answer that with SystemExit(1).
    import subprocess as sp

    script = "import sys\nfor i in range(200000): print(i)\nsys.exit(5)"
    wrapper = sp.Popen(
        [
            sys.executable,
            "-c",
            "from gpualert.cli import app; app(prog_name='gpualert')",
            "run",
            "-v",
            "--no-notify",
            "--",
            sys.executable,
            "-c",
            script,
        ],
        cwd=tmp_path,
        stdout=sp.PIPE,
        stderr=sp.DEVNULL,
    )
    wrapper.stdout.read(10)
    wrapper.stdout.close()
    assert wrapper.wait(timeout=120) == 5
