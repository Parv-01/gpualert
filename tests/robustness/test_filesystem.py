"""Filesystems that refuse to cooperate.

`run_job` documents that it never raises. On a cluster the home directory
is frequently over quota, read-only, or on an NFS mount that has gone
stale, and those are exactly the moments when the operator most needs a
result rather than a traceback.
"""

from __future__ import annotations

import errno
import os
import stat

import pytest

from gpualert.launcher import run_job
from gpualert.log_manager import get_tail, write_to_log
from tests.robustness import faults

pytestmark = pytest.mark.robustness

# root ignores permission bits, so chmod 0500 does not make a directory
# unwritable for it and the chmod-based tests would pass for the wrong
# reason. CI runs as an ordinary user; the not-a-directory test below
# covers the same path for root.
requires_permission_bits = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses permission bits",
)


class TestUnwritableHome:
    @requires_permission_bits
    @pytest.mark.xfail(
        strict=True,
        reason="D5: create_job_log_dir runs outside the try block, so an "
        "unwritable home makes run_job raise PermissionError",
    )
    def test_unwritable_home_still_returns_a_result(self, tmp_path, monkeypatch):
        locked = tmp_path / "locked_home"
        locked.mkdir()
        locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
        monkeypatch.setenv("HOME", str(locked))
        from pathlib import Path

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: locked))
        try:
            result = run_job(faults.quick())
            assert result is not None
            assert result.status in ("success", "failed", "error")
        finally:
            locked.chmod(stat.S_IRWXU)

    @requires_permission_bits
    @pytest.mark.xfail(
        strict=True,
        reason="D5: the same path raises when the job itself would have succeeded",
    )
    def test_unwritable_home_preserves_the_child_exit_code(self, tmp_path, monkeypatch):
        locked = tmp_path / "locked_home2"
        locked.mkdir()
        locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
        from pathlib import Path

        monkeypatch.setenv("HOME", str(locked))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: locked))
        try:
            result = run_job(faults.program("import sys; sys.exit(7)"))
            assert result.exit_code == 7
        finally:
            locked.chmod(stat.S_IRWXU)

    @pytest.mark.xfail(
        strict=True,
        reason="D5: a HOME that is a file makes mkdir raise NotADirectoryError, "
        "which escapes run_job for any user, root included",
    )
    def test_home_that_is_not_a_directory_still_returns_a_result(self, tmp_path, monkeypatch):
        not_a_dir = tmp_path / "home_is_a_file"
        not_a_dir.write_text("")
        from pathlib import Path

        monkeypatch.setenv("HOME", str(not_a_dir))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: not_a_dir))
        result = run_job(faults.program("import sys; sys.exit(7)"))
        assert result.exit_code == 7


class TestDiskFull:
    def test_write_to_log_swallows_enospc(self, tmp_path, monkeypatch):
        """Logging failures must never surface as job failures."""
        target = tmp_path / "combined.log"
        target.write_text("")

        real_open = open

        def full_disk(path, *args, **kwargs):
            if str(path) == str(target):
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", full_disk)
        write_to_log(str(target), "this write cannot land\n")  # must not raise

    def test_job_completes_when_the_log_dir_vanishes_mid_run(self, isolated_home, workdir):
        result = run_job(faults.quick())
        log_dir = os.path.dirname(result.combined_log_path)
        for name in os.listdir(log_dir):
            os.remove(os.path.join(log_dir, name))
        os.rmdir(log_dir)

        assert get_tail(result.combined_log_path, 10) == ""
        assert result.log_files() == []
        assert result.status == "success"


class TestDeletedWorkingDirectory:
    @pytest.mark.skipif(os.name != "posix", reason="Windows cannot remove a busy cwd")
    @pytest.mark.xfail(
        strict=True,
        reason="D13: the log header calls os.getcwd(), which raises once the "
        "working directory has been removed, so run_job raises",
    )
    def test_run_from_a_deleted_directory_returns_a_result(
        self, isolated_home, tmp_path, monkeypatch
    ):
        gone = tmp_path / "gone"
        gone.mkdir()
        monkeypatch.chdir(gone)
        gone.rmdir()
        result = run_job(faults.program("import sys; sys.exit(4)"))
        assert result.exit_code == 4


class TestDegradedReads:
    def test_tail_of_a_directory_is_empty(self, tmp_path):
        assert get_tail(str(tmp_path), 10) == ""

    def test_tail_of_a_binary_file_does_not_raise(self, tmp_path):
        blob = tmp_path / "blob.log"
        blob.write_bytes(bytes(range(256)) * 100)
        assert isinstance(get_tail(str(blob), 5), str)

    def test_log_paths_are_absolute(self, isolated_home, workdir):
        result = run_job(faults.quick())
        for path in result.log_files():
            assert os.path.isabs(path)
