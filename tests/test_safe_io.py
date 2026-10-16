"""Read-time checks on attachments and metric files (D15, D16)."""

from __future__ import annotations

import os
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from gpualert.artifacts import compress_artifacts, prepare_attachments
from gpualert.config import EmailConfig, GPUAlertConfig, SMTPConfig
from gpualert.metrics import extract_from_json, extract_metrics
from gpualert.notifier.email_notifier import EmailNotifier
from gpualert.safe_io import UnsafeFileError, open_verified, read_verified, resolve_roots
from gpualert.types import ArtifactFile, JobResult

posix = pytest.mark.skipif(os.name != "posix", reason="POSIX file semantics")


def _finishes(fn, seconds=5.0):
    done = threading.Event()
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            box["error"] = exc
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(seconds), "call blocked"
    if "error" in box:
        raise box["error"]
    return box.get("value")


class TestOpenVerified:
    def test_regular_own_file_opens(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"data")
        with open_verified(str(f)) as fh:
            assert fh.read() == b"data"

    @posix
    def test_symlink_as_last_component_is_refused(self, tmp_path):
        target = tmp_path / "t.txt"
        target.write_text("x")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        with pytest.raises(UnsafeFileError, match="symlink"):
            open_verified(str(link))

    @posix
    def test_fifo_is_refused_without_blocking(self, tmp_path):
        fifo = tmp_path / "p"
        os.mkfifo(fifo)
        with pytest.raises(UnsafeFileError, match="not a regular file"):
            _finishes(lambda: open_verified(str(fifo)))

    @posix
    def test_directory_is_refused(self, tmp_path):
        with pytest.raises(OSError):
            open_verified(str(tmp_path))

    @posix
    def test_other_owner_is_refused(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        with patch("os.geteuid", return_value=os.geteuid() + 1):
            with pytest.raises(UnsafeFileError, match="another user"):
                open_verified(str(f))
        with patch("os.geteuid", return_value=os.geteuid() + 1):
            open_verified(str(f), check_owner=False).close()

    def test_roots_admit_files_inside(self, tmp_path):
        f = tmp_path / "run" / "a.txt"
        f.parent.mkdir()
        f.write_text("x")
        open_verified(str(f), roots=resolve_roots([str(tmp_path / "run")])).close()

    def test_roots_refuse_files_outside(self, tmp_path):
        (tmp_path / "run").mkdir()
        f = tmp_path / "elsewhere.txt"
        f.write_text("x")
        with pytest.raises(UnsafeFileError, match="outside"):
            open_verified(str(f), roots=resolve_roots([str(tmp_path / "run")]))

    def test_root_prefix_is_not_enough(self, tmp_path):
        # /work/run2 must not count as inside /work/run.
        (tmp_path / "run").mkdir()
        f = tmp_path / "run2" / "a.txt"
        f.parent.mkdir()
        f.write_text("x")
        with pytest.raises(UnsafeFileError):
            open_verified(str(f), roots=resolve_roots([str(tmp_path / "run")]))

    @posix
    def test_intermediate_directory_swapped_for_a_symlink_is_refused(self, tmp_path):
        run = tmp_path / "run"
        (run / "outputs").mkdir(parents=True)
        path = run / "outputs" / "results.json"
        path.write_text("{}")
        roots = resolve_roots([str(run)])
        outside = tmp_path / "secret"
        outside.mkdir()
        (outside / "results.json").write_text("SECRET")
        (run / "outputs" / "results.json").unlink()
        (run / "outputs").rmdir()
        (run / "outputs").symlink_to(outside)
        with pytest.raises(UnsafeFileError, match="outside"):
            open_verified(str(path), roots=roots)

    def test_read_verified_caps_size(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"x" * 101)
        assert read_verified(str(f), 101) == b"x" * 101
        with pytest.raises(UnsafeFileError, match="larger"):
            read_verified(str(f), 100)

    def test_resolve_roots_skips_empty(self, tmp_path):
        assert resolve_roots(["", str(tmp_path)]) == [os.path.realpath(tmp_path)]


def _config():
    cfg = GPUAlertConfig()
    cfg.smtp = SMTPConfig(username="u@example.com", password="pw")
    cfg.email = EmailConfig(from_address="u@example.com", to_addresses=["u@example.com"])
    return cfg


def _send(result, attachments):
    sent = []
    with patch.object(EmailNotifier, "_deliver", lambda self, m, pw, ctx: sent.append(m)):
        note = EmailNotifier(_config()).send(result, attachments)
    names = [p.get_filename() for m in sent for p in m.iter_attachments()]
    return note, names


class TestNotifier:
    def test_files_outside_run_and_log_dirs_are_refused_and_reported(self, tmp_path):
        run = tmp_path / "run"
        logs = tmp_path / "logs"
        run.mkdir()
        logs.mkdir()
        log = logs / "combined.log"
        log.write_text("log")
        inside = run / "metrics.csv"
        inside.write_text("a")
        outside = tmp_path / "other.csv"
        outside.write_text("b")
        result = JobResult(
            command="c",
            job_id="j",
            start_time=datetime.now(),
            status="success",
            combined_log_path=str(log),
            cwd=str(run),
        )
        note, names = _send(result, [str(log), str(inside), str(outside)])
        assert note.success
        assert names == ["combined.log", "metrics.csv"]
        assert "refused" in note.message and "other.csv" in note.message

    def test_without_a_cwd_any_own_regular_file_is_allowed(self, tmp_path):
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF")
        result = JobResult(command="c", job_id="j", start_time=datetime.now(), status="success")
        note, names = _send(result, [str(f)])
        assert names == ["report.pdf"]

    @posix
    def test_fifo_attachment_cannot_hang_the_notifier(self, tmp_path):
        fifo = tmp_path / "output.txt"
        os.mkfifo(fifo)
        result = JobResult(command="c", job_id="j", start_time=datetime.now(), status="success")
        note, names = _finishes(lambda: _send(result, [str(fifo)]))
        assert names == []


class TestOverflowZip:
    def test_duplicate_basenames_get_distinct_members(self, tmp_path):
        a = tmp_path / "a" / "metrics.csv"
        b = tmp_path / "b" / "metrics.csv"
        for p, body in ((a, "1"), (b, "2")):
            p.parent.mkdir()
            p.write_text(body)
        out = compress_artifacts([str(a), str(b)], str(tmp_path / "o.zip"))
        with zipfile.ZipFile(out) as zf:
            assert sorted(zf.namelist()) == ["metrics.csv", "metrics_2.csv"]
            assert sorted(zf.read(n).decode() for n in zf.namelist()) == ["1", "2"]

    @posix
    def test_swapped_symlink_is_left_out_of_the_zip(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("SECRET")
        f = tmp_path / "run" / "results.txt"
        f.parent.mkdir()
        f.symlink_to(secret)
        out = compress_artifacts([str(f)], str(tmp_path / "o.zip"))
        with zipfile.ZipFile(out) as zf:
            assert zf.namelist() == []

    def test_no_log_dir_means_overflow_is_skipped_not_zipped_into_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        big = tmp_path / "w.npy"
        big.write_bytes(b"0" * 4096)
        art = ArtifactFile(path=str(big), size_bytes=4096, extension="npy")
        attach, skipped = prepare_attachments(
            [art], log_files=[], job_failed=True, max_total_mb=0.001
        )
        assert attach == []
        assert skipped == [str(big)]
        assert sorted(os.listdir(tmp_path)) == ["w.npy"]


class TestMetricsReads:
    @posix
    def test_fifo_metrics_file_returns_empty_quickly(self, tmp_path):
        fifo = tmp_path / "metrics.json"
        os.mkfifo(fifo)
        assert _finishes(lambda: extract_from_json(fifo)) == {}
        assert _finishes(lambda: extract_metrics([str(fifo)])) == {}

    @posix
    def test_symlinked_metrics_file_is_not_read(self, tmp_path):
        real = tmp_path / "real.json"
        real.write_text('{"val_loss": 0.5}')
        link = tmp_path / "metrics.json"
        link.symlink_to(real)
        assert extract_metrics([str(link)]) == {}
        assert extract_metrics([str(real)]) == {"val_loss": 0.5}

    def test_csv_and_tsv_still_parse(self, tmp_path):
        c = tmp_path / "m.csv"
        c.write_text("epoch,val_acc\n1,0.5\n2,0.9\n")
        t = tmp_path / "m.tsv"
        t.write_text("epoch\tf1\n1\t0.7\n")
        assert extract_metrics([str(c), str(t)]) == {"val_acc": 0.9, "f1": 0.7}

    def test_unreadable_path_is_empty(self, tmp_path):
        assert extract_metrics([str(Path(tmp_path) / "nope.json")]) == {}
