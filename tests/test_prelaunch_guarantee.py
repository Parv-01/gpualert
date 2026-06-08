"""Regression lock: log files MUST exist on disk before subprocess starts.

This is the invariant that makes GPUAlert reliable on crashes, segfaults,
SIGKILLs, and OOM-killer events: the wrapper creates and writes the header
into combined.log, stdout.log, and stderr.log BEFORE calling
subprocess.Popen, so the user always has files to inspect even if the
process dies before producing any output.

Any change that moves file creation after Popen will break this test.
Do not relax it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _popen_that_explodes(*args, **kwargs):
    raise RuntimeError("simulated Popen failure before any output")


class TestPreLaunchLogGuarantee:
    def test_log_files_exist_when_popen_raises(self, monkeypatch, tmp_path):
        """Popen raises immediately → all three log files still on disk
        with the GPUAlert header line."""
        # Force logs into an isolated dir so we don't pollute ~/.gpualert
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(subprocess, "Popen", _popen_that_explodes)

        from gpualert.launcher import run_job

        result = run_job(["python3", "-c", "print('never runs')"])

        log_files = [
            result.stdout_log_path,
            result.stderr_log_path,
            result.combined_log_path,
        ]
        for lf in log_files:
            assert lf, "log path must be populated"
            assert Path(lf).exists(), f"log file missing: {lf}"
            content = Path(lf).read_text(encoding="utf-8", errors="replace")
            assert "GPUAlert Job Log" in content, f"header not written before Popen in {lf}"
            assert result.job_id in content, "job_id must appear in header"

        assert result.is_failed(), "Popen failure must surface as a failed job"

    def test_header_written_before_popen_call_order(self, monkeypatch, tmp_path):
        """File creation must happen BEFORE Popen, not after.

        We track the order: assert the files already exist by the time the
        Popen call is attempted.
        """
        monkeypatch.setenv("HOME", str(tmp_path))

        captured = {"paths_existed_at_popen": None}

        from gpualert import launcher

        original_paths_holder = {}

        def spy_popen(*args, **kwargs):
            # At the moment Popen is invoked, the log files should already
            # exist with header content.
            existed = []
            for path in original_paths_holder.get("paths", []):
                existed.append(Path(path).exists())
            captured["paths_existed_at_popen"] = existed
            raise RuntimeError("stop after the order check")

        # Wrap create_job_log_dir to capture the paths the launcher chose.
        real_create = launcher.create_job_log_dir
        real_get_paths = launcher.get_job_log_paths

        def patched_create(job_id, cmd):
            d = real_create(job_id, cmd)
            original_paths_holder["paths"] = list(real_get_paths(d))
            return d

        monkeypatch.setattr(launcher, "create_job_log_dir", patched_create)
        monkeypatch.setattr(subprocess, "Popen", spy_popen)

        launcher.run_job(["python3", "-c", "print('x')"])

        assert (
            captured["paths_existed_at_popen"] is not None
        ), "Popen was never called — test setup broken"
        assert all(captured["paths_existed_at_popen"]), (
            "log files did NOT exist at the moment Popen was called: "
            f"{captured['paths_existed_at_popen']}"
        )

    def test_log_files_survive_filenotfounderror(self, monkeypatch, tmp_path):
        """Command-not-found is the realistic case where Popen raises.
        Logs must still be on disk with a SYSTEM error line appended."""
        monkeypatch.setenv("HOME", str(tmp_path))

        from gpualert.launcher import run_job

        result = run_job(["this-command-does-not-exist-xyz"])

        combined = Path(result.combined_log_path)
        assert combined.exists()
        text = combined.read_text(encoding="utf-8", errors="replace")
        assert "GPUAlert Job Log" in text
        # The launcher converts FileNotFoundError into a SYSTEM line.
        assert "Command not found" in text or "ERROR" in text
        assert result.is_failed()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
