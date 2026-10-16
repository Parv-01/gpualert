"""What the artifact scanner is allowed to pick up, and what it must refuse.

Attachments leave the machine by email, so this is a data boundary rather
than a convenience. A file only qualifies when it is a regular file, owned
by the user running the wrapper, inside the run directory, and written
during the run. Anything else is either another person's data, the tool's
own state, or something that can make the wrapper hang.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from gpualert.artifacts import find_artifacts, prepare_attachments
from gpualert.config import EmailConfig, GPUAlertConfig, SMTPConfig
from gpualert.notifier.email_notifier import EmailNotifier
from gpualert.types import JobResult

pytestmark = [
    pytest.mark.robustness,
    pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and FIFOs"),
]


def _names(found):
    return {os.path.basename(a.path) for a in found}


def _just_before_now() -> datetime:
    # One second of slack so a filesystem with coarse timestamps cannot
    # round a fresh file below the start time.
    return datetime.now() - timedelta(seconds=1)


class TestOwnership:
    @pytest.mark.xfail(
        strict=True,
        reason="D4: files owned by another uid are attached, so a co-tenant's "
        "output in a shared directory is emailed to this user",
    )
    def test_file_owned_by_another_user_is_not_attached(self, workdir):
        start = _just_before_now()
        (workdir / "results.json").write_text("{}")
        someone_else = os.geteuid() + 1
        with patch("os.geteuid", return_value=someone_else):
            found = find_artifacts(start, cwd=str(workdir))
        assert "results.json" not in _names(found)

    @pytest.mark.skipif(
        not hasattr(os, "geteuid") or os.geteuid() != 0, reason="needs root to chown"
    )
    @pytest.mark.xfail(strict=True, reason="D4: same defect, with a real second uid")
    def test_real_chown_to_another_uid_is_not_attached(self, workdir):
        start = _just_before_now()
        theirs = workdir / "their_metrics.csv"
        theirs.write_text("a,b\n1,2\n")
        os.chown(theirs, 65534, 65534)
        mine = workdir / "my_metrics.csv"
        mine.write_text("a,b\n1,2\n")
        names = _names(find_artifacts(start, cwd=str(workdir)))
        assert "my_metrics.csv" in names
        assert "their_metrics.csv" not in names


class TestRunWindow:
    @pytest.mark.xfail(
        strict=True,
        reason="D4: there is no upper bound on mtime, so a file written after the "
        "job ended is claimed by it",
    )
    def test_file_written_after_the_job_ended_is_not_attached(self, workdir):
        start = datetime.now() - timedelta(seconds=30)
        end = datetime.now() - timedelta(seconds=20)
        late = workdir / "results.csv"
        late.write_text("late")
        found = find_artifacts(start, cwd=str(workdir), end_time=end)
        assert "results.csv" not in _names(found)


class TestFileTypes:
    @pytest.mark.xfail(
        strict=True,
        reason="D11: a FIFO named like an output passes the scan and later blocks "
        "the metrics reader forever, so the notification is never sent",
    )
    def test_fifo_named_like_an_output_is_ignored(self, workdir):
        start = _just_before_now()
        os.mkfifo(workdir / "metrics.json")
        found = find_artifacts(start, cwd=str(workdir))
        assert "metrics.json" not in _names(found)


class TestToolState:
    @pytest.mark.xfail(
        strict=True,
        reason="D14: a job started from $HOME attaches ~/.gpualert/logs, which "
        "holds every other job's logs",
    )
    def test_gpualert_state_is_never_scanned(self, isolated_home, monkeypatch):
        monkeypatch.chdir(isolated_home)
        start = _just_before_now()
        other_job = isolated_home / ".gpualert" / "logs" / "20260101_000000_deadbeef"
        other_job.mkdir(parents=True)
        (other_job / "combined.log").write_text("another job's log")
        found = find_artifacts(start, cwd=str(isolated_home))
        assert not any(".gpualert" in a.path for a in found)

    @pytest.mark.xfail(
        strict=True,
        reason="D14: hidden directories (.ssh, .aws, .config) are scanned like "
        "any other output directory",
    )
    def test_hidden_directories_are_not_scanned(self, workdir):
        start = _just_before_now()
        (workdir / ".secrets").mkdir()
        (workdir / ".secrets" / "token.json").write_text('{"token": "x"}')
        found = find_artifacts(start, cwd=str(workdir))
        assert "token.json" not in _names(found)


def _config() -> GPUAlertConfig:
    cfg = GPUAlertConfig()
    cfg.smtp = SMTPConfig(username="u@example.com", password="pw")
    cfg.email = EmailConfig(from_address="u@example.com", to_addresses=["u@example.com"])
    return cfg


def _sent_attachment_bodies(attachments):
    """Run the real email notifier against a stub SMTP session."""
    result = JobResult(command="train", job_id="x", start_time=datetime.now(), status="success")
    sent = []
    with patch.object(EmailNotifier, "_deliver", lambda self, msg, pw, ctx: sent.append(msg)):
        EmailNotifier(_config()).send(result, attachments)
    bodies = []
    for msg in sent:
        for part in msg.iter_attachments():
            bodies.append(part.get_payload(decode=True))
    return bodies


class TestReadTimeContainment:
    @pytest.mark.xfail(
        strict=True,
        reason="D15: attachments are re-opened by path at send time, so a file "
        "swapped for an outward symlink after the scan is followed",
    )
    def test_file_swapped_for_a_symlink_after_the_scan_is_not_sent(self, workdir, tmp_path):
        secret = tmp_path / "outside" / "id_rsa"
        secret.parent.mkdir()
        secret.write_bytes(b"PRIVATE KEY MATERIAL")

        start = _just_before_now()
        artifact = workdir / "results.txt"
        artifact.write_text("ok")
        found = find_artifacts(start, cwd=str(workdir))
        assert "results.txt" in _names(found)

        time.sleep(0.01)
        artifact.unlink()
        artifact.symlink_to(secret)

        attach, _ = prepare_attachments(found, log_files=[], job_failed=False)
        bodies = _sent_attachment_bodies(attach)
        assert not any(b"PRIVATE KEY" in (b or b"") for b in bodies)
