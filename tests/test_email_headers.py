"""Control characters never reach an email header (D21).

A command with a newline in it (any multi-line `python -c` script) made
EmailMessage raise on the Subject, so the whole notification failed and
the user heard nothing about the job.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import patch

import pytest

from gpualert.config import EmailConfig, GPUAlertConfig, SMTPConfig
from gpualert.notifier.base import filename_safe, header_safe
from gpualert.notifier.email_notifier import EmailNotifier
from gpualert.types import JobResult


def _cfg():
    cfg = GPUAlertConfig()
    cfg.smtp = SMTPConfig(username="u@example.com", password="pw")
    cfg.email = EmailConfig(from_address="u@example.com", to_addresses=["u@example.com"])
    return cfg


def _send(command, attachments=()):
    sent = []
    result = JobResult(command=command, job_id="j", start_time=datetime.now(), status="success")
    with patch.object(EmailNotifier, "_deliver", lambda self, m, pw, ctx: sent.append(m)):
        note = EmailNotifier(_cfg()).send(result, list(attachments))
    return note, sent


def test_multiline_command_still_sends():
    note, sent = _send("python -c import os\nprint(os.getcwd())")
    assert note.success, note.message
    assert "\n" not in sent[0]["Subject"]
    assert "import os print(os.getcwd())" in sent[0]["Subject"]


def test_crlf_in_command_cannot_add_a_header():
    note, sent = _send("train\r\nBcc: someone@example.com")
    assert note.success
    assert sent[0]["Bcc"] is None


def test_control_characters_are_removed_from_the_subject():
    note, sent = _send("run\x1b[31m red\x00 \x7f end")
    assert note.success
    subject = sent[0]["Subject"]
    assert not any(ord(c) < 32 or 0x7F <= ord(c) <= 0x9F for c in subject)


@pytest.mark.skipif(os.name != "posix", reason="newline in a filename")
def test_attachment_with_a_newline_in_its_name(tmp_path):
    f = tmp_path / "res\nults.txt"
    f.write_text("x")
    note, sent = _send("train", [str(f)])
    assert note.success, note.message
    names = [p.get_filename() for p in sent[0].iter_attachments()]
    assert names == ["res_ults.txt"]


@pytest.mark.parametrize(
    "raw, expected",
    [("a\nb", "a b"), ("  a\t\tb  ", "a b"), ("", ""), ("\x85x", "x")],
)
def test_header_safe(raw, expected):
    assert header_safe(raw) == expected


def test_filename_safe_never_returns_empty():
    assert filename_safe("\n\r") == "__"
    assert filename_safe("") == "attachment"
