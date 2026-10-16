"""Artifact scanning must not follow symlinks out of the run directory (0.1.6+).

A file named like an artifact (results.json) that links to something outside
the working directory, such as ~/.ssh/id_rsa or a credentials file, must not
be attached. Links that stay inside the run directory are still allowed.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

from gpualert.artifacts import find_artifacts

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")


def _names(found):
    return sorted(os.path.basename(a.path) for a in found)


def test_symlink_outside_run_dir_is_skipped(tmp_path):
    run_dir = tmp_path / "run"
    secret_dir = tmp_path / "secret"
    run_dir.mkdir()
    secret_dir.mkdir()
    secret = secret_dir / "token.txt"
    secret.write_text("do not send")
    (run_dir / "results.json").symlink_to(secret)
    (run_dir / "metrics.json").write_text("{}")

    found = find_artifacts(
        start_time=datetime.now() - timedelta(minutes=1),
        cwd=str(run_dir),
        patterns=["*.json"],
    )
    paths = [a.path for a in found]
    assert str(secret.resolve()) not in paths
    assert _names(found) == ["metrics.json"]


def test_symlink_inside_run_dir_is_kept(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "out").mkdir(parents=True)
    target = run_dir / "out" / "final.json"
    target.write_text("{}")
    (run_dir / "latest.json").symlink_to(target)

    found = find_artifacts(
        start_time=datetime.now() - timedelta(minutes=1),
        cwd=str(run_dir),
        patterns=["*.json"],
    )
    assert str(target.resolve()) in [a.path for a in found]


def test_broken_symlink_is_ignored(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "gone.json").symlink_to(tmp_path / "missing.json")

    found = find_artifacts(
        start_time=datetime.now() - timedelta(minutes=1),
        cwd=str(run_dir),
        patterns=["*.json"],
    )
    assert found == []
