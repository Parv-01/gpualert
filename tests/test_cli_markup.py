"""Text from the job never reaches Rich as markup (D23).

Rich parses [tags] in console output. A file name, command argument or
notifier message containing something like "[/x]" raised MarkupError,
after the job had finished, and turned the job's exit status into 1. In
the banner it fired before the job started, so the job never ran. Hydra
list overrides such as data.dirs=[/scratch/a,/scratch/b] look exactly
like that.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

from typer.testing import CliRunner

from gpualert.cli import app
from gpualert.types import NotificationResult

runner = CliRunner()


def test_markup_in_the_command_does_not_stop_the_job(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "ran"
    code = f"open({str(marker)!r}, 'w').write('x'); raise SystemExit(4)"
    out = runner.invoke(
        app, ["run", "--no-notify", "--", sys.executable, "-c", code, "data.dirs=[/scratch/a]"]
    )
    assert marker.exists(), out.output
    assert out.exit_code == 4
    assert "[/scratch/a]" in out.output


def test_markup_in_a_notifier_message_keeps_the_exit_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    msg = "Email sent. Attached: ['[/x]results.csv', '[bold]metrics.csv']"
    with patch("gpualert.cli.get_notifier") as nf:
        nf.return_value.send.return_value = NotificationResult(
            success=True, notifier_type="email", message=msg
        )
        out = runner.invoke(app, ["run", "--", sys.executable, "-c", "raise SystemExit(4)"])
    assert out.exit_code == 4, out.output
    assert "[/x]results.csv" in out.output


def test_markup_in_a_failed_notification_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("gpualert.cli.get_notifier") as nf:
        nf.return_value.send.return_value = NotificationResult(
            success=False, notifier_type="email", message="refused [/oops]"
        )
        out = runner.invoke(app, ["run", "--", sys.executable, "-c", "raise SystemExit(2)"])
    assert out.exit_code == 2, out.output


def test_markup_in_an_output_path(tmp_path, monkeypatch):
    # A directory "out[" holding "b]big.npy" gives the relative path
    # "out[/b]big.npy", which Rich would read as a closing tag.
    monkeypatch.chdir(tmp_path)
    code = (
        "import os; os.makedirs('out[', exist_ok=True); "
        "open('out[/b]big.npy','wb').write(b'0'*(26*1024*1024)); "
        "open('[red]results.csv','w').write('a')"
    )
    out = runner.invoke(app, ["run", "--dry-run", "--", sys.executable, "-c", code])
    assert out.exit_code == 0, out.output
    assert "out[/b]big.npy" in out.output
