"""Artifact scan rules added in 0.1.6 (D4, D11, D14, D17).

The robustness suite pins each defect. These cover the rules around them:
what is reported instead of attached, the opt-in for hidden files, and the
CLI wiring that scopes the scan to the job's own directory and run window.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from gpualert import artifacts
from gpualert.artifacts import describe_skipped, find_artifacts
from gpualert.cli import app

posix = pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and FIFOs")


def _names(found):
    return sorted(os.path.basename(a.path) for a in found)


def _before():
    return datetime.now() - timedelta(seconds=2)


@pytest.fixture()
def work(tmp_path, monkeypatch):
    w = tmp_path / "work"
    w.mkdir()
    monkeypatch.chdir(w)
    return w


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    return h


class TestSkippedReasons:
    @posix
    def test_other_owner_is_counted_not_named(self, work):
        start = _before()
        (work / "their_results.json").write_text("{}")
        skipped = {}
        with patch("os.geteuid", return_value=os.geteuid() + 1):
            assert find_artifacts(start, cwd=str(work), skipped=skipped) == []
        assert skipped == {artifacts.SKIP_OTHER_OWNER: ["their_results.json"]}
        lines = describe_skipped(skipped)
        assert lines == [
            "1 file(s) matching the artifact patterns were not attached because another "
            "user owns them."
        ]
        assert "their_results" not in lines[0]

    def test_too_large_is_named(self, work):
        start = _before()
        (work / "big.npy").write_bytes(b"0" * 2048)
        skipped = {}
        assert find_artifacts(start, cwd=str(work), max_single_mb=0.001, skipped=skipped) == []
        assert skipped[artifacts.SKIP_TOO_LARGE] == ["big.npy"]
        assert "big.npy" in describe_skipped(skipped, max_single_mb=0.001)[0]

    @posix
    def test_outward_symlink_is_reported(self, work, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("x")
        start = _before()
        (work / "results.txt").symlink_to(outside)
        skipped = {}
        assert find_artifacts(start, cwd=str(work), skipped=skipped) == []
        assert skipped[artifacts.SKIP_OUTSIDE_RUN_DIR] == ["results.txt"]

    @posix
    def test_fifo_is_reported(self, work):
        start = _before()
        os.mkfifo(work / "output.txt")
        skipped = {}
        assert find_artifacts(start, cwd=str(work), skipped=skipped) == []
        assert skipped[artifacts.SKIP_NOT_REGULAR] == ["output.txt"]

    @posix
    def test_symlink_inside_the_tree_to_a_fifo_is_rejected(self, work):
        start = _before()
        os.mkfifo(work / "pipe")
        (work / "metrics.json").symlink_to(work / "pipe")
        assert find_artifacts(start, cwd=str(work)) == []

    def test_long_lists_are_shortened(self):
        lines = describe_skipped({artifacts.SKIP_TOO_LARGE: [f"f{i}.npy" for i in range(8)]})
        assert lines[0].endswith("and 3 more.")

    def test_nothing_skipped_means_no_lines(self):
        assert describe_skipped({}) == []


class TestRunWindow:
    def test_file_inside_the_window_is_kept(self, work):
        start = _before()
        (work / "metrics.csv").write_text("loss\n0.1\n")
        end = datetime.now()
        assert _names(find_artifacts(start, cwd=str(work), end_time=end)) == ["metrics.csv"]

    def test_slack_covers_coarse_timestamps(self, work):
        start = datetime.now() - timedelta(seconds=10)
        f = work / "metrics.csv"
        f.write_text("x")
        end = datetime.now() - timedelta(seconds=1)
        # Written "after" end by less than the slack: still this job's.
        assert _names(find_artifacts(start, cwd=str(work), end_time=end)) == ["metrics.csv"]


class TestHidden:
    def test_hidden_file_is_skipped_by_default(self, work):
        start = _before()
        (work / ".results.json").write_text("{}")
        assert find_artifacts(start, cwd=str(work)) == []

    def test_hidden_file_is_kept_when_asked_for_by_name(self, work):
        start = _before()
        (work / ".results.json").write_text("{}")
        found = find_artifacts(start, cwd=str(work), patterns=[".results*"])
        assert _names(found) == [".results.json"]

    def test_hidden_directories_stay_skipped_even_when_asked(self, work):
        start = _before()
        (work / ".hydra").mkdir()
        (work / ".hydra" / ".config.json").write_text("{}")
        assert find_artifacts(start, cwd=str(work), patterns=[".*"]) == []

    def test_ordinary_output_directories_are_still_scanned(self, work):
        start = _before()
        for d in ("outputs", "runs/exp1", "lightning_logs"):
            (work / d).mkdir(parents=True)
            (work / d / "metrics.csv").write_text("x")
        assert len(find_artifacts(start, cwd=str(work))) == 3


class TestCliWiring:
    def test_scan_is_scoped_to_the_job_directory(self, home, work, monkeypatch):
        sub = work / "job"
        sub.mkdir()
        (work / "sibling_results.json").write_text("{}")
        script = "open('results.json', 'w').write('{}')"
        monkeypatch.chdir(sub)
        out = CliRunner().invoke(app, ["run", "--dry-run", "--", sys.executable, "-c", script])
        assert out.exit_code == 0, out.output
        assert "results.json" in out.output
        assert "sibling_results.json" not in out.output

    def test_oversized_files_are_named_in_the_report(self, home, work):
        script = (
            "open('results.json','w').write('{}');"
            "open('weights.npy','wb').write(b'0' * (26 * 1024 * 1024))"
        )
        out = CliRunner().invoke(app, ["run", "--dry-run", "--", sys.executable, "-c", script])
        assert out.exit_code == 0, out.output
        assert "larger than the 25 MB per-file limit: weights.npy" in out.output

    @posix
    def test_a_fifo_in_the_run_directory_cannot_hang_the_cli(self, home, work, tmp_path):
        # End to end in a real process, so a regression shows up as a
        # timeout instead of a hung test run.
        os.mkfifo(work / "metrics.json")
        env = dict(os.environ, HOME=str(home))
        code = (
            "import os; os.utime('metrics.json')\n"
            "open('results.csv','w').write('loss\\n0.5\\n')\n"
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from gpualert.cli import app; app(prog_name='gpualert')",
                "run",
                "--dry-run",
                "--",
                sys.executable,
                "-c",
                code,
            ],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "not regular files" in proc.stdout
        assert "loss=0.5000" in proc.stdout


class TestConfigKnobs:
    def _run(self, cfg, script):
        with patch("gpualert.cli.load_config", return_value=cfg):
            return CliRunner().invoke(app, ["run", "--dry-run", "--", sys.executable, "-c", script])

    def _cfg(self, **artifacts_overrides):
        from gpualert.config import GPUAlertConfig

        cfg = GPUAlertConfig()
        for key, value in artifacts_overrides.items():
            setattr(cfg.artifacts, key, value)
        return cfg

    def test_max_single_file_mb_is_honoured(self, home, work):
        out = self._run(
            self._cfg(max_single_file_mb=1), "open('w.npy','wb').write(b'0' * (2 * 1024 * 1024))"
        )
        assert "larger than the 1 MB per-file limit: w.npy" in out.output

    def test_scan_depth_is_honoured(self, home, work):
        script = (
            "import os; os.makedirs('a/b', exist_ok=True); "
            "open('a/b/results.json','w').write('{}'); open('top.csv','w').write('x')"
        )
        out = self._run(self._cfg(scan_depth=1), script)
        attach_line = next(line for line in out.output.splitlines() if line.startswith("Attach :"))
        assert "top.csv" in attach_line
        assert "results.json" not in attach_line

    def test_max_total_mb_is_honoured(self, home, work):
        # Random bytes, so the overflow zip cannot compress its way back
        # under the budget.
        script = (
            "import os; open('a.npy','wb').write(os.urandom(600_000)); "
            "open('b.npy','wb').write(os.urandom(600_000))"
        )
        out = self._run(self._cfg(max_total_mb=1), script)
        assert "over the email size budget" in out.output
