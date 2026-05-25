"""Day 3 tests — artifact scanner."""

from __future__ import annotations

import time
from datetime import datetime, timedelta


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
        (tmp_path / "huge.csv").write_bytes(b"x" * (30 * 1024 * 1024))
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
        found = find_artifacts(start, cwd=str(tmp_path), patterns=["*.csv"], max_depth=2)
        names = [a.filename() for a in found]
        assert "surface.csv" in names
        assert "buried.csv" not in names

    def test_prepare_attachments_always_includes_logs_on_failure(self, tmp_path):
        from gpualert.artifacts import prepare_attachments

        log_path = str(tmp_path / "stderr.log")
        (tmp_path / "stderr.log").write_text("error output")
        to_attach, _ = prepare_attachments(
            artifacts=[],
            log_files=[log_path],
            job_failed=True,
            attach_logs=False,
        )
        assert log_path in to_attach

    def test_prepare_attachments_logs_skipped_on_success_when_disabled(self, tmp_path):
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

    def test_tool_output_dirs_are_excluded(self, tmp_path):
        from gpualert.artifacts import find_artifacts

        start = datetime.now() - timedelta(seconds=1)
        (tmp_path / "metrics.csv").write_text("real")
        for excluded in (".venv", "node_modules", ".pytest_cache", "__pycache__"):
            d = tmp_path / excluded
            d.mkdir()
            (d / "noise.csv").write_text("noise")
        found = find_artifacts(start, cwd=str(tmp_path), patterns=["*.csv"])
        names = [a.filename() for a in found]
        assert "metrics.csv" in names
        assert "noise.csv" not in names
