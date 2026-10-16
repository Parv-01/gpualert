"""get_tail reads from the end of the file (D6).

The reference is the 0.1.5 implementation, which read the whole file with
universal newlines. For any file that fits inside the read budget the new
reader must return exactly what the old one did.
"""

from __future__ import annotations

import random
import tracemalloc

import pytest

from gpualert import log_manager
from gpualert.log_manager import get_tail


def reference_tail(path, n):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return "".join(f.readlines()[-n:])


ALPHABET = [b"a", b"b", b" ", b"\n", b"\r", b"\r\n", b"\xe2\x82\xac", b"\xff", b"\x00", b"\t"]


@pytest.mark.parametrize("seed", range(300))
def test_matches_the_old_reader_on_random_content(tmp_path, seed, monkeypatch):
    rng = random.Random(seed)
    # Small blocks so most files span several of them and every boundary
    # case (split CRLF, split multi-byte character) is exercised.
    monkeypatch.setattr(log_manager, "_TAIL_BLOCK", rng.choice([1, 2, 3, 7, 16, 64]))
    data = b"".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 400)))
    path = tmp_path / "x.log"
    path.write_bytes(data)
    n = rng.randint(1, 30)
    assert get_tail(str(path), n) == reference_tail(path, n)


@pytest.mark.parametrize(
    "content, n, expected",
    [
        (b"", 5, ""),
        (b"one\n", 5, "one\n"),
        (b"one\ntwo", 1, "two"),
        (b"one\ntwo\n", 1, "two\n"),
        (b"a\r\nb\r\nc\r\n", 2, "b\nc\n"),
        (b"a\rb\rc", 2, "b\nc"),
        (b"\n\n\n", 2, "\n\n"),
    ],
)
def test_edge_shapes(tmp_path, content, n, expected):
    path = tmp_path / "x.log"
    path.write_bytes(content)
    assert get_tail(str(path), n) == expected


def test_zero_or_negative_lines_is_empty(tmp_path):
    path = tmp_path / "x.log"
    path.write_text("a\nb\n")
    assert get_tail(str(path), 0) == ""
    assert get_tail(str(path), -3) == ""


def test_a_line_longer_than_the_budget_is_cut_and_marked(tmp_path):
    path = tmp_path / "x.log"
    path.write_bytes(b"head\n" + b"x" * 5000 + b"END")
    tail = get_tail(str(path), 3, max_bytes=1000)
    assert tail.startswith("[...]")
    assert tail.endswith("END")
    assert len(tail) <= 1000 + len("[...]")


def test_budget_caps_lines_returned_from_huge_lines(tmp_path):
    path = tmp_path / "x.log"
    path.write_bytes((b"y" * 3000 + b"\n") * 10)
    tail = get_tail(str(path), 10, max_bytes=7000)
    # Only whole lines that fit in the budget come back.
    assert tail.count("\n") == 2
    assert not tail.startswith("[...]")


def test_memory_is_bounded_on_a_large_file(tmp_path):
    path = tmp_path / "big.log"
    line = b"epoch 1 loss 0.1234 acc 0.98 lr 1e-4 padding padding\n"
    with open(path, "wb") as f:
        f.write(line * 400_000)  # ~21 MB
    tracemalloc.start()
    tail = get_tail(str(path), 50)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert tail.count("\n") == 50
    assert peak < 1 * 1024 * 1024


def test_unreadable_path_is_empty(tmp_path):
    assert get_tail(str(tmp_path), 5) == ""
    assert get_tail("", 5) == ""
