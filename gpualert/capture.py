"""
gpualert.capture — Binary-safe capture of a child's stdout and stderr.

The pipes are read as raw bytes and decoded here, not by a text-mode pipe.
A text-mode pipe raises UnicodeDecodeError on the first byte that is not
valid in the locale encoding; in 0.1.5 the reader thread swallowed that
error and exited, so every line after one stray byte from a C or CUDA
extension was lost while the job still reported success (D1).

Here the decoder replaces bad sequences with U+FFFD and counts them, so no
byte sequence can stop the stream and every replacement is reported. Line
splitting is done after decoding with an incremental decoder, so a
multi-byte character or a CRLF split across two reads comes out whole.

Nothing in this module raises into the launcher. A reader that fails for
any reason records why, then keeps draining its pipe so the child can never
block on a full pipe buffer.
"""

from __future__ import annotations

import codecs
import io
import locale
import os
import threading
from collections import deque
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional

# Bytes requested per read. A read returns whatever is in the pipe, up to this.
READ_CHUNK = 64 * 1024

# A line longer than this is written in pieces instead of being held in
# memory until its newline arrives. One 100 MB progress line should cost
# 64 KiB of wrapper memory, not 100 MB.
MAX_PENDING = 64 * 1024

# Lines kept in memory per stream. Only used for the result tails when no
# log file could be created at all.
RING_LINES = 200
RING_LINE_CHARS = 8 * 1024

_ERRORS = "gpualert.count-replace"
_counter = threading.local()


def _count_and_replace(exc: UnicodeError):
    # Same output as errors="replace", plus a per-thread count. Each reader
    # runs in its own thread and only decodes there, so a thread-local
    # counter is exact without any locking.
    if not isinstance(exc, UnicodeDecodeError):
        raise exc
    _counter.n = getattr(_counter, "n", 0) + 1
    return ("�", exc.end)


codecs.register_error(_ERRORS, _count_and_replace)


def stream_encoding() -> str:
    """Encoding used to decode child output.

    Matches what Popen(text=True) used before, so a job that printed fine
    under 0.1.5 prints the same now. A plain ASCII locale (LANG=C on an old
    image) is widened to UTF-8, since ASCII would turn every accented
    character into a replacement.
    """
    try:
        name = codecs.lookup(locale.getpreferredencoding(False) or "utf-8").name
    except LookupError:
        return "utf-8"
    return "utf-8" if name == "ascii" else name


class LogSink:
    """Appends to the three job logs from several threads.

    Files are opened once in append mode and written without a userspace
    buffer, so every write reaches the kernel immediately (the same
    durability as the old open-append-close per line) without paying for an
    open() per line. All writes take the shared lock, which the launcher
    also passes to write_to_log for its [SYSTEM] lines, so ordering holds.

    A path of "" means that log could not be created; writes to it are
    dropped and the in-memory ring is the only record.
    """

    def __init__(self, paths: Dict[str, str], lock: threading.Lock):
        self._paths = paths
        self._lock = lock
        self._files: Dict[str, Optional[io.FileIO]] = {}
        self._closed = False
        self.write_failures: Dict[str, int] = {}
        self.first_write_error: Dict[str, str] = {}
        self.bytes_after_close = 0

    def _handle(self, key: str):
        if key in self._files:
            return self._files[key]
        path = self._paths.get(key) or ""
        handle = None
        if path:
            try:
                handle = open(path, "ab", buffering=0)
            except OSError as exc:
                self._record_failure(key, exc)
        self._files[key] = handle
        return handle

    def _record_failure(self, key: str, exc: BaseException) -> None:
        self.write_failures[key] = self.write_failures.get(key, 0) + 1
        self.first_write_error.setdefault(key, f"{type(exc).__name__}: {exc}")

    def write(self, pieces: List[tuple]) -> None:
        """Write (key, text) pieces under one lock acquisition."""
        with self._lock:
            if self._closed:
                self.bytes_after_close += sum(len(t) for _k, t in pieces)
                return
            for key, text in pieces:
                handle = self._handle(key)
                if handle is None:
                    continue
                data = text.encode("utf-8", errors="replace")
                if os.linesep != "\n":
                    data = data.replace(b"\n", os.linesep.encode())
                try:
                    view = memoryview(data)
                    while view:
                        written = handle.write(view)
                        if not written:
                            raise OSError("write returned 0 bytes")
                        view = view[written:]
                except (OSError, ValueError) as exc:
                    self._record_failure(key, exc)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for handle in self._files.values():
                if handle is not None:
                    try:
                        handle.close()
                    except OSError:
                        pass
            self._files.clear()


class StreamReader:
    """Reads one pipe to EOF and writes timestamped lines to the sink.

    Per-stream log: one ``[HH:MM:SS] `` prefix per line, line content kept
    byte-for-byte apart from decoding. A line longer than MAX_PENDING is
    written in pieces with no extra newlines, so the file still holds one
    line. Combined log: every piece gets its own ``[OUT] [HH:MM:SS] `` line,
    because two streams interleave there and a half-written line would be
    split by the other stream's output anyway.
    """

    def __init__(
        self,
        name: str,
        prefix: str,
        log_key: str,
        sink: LogSink,
        encoding: str,
        echo: Optional[Callable[[str], None]] = None,
    ):
        self.name = name
        self.prefix = prefix
        self.log_key = log_key
        self.sink = sink
        self.encoding = encoding
        self.echo = echo
        self.bytes_read = 0
        self.lines = 0
        self.replacements = 0
        self.error = ""
        self.bytes_discarded = 0
        self.ring: Deque[str] = deque(maxlen=RING_LINES)
        self._mid_line = False

    def tail(self, n: int) -> str:
        return "".join(list(self.ring)[-n:]) if n > 0 else ""

    def _emit(self, text: str, final: bool) -> str:
        """Write every complete line in text; return the unfinished rest."""
        stamp = datetime.now().strftime("%H:%M:%S")
        pieces: List[tuple] = []
        start = 0
        while True:
            nl = text.find("\n", start)
            if nl < 0:
                break
            line = text[start : nl + 1]
            self._add(pieces, line, stamp, complete=True)
            start = nl + 1
        rest = text[start:]
        if rest and (final or len(rest) >= MAX_PENDING):
            self._add(pieces, rest + ("\n" if final else ""), stamp, complete=final)
            rest = ""
        elif final and self._mid_line:
            # The last piece of an over-long line was flushed without its
            # newline and the stream ended there. Close the line.
            pieces.append((self.log_key, "\n"))
            self._mid_line = False
            self.lines += 1
        if pieces:
            self.sink.write(pieces)
        return rest

    def _add(self, pieces: List[tuple], text: str, stamp: str, complete: bool) -> None:
        head = "" if self._mid_line else f"[{stamp}] "
        pieces.append((self.log_key, head + text))
        body = text if text.endswith("\n") else text + "\n"
        pieces.append(("combined", f"[{self.prefix}] [{stamp}] {body}"))
        self._mid_line = not complete
        if complete:
            self.lines += 1
        self.ring.append(text[:RING_LINE_CHARS] + ("" if text.endswith("\n") else "\n"))
        if self.echo is not None:
            try:
                self.echo(f"  {self.prefix} | {body}")
            except Exception:
                pass

    def run(self, pipe) -> None:
        _counter.n = 0
        pending = ""
        try:
            decoder = io.IncrementalNewlineDecoder(
                codecs.getincrementaldecoder(self.encoding)(errors=_ERRORS), translate=True
            )
            while True:
                chunk = pipe.read(READ_CHUNK)
                if not chunk:
                    break
                self.bytes_read += len(chunk)
                pending = self._emit(pending + decoder.decode(chunk), final=False)
            self._emit(pending + decoder.decode(b"", final=True), final=True)
        except Exception as exc:  # must keep draining whatever went wrong
            self.error = f"{type(exc).__name__}: {exc}"
            self._drain(pipe)
        finally:
            self.replacements = getattr(_counter, "n", 0)
            try:
                pipe.close()
            except Exception:
                pass

    def _drain(self, pipe) -> None:
        # The child writes into a pipe of ~64 KiB. If nobody reads it, the
        # child blocks forever on its next write and the job looks hung.
        try:
            while True:
                chunk = pipe.read(READ_CHUNK)
                if not chunk:
                    return
                self.bytes_discarded += len(chunk)
        except Exception:
            return

    def notes(self) -> List[str]:
        out: List[str] = []
        if self.replacements:
            out.append(
                f"{self.name} had {self.replacements} byte sequence(s) that could not be "
                f"decoded as {self.encoding}; each was replaced with U+FFFD in the logs. "
                "Everything around them was captured."
            )
        if self.error:
            out.append(
                f"{self.name} capture stopped early ({self.error}). "
                f"{self.bytes_discarded} byte(s) after that point were read and discarded "
                "so the job could not block; the log is incomplete."
            )
        return out


def sink_notes(sink: LogSink, labels: Dict[str, str]) -> List[str]:
    out: List[str] = []
    for key, count in sink.write_failures.items():
        out.append(
            f"Writing {labels.get(key, key)} failed {count} time(s) "
            f"({sink.first_write_error.get(key, '')}); that log is incomplete."
        )
    return out
