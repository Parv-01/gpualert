"""Where logs go when the usual directory cannot be used (D5, D13).

run_job promises a JobResult for every input. A home directory that is
read-only, over quota or not a directory at all is ordinary on a cluster,
so each of those has to end in a result with the child's real exit code
and a note saying where the logs went.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path

import pytest

from gpualert import launcher, log_manager
from gpualert.launcher import run_job


def _py(src):
    return [sys.executable, "-c", src]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    monkeypatch.delenv(log_manager.LOG_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


@pytest.fixture()
def private_tmp(tmp_path, monkeypatch):
    t = tmp_path / "systmp"
    t.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(t))
    return t


class TestOverride:
    def test_env_var_moves_the_logs(self, home, tmp_path, monkeypatch):
        target = tmp_path / "scratch" / "logs"
        monkeypatch.setenv(log_manager.LOG_DIR_ENV, str(target))
        result = run_job(_py("print('hi')"))
        assert Path(result.combined_log_path).parent.parent == target.resolve()
        assert result.notes == []
        assert not (home / ".gpualert").exists()

    def test_label_shows_the_override_without_touching_disk(self, tmp_path, monkeypatch):
        target = tmp_path / "never-created"
        monkeypatch.setenv(log_manager.LOG_DIR_ENV, str(target))
        assert log_manager.log_dir_label() == str(target)
        assert not target.exists()

    def test_override_keeps_its_own_permissions(self, tmp_path, monkeypatch):
        shared = tmp_path / "group-logs"
        shared.mkdir(mode=0o770)
        os.chmod(shared, 0o770)
        monkeypatch.setenv(log_manager.LOG_DIR_ENV, str(shared))
        log_manager.get_log_dir()
        assert stat.S_IMODE(shared.stat().st_mode) == 0o770


class TestFallbackToTemp:
    def test_home_that_is_a_file_falls_back(self, tmp_path, monkeypatch, private_tmp):
        not_a_dir = tmp_path / "file-home"
        not_a_dir.write_text("")
        monkeypatch.setenv("HOME", str(not_a_dir))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: not_a_dir))
        monkeypatch.delenv(log_manager.LOG_DIR_ENV, raising=False)
        result = run_job(_py("import sys; print('out'); sys.exit(7)"))
        assert result.exit_code == 7
        assert result.status == "failed"
        assert len(result.log_files()) == 3
        assert Path(result.combined_log_path).is_relative_to(private_tmp)
        assert "out" in Path(result.stdout_log_path).read_text()
        assert any("could not be used" in n and str(private_tmp) in n for n in result.notes)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
    def test_fallback_directory_is_private(self, tmp_path, monkeypatch, private_tmp):
        not_a_dir = tmp_path / "file-home"
        not_a_dir.write_text("")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: not_a_dir))
        monkeypatch.delenv(log_manager.LOG_DIR_ENV, raising=False)
        result = run_job(_py("pass"))
        mode = stat.S_IMODE(Path(result.combined_log_path).parent.stat().st_mode)
        assert mode == 0o700

    def test_two_fallback_jobs_never_share_a_directory(self, tmp_path, monkeypatch, private_tmp):
        not_a_dir = tmp_path / "file-home"
        not_a_dir.write_text("")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: not_a_dir))
        monkeypatch.delenv(log_manager.LOG_DIR_ENV, raising=False)
        a = run_job(_py("pass"))
        b = run_job(_py("pass"))
        assert Path(a.combined_log_path).parent != Path(b.combined_log_path).parent

    def test_home_over_quota_falls_back(self, home, monkeypatch, private_tmp):
        # mkdir succeeds but writing fails, which is how a full quota looks.
        real_open = open

        def quota_open(path, mode="r", *args, **kwargs):
            if str(path).startswith(str(home)) and "w" in mode:
                raise OSError(errno.EDQUOT, "Disk quota exceeded")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(log_manager, "open", quota_open, raising=False)
        result = run_job(_py("import sys; sys.exit(3)"))
        assert result.exit_code == 3
        assert Path(result.combined_log_path).is_relative_to(private_tmp)
        assert any("quota" in n.lower() for n in result.notes)


class TestMemoryOnly:
    def test_nothing_writable_still_reports(self, home, monkeypatch):
        def refuse(*_a, **_k):
            raise PermissionError(errno.EACCES, "nope")

        monkeypatch.setattr(launcher, "create_job_log_dir", refuse)
        monkeypatch.setattr(launcher, "create_fallback_log_dir", refuse)
        result = run_job(_py("import sys; print('kept in memory'); sys.exit(5)"))
        assert result.exit_code == 5
        assert result.status == "failed"
        assert result.log_files() == []
        assert "kept in memory" in result.stdout_tail
        assert any("memory only" in n for n in result.notes)

    def test_missing_command_with_no_logs_does_not_raise(self, home, monkeypatch):
        def refuse(*_a, **_k):
            raise OSError("read-only file system")

        monkeypatch.setattr(launcher, "create_job_log_dir", refuse)
        monkeypatch.setattr(launcher, "create_fallback_log_dir", refuse)
        result = run_job(["definitely-not-a-binary-xyz"])
        assert result.exit_code == 127


@pytest.mark.skipif(os.name != "posix", reason="Windows cannot remove a busy cwd")
class TestDeletedWorkingDirectory:
    def test_header_records_that_cwd_was_unavailable(self, home, tmp_path, monkeypatch):
        gone = tmp_path / "gone"
        gone.mkdir()
        monkeypatch.chdir(gone)
        gone.rmdir()
        result = run_job(_py("print('ran')"))
        assert result.status == "success"
        assert result.cwd == ""
        assert "CWD     : <unavailable" in Path(result.combined_log_path).read_text()

    def test_relative_cwd_argument_with_deleted_cwd(self, home, tmp_path, monkeypatch):
        gone = tmp_path / "gone2"
        gone.mkdir()
        monkeypatch.chdir(gone)
        gone.rmdir()
        result = run_job(_py("print('ran')"), cwd="relative/dir")
        assert result.is_failed()


def test_cwd_is_recorded_absolute(home, tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    result = run_job(_py("pass"), cwd=str(work))
    assert result.cwd == str(work)
