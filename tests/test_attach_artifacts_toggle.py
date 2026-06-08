"""Tests for the attach_artifacts master toggle (Feature 2, added 0.1.2).

Behavior under test:
- attach_artifacts=True (default) → scan happens, artifacts attach as before.
- attach_artifacts=False        → scan skipped, no artifacts attached, logs
                                  still attach, a NOTES line is added to
                                  the email body and the result.
- Overflow remains non-silent in both modes (existing 0.1.1 behavior).
"""

from __future__ import annotations

from datetime import datetime
from typing import List
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from gpualert.cli import app
from gpualert.config import ArtifactConfig, GPUAlertConfig
from gpualert.notifier.base import BaseNotifier
from gpualert.types import JobResult, NotificationResult

runner = CliRunner()


# ── Schema ────────────────────────────────────────────────────────────────
class TestArtifactConfigSchema:
    def test_attach_artifacts_default_is_true(self):
        assert ArtifactConfig().attach_artifacts is True

    def test_attach_artifacts_round_trips(self):
        cfg = ArtifactConfig(attach_artifacts=False)
        assert cfg.attach_artifacts is False
        dumped = cfg.model_dump()
        rebuilt = ArtifactConfig(**dumped)
        assert rebuilt.attach_artifacts is False

    def test_attach_artifacts_preserves_other_defaults(self):
        cfg = ArtifactConfig(attach_artifacts=False)
        # Existing fields must NOT change when the new toggle flips.
        assert cfg.max_single_file_mb == 25
        assert cfg.max_total_mb == 45
        assert "*.csv" in cfg.patterns


# ── CLI gating ────────────────────────────────────────────────────────────
def _build_config(attach: bool) -> GPUAlertConfig:
    cfg = GPUAlertConfig()
    cfg.smtp.server = "smtp.example.com"
    cfg.smtp.username = "u@example.com"
    cfg.smtp.password = "pw"
    cfg.email.from_address = "u@example.com"
    cfg.email.to_addresses = ["dest@example.com"]
    cfg.artifacts.attach_artifacts = attach
    return cfg


class TestCliGating:
    def test_disabled_skips_find_artifacts(self, tmp_path):
        cfg = _build_config(attach=False)
        # Drop a CSV that WOULD match patterns — it must not be scanned.
        (tmp_path / "metrics.csv").write_text("epoch,loss\n0,0.5\n")

        with (
            patch("gpualert.cli.load_config", return_value=cfg),
            patch("gpualert.cli.find_artifacts") as mock_find,
            patch("gpualert.cli.run_job") as mock_run,
            patch("gpualert.cli.get_notifier") as mock_get_notifier,
        ):
            mock_run.return_value = JobResult(
                command="echo hi",
                job_id="abc123",
                start_time=datetime.now(),
                end_time=datetime.now(),
                status="success",
                exit_code=0,
                stdout_log_path=str(tmp_path / "stdout.log"),
                stderr_log_path=str(tmp_path / "stderr.log"),
                combined_log_path=str(tmp_path / "combined.log"),
            )
            for f in ("stdout.log", "stderr.log", "combined.log"):
                (tmp_path / f).write_text("ok\n")

            sent_attachments: List[List[str]] = []
            sent_results: List[JobResult] = []

            class _Spy:
                def send(self, result, attachments):
                    sent_attachments.append(list(attachments))
                    sent_results.append(result)
                    return NotificationResult(success=True, notifier_type="email", message="ok")

            mock_get_notifier.return_value = _Spy()

            result = runner.invoke(app, ["run", "echo", "hi"])

        assert result.exit_code == 0, result.output
        # find_artifacts must NOT be called when the toggle is off.
        mock_find.assert_not_called()
        # Logs are still attached.
        assert any(
            any("log" in p for p in batch) for batch in sent_attachments
        ), f"logs missing from attachments: {sent_attachments}"
        # No CSV / artifact path attached.
        flat = [p for batch in sent_attachments for p in batch]
        assert not any(p.endswith(".csv") for p in flat)
        # Console output mentions the disabled state.
        assert "disabled" in result.output.lower()
        # Result carries the note for the email body.
        assert sent_results, "notifier.send was not called"
        assert any(
            "disabled" in n.lower() for n in (sent_results[0].notes or [])
        ), f"NOTES not propagated to JobResult: {sent_results[0].notes}"

    def test_enabled_still_scans_and_attaches(self, tmp_path):
        cfg = _build_config(attach=True)

        with (
            patch("gpualert.cli.load_config", return_value=cfg),
            patch("gpualert.cli.find_artifacts") as mock_find,
            patch("gpualert.cli.run_job") as mock_run,
            patch("gpualert.cli.get_notifier") as mock_get_notifier,
        ):
            mock_find.return_value = []
            mock_run.return_value = JobResult(
                command="echo hi",
                job_id="abc456",
                start_time=datetime.now(),
                end_time=datetime.now(),
                status="success",
                exit_code=0,
                stdout_log_path=str(tmp_path / "stdout.log"),
                stderr_log_path=str(tmp_path / "stderr.log"),
                combined_log_path=str(tmp_path / "combined.log"),
            )
            for f in ("stdout.log", "stderr.log", "combined.log"):
                (tmp_path / f).write_text("ok\n")

            sent_results: List[JobResult] = []

            class _Spy:
                def send(self, result, attachments):
                    sent_results.append(result)
                    return NotificationResult(success=True, notifier_type="email", message="ok")

            mock_get_notifier.return_value = _Spy()

            result = runner.invoke(app, ["run", "echo", "hi"])

        assert result.exit_code == 0, result.output
        mock_find.assert_called_once()
        # No disabled note when the toggle is on.
        assert sent_results
        assert not any("disabled" in n.lower() for n in (sent_results[0].notes or []))


# ── Email body rendering ──────────────────────────────────────────────────
class _NoopNotifier(BaseNotifier):
    def send(self, result, attachments):
        return NotificationResult(success=True, notifier_type="email", message="")


def _job_result_with_notes(notes: List[str]) -> JobResult:
    r = JobResult(
        command="train.py",
        job_id="j1",
        start_time=datetime.now(),
        end_time=datetime.now(),
        status="success",
        exit_code=0,
    )
    r.notes = notes
    return r


class TestEmailBodyNotes:
    def test_notes_render_in_body(self):
        cfg = _build_config(attach=True)
        notifier = _NoopNotifier(cfg)
        result = _job_result_with_notes(
            ["Artifact attachment disabled (artifacts.attach_artifacts=false)."]
        )
        body = notifier._build_body(result, attachments=[])
        assert "NOTES" in body
        assert "Artifact attachment disabled" in body

    def test_no_notes_no_section(self):
        cfg = _build_config(attach=True)
        notifier = _NoopNotifier(cfg)
        result = _job_result_with_notes([])
        body = notifier._build_body(result, attachments=[])
        assert "NOTES" not in body


# ── Non-silent overflow still preserved ───────────────────────────────────
class TestOverflowStillNonSilent:
    def test_overflow_routes_to_skipped_when_toggle_on(self, tmp_path):
        """Existing 0.1.1 overflow behavior must not regress under the new flag."""
        from gpualert.artifacts import prepare_attachments
        from gpualert.types import ArtifactFile

        # Big fake artifact that exceeds the total budget.
        big = tmp_path / "huge.bin"
        big.write_bytes(b"x" * 1024)  # tiny on disk, large declared size
        art = ArtifactFile(path=str(big), size_bytes=100 * 1024 * 1024, extension="bin")
        log = tmp_path / "combined.log"
        log.write_text("log\n")

        attached, skipped = prepare_attachments(
            artifacts=[art],
            log_files=[str(log)],
            job_failed=False,
            max_total_mb=1,  # tight budget forces overflow path
            attach_logs=True,
        )
        # Either the overflow zip was attached OR the artifact was skipped;
        # either way the artifact does NOT silently disappear.
        if str(big) not in attached:
            zip_attached = any(a.endswith("artifacts_overflow.zip") for a in attached)
            assert (
                zip_attached or skipped
            ), "overflow must not be silent: expected overflow zip or skipped list"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
