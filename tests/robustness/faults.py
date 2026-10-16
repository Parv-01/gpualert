"""Fault generators for the robustness harness.

Each helper returns a child program that misbehaves in one specific way.
They are written as source strings rather than fixtures because the point
is to exercise a real `subprocess.Popen` boundary, which is where the
wrapper's contract actually lives.
"""

from __future__ import annotations

import sys
import textwrap
from typing import List

PYTHON = sys.executable


def program(source: str) -> List[str]:
    """Wrap python source into an argv the launcher can run."""
    return [PYTHON, "-c", textwrap.dedent(source)]


# ── Output shapes ────────────────────────────────────────────────────────


def emits_invalid_utf8(before: str = "BEFORE", after: str = "AFTER", lines: int = 5) -> List[str]:
    """Prints text, then a raw non-UTF8 byte, then more text.

    C and CUDA extensions write bytes straight to fd 1 without regard for
    the parent's encoding, so this is ordinary behaviour, not an edge case.
    """
    return program(f"""
        import sys
        sys.stdout.write("{before}\\n"); sys.stdout.flush()
        sys.stdout.buffer.write(b"\\xff\\xfe binary from a C extension\\n")
        sys.stdout.buffer.flush()
        for i in range({lines}):
            sys.stdout.write(f"{after} " + str(i) + "\\n"); sys.stdout.flush()
        """)


def emits_long_line_without_newline(megabytes: int = 8) -> List[str]:
    """One enormous line with no terminator, then a normal line."""
    return program(f"""
        import sys
        sys.stdout.write("x" * ({megabytes} * 1024 * 1024))
        sys.stdout.write("\\nTAIL MARKER\\n")
        sys.stdout.flush()
        """)


def emits_volume(megabytes: int = 32) -> List[str]:
    """A chatty job, for log growth and memory behaviour."""
    return program(f"""
        import sys
        line = "epoch 0 loss 0.1234 acc 0.9876 lr 1e-4 " + "pad" * 10 + "\\n"
        target = {megabytes} * 1024 * 1024
        written = 0
        while written < target:
            sys.stdout.write(line); written += len(line)
        sys.stdout.write("TAIL MARKER\\n"); sys.stdout.flush()
        """)


def emits_ansi_and_crlf() -> List[str]:
    return program(r"""
        import sys
        sys.stdout.write("\x1b[32mgreen progress bar\x1b[0m\r\n")
        sys.stdout.write("\rprogress 100%\r\n")
        sys.stdout.write("TAIL MARKER\n")
        sys.stdout.flush()
        """)


# ── Process shapes ───────────────────────────────────────────────────────


def spawns_grandchild(pid_file: str, seconds: int = 300) -> List[str]:
    """A launcher-shaped job: spawns a worker, then waits.

    torchrun, mpirun and srun all look like this. Killing only the direct
    child leaves the worker holding GPU memory.
    """
    return program(f"""
        import subprocess, sys, time
        worker = subprocess.Popen([sys.executable, "-c",
            "import time; time.sleep({seconds})"])
        open({pid_file!r}, "w").write(str(worker.pid))
        print("worker", worker.pid, flush=True)
        time.sleep({seconds})
        """)


def double_forks(pid_file: str, seconds: int = 300) -> List[str]:
    """Daemonises, so the surviving process is reparented to init."""
    return program(f"""
        import os, sys, time
        pid = os.fork()
        if pid == 0:
            os.setsid()
            if os.fork() == 0:
                open({pid_file!r}, "w").write(str(os.getpid()))
                time.sleep({seconds})
            os._exit(0)
        os.waitpid(pid, 0)
        print("daemonised", flush=True)
        time.sleep({seconds})
        """)


def ignores_sigterm(seconds: int = 300) -> List[str]:
    """Refuses to die politely; only SIGKILL should end it."""
    return program(f"""
        import signal, sys, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("ignoring SIGTERM", flush=True)
        time.sleep({seconds})
        """)


def silent(seconds: int = 300) -> List[str]:
    """Produces one line then goes quiet — the hang signature."""
    return program(f"""
        import sys, time
        print("training started", flush=True)
        time.sleep({seconds})
        """)


def chatty(lines: int = 20, interval: float = 0.2) -> List[str]:
    return program(f"""
        import sys, time
        for i in range({lines}):
            print("step", i, flush=True)
            time.sleep({interval})
        """)


def quick(message: str = "done") -> List[str]:
    return program(f"print({message!r})")


def writes_artifact(directory: str, name: str, payload: str = "x") -> List[str]:
    """Writes one output file, for artifact-scoping tests."""
    return program(f"""
        import os
        os.makedirs({directory!r}, exist_ok=True)
        open(os.path.join({directory!r}, {name!r}), "w").write({payload!r})
        print("wrote {name}")
        """)


def cleans_up_on_sigterm(marker_file: str, seconds: int = 300) -> List[str]:
    """Catches SIGTERM, writes a marker, then exits.

    Real training scripts use this to flush a final checkpoint. A wrapper
    that goes straight to SIGKILL destroys that work.
    """
    return program(f"""
        import signal, sys, time

        def handler(signum, frame):
            open({marker_file!r}, "w").write("cleaned up")
            sys.exit(0)

        signal.signal(signal.SIGTERM, handler)
        print("ready for graceful shutdown", flush=True)
        time.sleep({seconds})
        """)
