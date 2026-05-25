"""Day 2 tests — launcher and error parser."""

from __future__ import annotations

import os
import sys

import pytest


# ── parse_errors tests ──────────────────────────────────────────────────────
class TestParseErrors:
    def test_cuda_oom_detected(self):
        from gpualert.parse_errors import parse_errors

        result = parse_errors("", "CUDA out of memory. Tried to allocate 2.00 GiB", 1)
        assert "GPU out-of-memory" in result
        assert "Suggestion" in result

    def test_traceback_detected(self):
        from gpualert.parse_errors import parse_errors

        stderr = (
            "Traceback (most recent call last):\n"
            "  File 'train.py', line 5\n"
            "ValueError: bad input"
        )
        result = parse_errors("", stderr, 1)
        assert result != ""

    def test_no_error_returns_empty(self):
        from gpualert.parse_errors import parse_errors

        result = parse_errors("Training complete. Accuracy: 93.2%", "", 0)
        assert result == ""

    def test_generic_exit_code(self):
        from gpualert.parse_errors import parse_errors

        result = parse_errors("", "", 1)
        assert "exited with code 1" in result

    def test_extract_traceback(self):
        from gpualert.parse_errors import extract_traceback

        stderr = (
            "Some output\n"
            "Traceback (most recent call last):\n"
            "  File 'x.py', line 1\n"
            "ValueError: oops"
        )
        tb = extract_traceback(stderr)
        assert "Traceback" in tb
        assert "ValueError" in tb

    def test_success_metrics_extracted(self):
        from gpualert.parse_errors import extract_success_metrics

        stdout = "Epoch 50/50 - loss: 0.23 - accuracy: 0.932 - val_loss: 0.31"
        metrics = extract_success_metrics(stdout)
        assert metrics != ""
        assert "0.932" in metrics or "Accuracy" in metrics

    @pytest.mark.parametrize(
        "pattern,text",
        [
            ("NCCL", "NCCL error timeout"),
            ("MemoryError", "MemoryError: unable to allocate"),
            ("FileNotFoundError", "FileNotFoundError: /data/train.csv"),
            ("nan", "Training loss: nan detected at step 100"),
        ],
    )
    def test_various_error_patterns(self, pattern, text):
        from gpualert.parse_errors import parse_errors

        result = parse_errors("", text, 1)
        assert result != "", f"Pattern '{pattern}' not detected in: {text}"

    def test_confidence_levels(self):
        from gpualert.parse_errors import get_error_confidence

        assert get_error_confidence("") == "NONE"
        assert get_error_confidence("CUDA OOM\nSuggestion: try smaller batch") == "HIGH"
        assert get_error_confidence("Process exited with code 1") == "MEDIUM"


# ── launcher tests ──────────────────────────────────────────────────────────
class TestLauncher:
    def test_successful_echo_command(self):
        from gpualert.launcher import run_job

        result = run_job(["echo", "hello gpualert"])
        assert result.status == "success"
        assert result.exit_code == 0
        assert result.is_success()
        assert not result.is_failed()

    def test_failed_command_captured(self):
        from gpualert.launcher import run_job

        result = run_job([sys.executable, "-c", "import sys; sys.exit(1)"])
        assert result.status == "failed"
        assert result.exit_code == 1
        assert result.is_failed()

    def test_log_files_always_created(self):
        from gpualert.launcher import run_job

        result = run_job(["echo", "test"])
        assert len(result.log_files()) == 3
        for path in result.log_files():
            assert os.path.isfile(path), f"Log file missing: {path}"
            assert os.path.getsize(path) > 0, f"Log file empty: {path}"

    def test_log_files_created_even_on_failure(self):
        from gpualert.launcher import run_job

        result = run_job([sys.executable, "-c", "raise RuntimeError('test error')"])
        assert result.status == "failed"
        for path in result.log_files():
            assert os.path.isfile(path), f"Log file missing on failure: {path}"

    def test_stdout_captured_in_log(self):
        from gpualert.launcher import run_job

        result = run_job([sys.executable, "-c", "print('unique_test_string_12345')"])
        with open(result.stdout_log_path) as f:
            content = f.read()
        assert "unique_test_string_12345" in content

    def test_stderr_captured_in_log(self):
        from gpualert.launcher import run_job

        result = run_job([sys.executable, "-c", "import sys; sys.stderr.write('err_test_99\\n')"])
        with open(result.stderr_log_path) as f:
            content = f.read()
        assert "err_test_99" in content

    def test_command_not_found(self):
        from gpualert.launcher import run_job

        result = run_job(["nonexistent_command_xyz_999"])
        assert result.status == "failed"
        for path in result.log_files():
            assert os.path.isfile(path)

    def test_timeout_kills_process(self):
        from gpualert.launcher import run_job

        result = run_job([sys.executable, "-c", "import time; time.sleep(60)"], timeout=2)
        assert result.status == "timeout"
        assert result.is_failed()
        for path in result.log_files():
            assert os.path.isfile(path)

    def test_job_result_has_duration(self):
        from gpualert.launcher import run_job

        result = run_job([sys.executable, "-c", "import time; time.sleep(0.1)"])
        assert result.duration_seconds >= 0.1
        assert result.duration_human() != ""

    def test_combined_log_contains_both_streams(self):
        from gpualert.launcher import run_job

        result = run_job(
            [
                sys.executable,
                "-c",
                "import sys; print('stdout_marker'); sys.stderr.write('stderr_marker\\n')",
            ]
        )
        with open(result.combined_log_path) as f:
            content = f.read()
        assert "stdout_marker" in content
        assert "stderr_marker" in content
