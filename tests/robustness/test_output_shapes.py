"""What the wrapper does with output that is not tidy UTF-8 text.

The pre-launch log guarantee is only worth something if what lands in the
log is what the job actually produced. These tests treat the log as the
product.
"""

from __future__ import annotations

import os
import tracemalloc

import pytest

from gpualert.launcher import run_job
from gpualert.log_manager import get_tail
from tests.robustness import faults, probes

pytestmark = pytest.mark.robustness


class TestInvalidUTF8:
    def test_output_after_a_bad_byte_is_still_captured(self, isolated_home, workdir):
        result = run_job(faults.emits_invalid_utf8())
        body = probes.log_text(result.stdout_log_path)
        assert "BEFORE" in body, "output before the bad byte should always survive"
        assert "AFTER 4" in body, "output after the bad byte was silently discarded"

    def test_output_before_a_bad_byte_survives(self, isolated_home, workdir):
        result = run_job(faults.emits_invalid_utf8())
        assert "BEFORE" in probes.log_text(result.stdout_log_path)

    def test_truncation_is_disclosed(self, isolated_home, workdir):
        result = run_job(faults.emits_invalid_utf8())
        disclosed = any("decode" in n.lower() or "truncat" in n.lower() for n in result.notes)
        assert disclosed, "the job lost output and said nothing about it"


class TestAwkwardLineShapes:
    @pytest.mark.slow
    def test_a_line_without_a_newline_does_not_lose_what_follows(self, isolated_home, workdir):
        result = run_job(faults.emits_long_line_without_newline(megabytes=4))
        assert "TAIL MARKER" in probes.log_text(result.stdout_log_path)

    def test_ansi_and_crlf_pass_through(self, isolated_home, workdir):
        result = run_job(faults.emits_ansi_and_crlf())
        assert "TAIL MARKER" in probes.log_text(result.stdout_log_path)

    def test_empty_output_still_produces_all_three_logs(self, isolated_home, workdir):
        result = run_job(faults.program("pass"))
        assert len(result.log_files()) == 3
        assert probes.has_footer(result.combined_log_path)


class TestVolume:
    @pytest.mark.slow
    def test_a_chatty_job_keeps_its_last_line(self, isolated_home, workdir):
        result = run_job(faults.emits_volume(megabytes=16))
        assert "TAIL MARKER" in probes.log_text(result.stdout_log_path)

    @pytest.mark.slow
    @pytest.mark.xfail(
        strict=True,
        reason="D7: no rotation or size cap, so a chatty job grows ~/.gpualert "
        "without bound on a quota'd home",
    )
    def test_log_growth_is_bounded(self, isolated_home, workdir):
        result = run_job(faults.emits_volume(megabytes=16))
        combined = os.path.getsize(result.combined_log_path) / (1024 * 1024)
        assert combined < 8, f"combined.log reached {combined:.0f} MB with no cap"


class TestTailReader:
    @pytest.mark.slow
    def test_tail_memory_is_independent_of_file_size(self, tmp_path):
        # tracemalloc, not RSS: the resident set is a high-water mark for the
        # whole process and stays inflated by earlier tests, which would let
        # this pass for the wrong reason.
        big = tmp_path / "combined.log"
        with open(big, "w", encoding="utf-8") as handle:
            for i in range(300_000):
                handle.write(f"epoch {i} loss 0.1234 acc 0.98 lr 1e-4 padding padding\n")
        size_mb = os.path.getsize(big) / (1024 * 1024)

        tracemalloc.start()
        tail = get_tail(str(big), 50)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert tail.count("\n") <= 51
        peak_mb = peak / (1024 * 1024)
        assert peak_mb < 8, (
            f"reading 50 lines from a {size_mb:.0f} MB log peaked at {peak_mb:.0f} MB; "
            "the reader should seek from the end instead of loading the file"
        )

    def test_tail_returns_the_last_lines(self, tmp_path):
        path = tmp_path / "x.log"
        path.write_text("".join(f"line {i}\n" for i in range(500)), encoding="utf-8")
        tail = get_tail(str(path), 10)
        assert "line 499" in tail
        assert "line 400" not in tail

    def test_tail_of_a_missing_file_is_empty(self, tmp_path):
        assert get_tail(str(tmp_path / "nope.log"), 10) == ""
