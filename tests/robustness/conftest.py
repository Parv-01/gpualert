"""Shared fixtures for the robustness harness."""

from __future__ import annotations

from pathlib import Path

import pytest

TREE = str(Path(__file__).resolve().parents[2])


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Give each test its own ~/.gpualert so log dirs never collide."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    """A clean working directory, since artifact scanning starts at cwd."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


@pytest.fixture()
def tree() -> str:
    """Path to the package under test, for out-of-process wrappers."""
    return TREE


@pytest.fixture(autouse=True)
def _no_stray_env(monkeypatch):
    """Distributed detection reads the environment; keep tests deterministic."""
    for name in (
        "WORLD_SIZE",
        "LOCAL_WORLD_SIZE",
        "SLURM_NNODES",
        "SLURM_NTASKS",
        "SLURM_JOB_ID",
        "SLURM_JOB_NODELIST",
        "TORCHELASTIC_RUN_ID",
        "TORCHELASTIC_ERROR_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def pytest_configure(config):
    config.addinivalue_line("markers", "robustness: fault-injection and contract tests")
    config.addinivalue_line("markers", "slow: takes more than a few seconds")
