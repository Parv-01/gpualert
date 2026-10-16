"""Binary-safe capture (D1).

Unit tests drive StreamReader with a fake pipe that returns exactly the
chunks we choose, so chunk-boundary cases are deterministic instead of
depending on how the kernel happens to split a child's writes. The
integration tests at the bottom go through a real Popen.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from gpualert import capture
from gpualert.capture import LogSink, StreamReader, stream_encoding
from gpualert.launcher import run_job


class FakePipe:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.closed = False

    def read(self, _n):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


def _capture(tmp_path, chunks, encoding="utf-8"):
    paths = {
        "stdout": str(tmp_path / "stdout.log"),
        "stderr": str(tmp_path / "stderr.log"),
        "combined": str(tmp_path / "combined.log"),
    }
    sink = LogSink(paths, threading.Lock())
    reader = StreamReader("stdout", "OUT", "stdout", sink, encoding)
    pipe = FakePipe(chunks)
    reader.run(pipe)
    sink.close()
    assert pipe.closed
    stdout = Path(paths["stdout"]).read_bytes().decode("utf-8")
    combined = Path(paths["combined"]).read_bytes().decode("utf-8")
    return reader, stdout, combined


def _bodies(log_text):
    """Strip the '[HH:MM:SS] ' prefix from each line of a per-stream log."""
    return [line[11:] for line in log_text.splitlines()]


class TestDecoding:
    def test_bad_byte_does_not_end_the_stream(self, tmp_path):
        reader, out, _ = _capture(tmp_path, [b"before\n\xff\xfe raw\nafter 1\nafter 2\n"])
        assert _bodies(out) == ["before", "�� raw", "after 1", "after 2"]
        assert reader.replacements == 2
        assert reader.error == ""

    def test_multibyte_character_split_across_reads(self, tmp_path):
        euro = "€".encode()
        reader, out, _ = _capture(tmp_path, [b"price " + euro[:1], euro[1:2], euro[2:] + b"\n"])
        assert _bodies(out) == ["price €"]
        assert reader.replacements == 0

    def test_crlf_split_across_reads_is_one_newline(self, tmp_path):
        _, out, _ = _capture(tmp_path, [b"first\r", b"\nsecond\r\n"])
        assert _bodies(out) == ["first", "second"]

    def test_bare_carriage_returns_split_lines_as_before(self, tmp_path):
        # 0.1.5 used universal newlines, so tqdm-style \r updates became
        # separate lines. Keep that.
        _, out, _ = _capture(tmp_path, [b"10%\r50%\r100%\n"])
        assert _bodies(out) == ["10%", "50%", "100%"]

    def test_a_genuine_replacement_character_is_not_counted(self, tmp_path):
        reader, out, _ = _capture(tmp_path, ["already � here\n".encode()])
        assert reader.replacements == 0
        assert "already � here" in out

    def test_replacements_are_counted_exactly(self, tmp_path):
        block = b"a" * 4095 + b"\x80"
        chunks = [block] * 256 + [b"\nend\n"]
        reader, out, _ = _capture(tmp_path, chunks)
        assert reader.replacements == 256
        assert out.endswith("end\n")

    def test_nul_bytes_pass_through(self, tmp_path):
        _, out, _ = _capture(tmp_path, [b"a\x00b\n"])
        assert _bodies(out) == ["a\x00b"]

    def test_truncated_sequence_at_eof_is_replaced_not_lost(self, tmp_path):
        reader, out, _ = _capture(tmp_path, [b"tail \xe2\x82"])
        assert _bodies(out) == ["tail �"]
        assert reader.replacements == 1

    def test_last_line_without_newline_is_kept(self, tmp_path):
        _, out, combined = _capture(tmp_path, [b"one\ntwo"])
        assert _bodies(out) == ["one", "two"]
        assert out.endswith("two\n")
        assert "[OUT]" in combined and "two" in combined

    def test_latin1_locale_decodes_as_latin1(self, tmp_path):
        reader, out, _ = _capture(tmp_path, [b"caf\xe9\n"], encoding="latin-1")
        assert _bodies(out) == ["café"]
        assert reader.replacements == 0


class TestLongLines:
    def test_long_line_is_written_in_pieces_but_stays_one_line(self, tmp_path):
        size = capture.MAX_PENDING * 3 + 17
        chunks = [b"x" * 4096] * (size // 4096) + [b"x" * (size % 4096), b"\nTAIL\n"]
        reader, out, combined = _capture(tmp_path, chunks)
        lines = out.splitlines()
        assert len(lines) == 2
        assert lines[0].count("x") == size
        assert lines[1].endswith("TAIL")
        assert reader.lines == 2
        # Combined gets one prefixed line per piece.
        assert combined.count("[OUT]") >= 4

    def test_long_line_ending_exactly_at_eof_is_closed(self, tmp_path):
        chunks = [b"y" * capture.MAX_PENDING]
        _, out, _ = _capture(tmp_path, chunks)
        assert out.endswith("\n")
        assert out.count("\n") == 1

    def test_pending_memory_is_bounded(self, tmp_path):
        # 8 MB with no newline must never sit in memory as one string.
        seen = []
        real_emit = StreamReader._emit

        def spy(self, text, final):
            seen.append(len(text))
            return real_emit(self, text, final)

        chunks = [b"z" * capture.READ_CHUNK] * 128
        original = StreamReader._emit
        StreamReader._emit = spy
        try:
            _capture(tmp_path, chunks)
        finally:
            StreamReader._emit = original
        assert max(seen) <= capture.MAX_PENDING + capture.READ_CHUNK


class TestFailureHandling:
    def test_reader_failure_is_recorded_and_the_pipe_is_drained(self, tmp_path, monkeypatch):
        calls = {"n": 0}
        real_emit = StreamReader._emit

        def flaky(self, text, final):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated writer bug")
            return real_emit(self, text, final)

        monkeypatch.setattr(StreamReader, "_emit", flaky)
        pipe_chunks = [b"ok\n", b"boom\n", b"rest" * 1000, b"more\n"]
        reader, out, _ = _capture(tmp_path, pipe_chunks)
        assert "ok" in out
        assert "simulated writer bug" in reader.error
        assert reader.bytes_discarded == len(b"rest" * 1000) + len(b"more\n")
        assert any("capture stopped early" in n for n in reader.notes())

    def test_unwritable_log_is_reported(self, tmp_path):
        paths = {
            "stdout": str(tmp_path / "missing_dir" / "stdout.log"),
            "combined": str(tmp_path / "combined.log"),
        }
        sink = LogSink(paths, threading.Lock())
        reader = StreamReader("stdout", "OUT", "stdout", sink, "utf-8")
        reader.run(FakePipe([b"hello\n"]))
        sink.close()
        notes = capture.sink_notes(sink, {"stdout": "stdout.log"})
        assert notes and "stdout.log" in notes[0]
        assert "hello" in Path(paths["combined"]).read_text()
        assert reader.tail(5) == "hello\n"

    def test_writes_after_close_are_counted_not_written(self, tmp_path):
        path = tmp_path / "combined.log"
        sink = LogSink({"combined": str(path)}, threading.Lock())
        sink.write([("combined", "kept\n")])
        sink.close()
        sink.write([("combined", "late\n")])
        assert path.read_text() == "kept\n"
        assert sink.bytes_after_close == len("late\n")


class TestEncodingChoice:
    def test_ascii_locale_is_widened_to_utf8(self, monkeypatch):
        monkeypatch.setattr("locale.getpreferredencoding", lambda _=False: "ANSI_X3.4-1968")
        assert stream_encoding() == "utf-8"

    def test_unknown_locale_falls_back_to_utf8(self, monkeypatch):
        monkeypatch.setattr("locale.getpreferredencoding", lambda _=False: "no-such-codec")
        assert stream_encoding() == "utf-8"

    def test_latin1_locale_is_respected(self, monkeypatch):
        monkeypatch.setattr("locale.getpreferredencoding", lambda _=False: "ISO-8859-1")
        assert stream_encoding() == "iso8859-1"


# ── Through a real child process ─────────────────────────────────────────


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    monkeypatch.chdir(tmp_path)
    return h


def _py(src):
    return [sys.executable, "-c", src]


class TestThroughPopen:
    def test_bad_bytes_on_stderr_are_survived_and_reported(self, home):
        result = run_job(
            _py(
                "import sys\n"
                "sys.stderr.buffer.write(b'err \\xff\\n'); sys.stderr.flush()\n"
                "print('still here', flush=True)\n"
                "sys.stderr.write('last err\\n')\n"
            )
        )
        assert result.status == "success"
        err = Path(result.stderr_log_path).read_text(encoding="utf-8")
        assert "err �" in err and "last err" in err
        assert any(
            "stderr had 1 byte sequence(s) that could not be decoded" in n for n in result.notes
        )

    def test_notes_reach_the_log_footer(self, home):
        result = run_job(_py("import sys; sys.stdout.buffer.write(b'\\x80\\n')"))
        combined = Path(result.combined_log_path).read_text(encoding="utf-8")
        assert "Note     : stdout had 1 byte sequence(s) that could not be decoded" in combined

    def test_split_write_of_a_multibyte_character(self, home):
        result = run_job(
            _py(
                "import sys, time\n"
                "e = '€'.encode()\n"
                "sys.stdout.buffer.write(e[:1]); sys.stdout.flush(); time.sleep(0.2)\n"
                "sys.stdout.buffer.write(e[1:] + b'\\n'); sys.stdout.flush()\n"
            )
        )
        assert "€" in Path(result.stdout_log_path).read_text(encoding="utf-8")
        assert result.notes == []

    def test_clean_output_produces_no_notes(self, home):
        result = run_job(_py("print('hello 안녕하세요 🚀')"))
        assert result.notes == []
        assert "안녕하세요 🚀" in Path(result.stdout_log_path).read_text(encoding="utf-8")

    def test_verbose_echoes_lines(self, home, capsys):
        run_job(_py("print('echo me')"), verbose=True)
        assert "OUT | echo me" in capsys.readouterr().out

    def test_large_output_after_a_reader_failure_does_not_hang(self, home, monkeypatch):
        def broken(self, text, final):
            raise OSError("simulated")

        monkeypatch.setattr(StreamReader, "_emit", broken)
        start = time.monotonic()
        # 4 MB is far beyond a pipe buffer: without draining the child blocks.
        result = run_job(
            _py("import sys; sys.stdout.write('x' * (4 * 1024 * 1024)); print('done')"),
            timeout=60,
        )
        assert result.status == "success"
        assert time.monotonic() - start < 30
        assert any("capture stopped early" in n for n in result.notes)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process model")
    def test_pipe_held_by_a_background_process_is_disclosed(self, home, tmp_path):
        pid_file = tmp_path / "bg.pid"
        result = run_job(
            _py(
                "import subprocess, sys\n"
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
                "print('parent exits', flush=True)\n"
            )
        )
        try:
            assert result.status == "success"
            assert any("stayed open after the job exited" in n for n in result.notes)
            combined = Path(result.combined_log_path).read_text(encoding="utf-8")
            assert "=== Job Complete ===" in combined
        finally:
            try:
                os.kill(int(pid_file.read_text()), 9)
            except (OSError, ValueError):
                pass
