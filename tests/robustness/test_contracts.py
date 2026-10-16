"""The invariants, written down as executable specification.

Everything else in this harness injects a specific fault. This module
states what must hold no matter which fault was injected, so a future
refactor cannot quietly weaken the guarantees the tool is built on.
"""

from __future__ import annotations

import os

import pytest

from gpualert.artifacts import prepare_attachments
from gpualert.launcher import run_job
from gpualert.types import ArtifactFile
from tests.robustness import faults, probes

pytestmark = pytest.mark.robustness

TERMINAL_STATUSES = {"success", "failed", "timeout", "interrupted", "stalled", "error"}

ADVERSARIAL_COMMANDS = [
    pytest.param(faults.quick(), id="trivial"),
    pytest.param(faults.program("import sys; sys.exit(3)"), id="nonzero-exit"),
    pytest.param(faults.program("raise SystemExit(-5)"), id="negative-exit"),
    pytest.param(faults.program("import os; os._exit(0)"), id="hard-exit"),
    pytest.param(
        faults.program("import sys; sys.stderr.write('boom\\n'); sys.exit(1)"), id="stderr"
    ),
    pytest.param(
        faults.program("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"), id="segfault"
    ),
    pytest.param(faults.program("pass"), id="no-output"),
    pytest.param(faults.emits_invalid_utf8(lines=1), id="invalid-utf8"),
    pytest.param(faults.emits_ansi_and_crlf(), id="ansi-crlf"),
    pytest.param(["definitely-not-a-real-binary-xyz"], id="missing-binary"),
    pytest.param(["/etc/passwd"], id="not-executable"),
    pytest.param([], id="empty-argv"),
]


class TestNeverRaises:
    """`run_job` promises a JobResult for every input. No exceptions."""

    @pytest.mark.parametrize("cmd", ADVERSARIAL_COMMANDS)
    def test_returns_a_result(self, isolated_home, workdir, cmd):
        try:
            result = run_job(cmd)
        except Exception as exc:  # noqa: BLE001 - the contract is that this cannot happen
            pytest.fail(f"run_job raised {type(exc).__name__}: {exc}")
        assert result is not None
        assert result.status in TERMINAL_STATUSES


class TestLogGuarantee:
    """Log paths are always populated and always absolute."""

    @pytest.mark.parametrize("cmd", ADVERSARIAL_COMMANDS)
    def test_log_paths_are_populated(self, isolated_home, workdir, cmd):
        result = run_job(cmd)
        assert result.stdout_log_path
        assert result.stderr_log_path
        assert result.combined_log_path
        for path in (
            result.stdout_log_path,
            result.stderr_log_path,
            result.combined_log_path,
        ):
            assert os.path.isabs(path)

    @pytest.mark.parametrize("cmd", ADVERSARIAL_COMMANDS)
    def test_logs_exist_on_disk_and_carry_the_header(self, isolated_home, workdir, cmd):
        result = run_job(cmd)
        assert len(result.log_files()) == 3
        assert "=== GPUAlert Job Log ===" in probes.log_text(result.combined_log_path)

    @pytest.mark.parametrize("cmd", ADVERSARIAL_COMMANDS)
    def test_every_run_is_finalised(self, isolated_home, workdir, cmd):
        result = run_job(cmd)
        assert probes.has_footer(result.combined_log_path)
        assert result.end_time is not None
        assert result.duration_seconds >= 0


class TestExitCodeFidelity:
    """The wrapper reports the child's fate, not its own."""

    @pytest.mark.parametrize("code", [0, 1, 3, 42, 127])
    def test_child_exit_code_is_preserved(self, isolated_home, workdir, code):
        result = run_job(faults.program(f"import sys; sys.exit({code})"))
        assert result.exit_code == code
        assert result.is_success() == (code == 0)

    def test_success_and_failure_are_mutually_exclusive(self, isolated_home, workdir):
        for cmd in (faults.quick(), faults.program("import sys; sys.exit(1)")):
            result = run_job(cmd)
            assert result.is_success() != result.is_failed()

    def test_a_signalled_child_is_reported_as_failed(self, isolated_home, workdir):
        result = run_job(faults.program("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"))
        assert result.is_failed()
        assert result.exit_code is not None and result.exit_code < 0

    @pytest.mark.skipif(os.name != "posix", reason="POSIX exec permissions")
    def test_a_non_executable_command_reports_126(self, isolated_home, workdir):
        script = workdir / "train.sh"
        script.write_text("#!/bin/sh\necho hi\n")
        script.chmod(0o644)
        result = run_job([str(script)])
        assert result.exit_code == 126
        assert "Unexpected launcher error" not in probes.log_text(result.combined_log_path)


class TestNonSilentBudget:
    """Nothing is dropped without being named."""

    def test_oversized_artifacts_are_reported_not_dropped(self, tmp_path, workdir):
        big = tmp_path / "checkpoint.pkl"
        big.write_bytes(b"0" * (2 * 1024 * 1024))
        artifacts = [ArtifactFile(path=str(big), size_bytes=big.stat().st_size, extension="pkl")]

        attached, skipped = prepare_attachments(
            artifacts=artifacts, log_files=[], job_failed=True, max_total_mb=0.001
        )
        accounted = set(attached) | set(skipped)
        assert str(big) in accounted or any(
            a.endswith(".zip") for a in accounted
        ), "an artifact vanished without appearing in either list"

    def test_overflow_zip_is_never_written_into_the_working_directory(self, tmp_path, workdir):
        big = tmp_path / "checkpoint.pkl"
        big.write_bytes(b"0" * (64 * 1024))
        artifacts = [ArtifactFile(path=str(big), size_bytes=big.stat().st_size, extension="pkl")]
        before = set(os.listdir(workdir))
        prepare_attachments(artifacts=artifacts, log_files=[], job_failed=True, max_total_mb=0.01)
        assert set(os.listdir(workdir)) == before

    def test_a_missing_artifact_is_listed_as_skipped(self, tmp_path):
        ghost = ArtifactFile(path=str(tmp_path / "gone.csv"), size_bytes=10, extension="csv")
        attached, skipped = prepare_attachments(artifacts=[ghost], log_files=[], job_failed=True)
        assert ghost.path in skipped
        assert ghost.path not in attached
