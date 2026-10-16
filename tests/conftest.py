"""Suite-wide isolation.

Several CLI tests invoke `gpualert run` through Typer's runner without
setting HOME, which wrote job logs into the developer's real
~/.gpualert/logs and read their real config. Every test now starts with a
private home and no GPUALERT_* variables; a test that sets its own still
wins, because its monkeypatch runs after this fixture. Only the
environment is changed: Path.home() follows HOME (USERPROFILE on Windows),
and tests that set HOME themselves rely on exactly that.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _private_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in ("GPUALERT_LOG_DIR", "GPUALERT_EMAIL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    return home
