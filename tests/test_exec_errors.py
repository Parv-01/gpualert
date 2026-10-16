"""Exec failures report what a shell would (D12).

`gpualert run -- cmd` is meant to be a drop-in for `cmd` in Slurm scripts,
so a command that cannot start has to exit the way a shell reports it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gpualert.cli import app
from gpualert.launcher import run_job

posix = pytest.mark.skipif(os.name != "posix", reason="POSIX exec semantics")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    monkeypatch.chdir(tmp_path)
    return h


def _combined(result):
    return Path(result.combined_log_path).read_text()


@posix
def test_file_without_execute_bit_is_126(tmp_path):
    script = tmp_path / "train.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o644)
    result = run_job([str(script)])
    assert result.exit_code == 126
    assert "chmod +x" in _combined(result)


@posix
def test_directory_is_126(tmp_path):
    result = run_job([str(tmp_path)])
    assert result.exit_code == 126
    assert "Cannot execute" in _combined(result)


@posix
def test_script_without_shebang_is_126_with_a_hint(tmp_path):
    script = tmp_path / "noshebang.sh"
    script.write_text("echo hi\n")
    script.chmod(0o755)
    result = run_job([str(script)])
    assert result.exit_code == 126
    assert "no #! line" in _combined(result)


@posix
def test_missing_interpreter_is_127_and_says_so(tmp_path):
    script = tmp_path / "bad.sh"
    script.write_text("#!/nonexistent/interpreter\necho hi\n")
    script.chmod(0o755)
    result = run_job([str(script)])
    assert result.exit_code == 127
    assert "#! interpreter" in _combined(result)


def test_missing_command_is_still_127():
    result = run_job(["definitely-not-a-binary-xyz"])
    assert result.exit_code == 127
    assert "Command not found: definitely-not-a-binary-xyz" in _combined(result)


def test_missing_working_directory_is_a_launcher_error(tmp_path):
    result = run_job([sys.executable, "-c", "pass"], cwd=str(tmp_path / "nope"))
    assert result.exit_code == -99
    assert "Working directory not found" in _combined(result)


@posix
def test_cli_exits_126_for_a_non_executable_file(tmp_path):
    script = tmp_path / "train.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o644)
    out = CliRunner().invoke(app, ["run", "--no-notify", "--", str(script)])
    assert out.exit_code == 126


@posix
def test_wrapper_failure_after_start_does_not_leave_the_child_running(monkeypatch, tmp_path):
    # If something in the wrapper breaks after Popen succeeded (here: thread
    # creation), the child must be stopped rather than left running
    # unwatched with pipes nobody reads.
    import threading as _threading

    import psutil

    started = {}
    real_popen = __import__("subprocess").Popen

    def spy(*a, **k):
        p = real_popen(*a, **k)
        started["pid"] = p.pid
        return p

    def boom(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr("gpualert.launcher.subprocess.Popen", spy)
    monkeypatch.setattr(_threading.Thread, "start", boom)
    result = run_job([sys.executable, "-c", "import time; time.sleep(60)"])
    assert result.exit_code == -99
    assert "can't start new thread" in _combined(result)
    assert not psutil.pid_exists(started["pid"]) or (
        psutil.Process(started["pid"]).status() == psutil.STATUS_ZOMBIE
    )
