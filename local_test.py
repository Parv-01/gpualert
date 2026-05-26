#!/usr/bin/env python3
"""
local_test.py — Offline end-to-end test harness for GPUAlert.

Runs every component of the package against an isolated temp HOME so it does
not touch your real ~/.gpualert/. Each check prints PASS or FAIL with a
reason, and the full run (including every traceback) is mirrored to a log
file under local_test_runs/<timestamp>.log so you can post-mortem failures.

Usage:
    python local_test.py                  # run everything
    python local_test.py --only config    # run one section
    python local_test.py --list           # list section names
    python local_test.py --keep-tmp       # don't delete the temp HOME at exit

Exit code: 0 if every check passed, 1 otherwise. Suitable for CI gating
and as the "is this branch ready to publish" green-light.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

# Snapshot the parent process's sys.path BEFORE we repoint HOME — Python's
# user-site dir is computed from HOME, so without this snapshot subprocesses
# launched under the test HOME can no longer import packages installed with
# `pip install --user` (pydantic, typer, rich, etc.).
_PYPATH_SNAPSHOT = os.pathsep.join(p for p in sys.path if p)

# ── HOME isolation MUST happen before importing gpualert ──────────────────────
_TEST_HOME = Path(tempfile.mkdtemp(prefix="gpualert_test_home_"))
os.environ["HOME"] = str(_TEST_HOME)
os.environ["USERPROFILE"] = str(_TEST_HOME)  # Windows safety

# Now safe to import the package under test.
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

# ── Logging — every check goes to stdout AND a file ──────────────────────────
_LOG_DIR = _ROOT / "local_test_runs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_PATH = _LOG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
_LOG_FILE = open(_LOG_PATH, "w", encoding="utf-8")

_PASS = 0
_FAIL = 0
_FAIL_DETAILS: List[Tuple[str, str]] = []  # [(check_name, detail)]
_CURRENT_SECTION = ""

_ANSI = {
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}

# ── Args ─────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
parser.add_argument("--only", help="Run a single section (use --list to see names).")
parser.add_argument("--list", action="store_true", help="List section names and exit.")
parser.add_argument("--keep-tmp", action="store_true", help="Do not delete temp HOME.")
parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors.")
ARGS = parser.parse_args()
if ARGS.no_color or not sys.stdout.isatty():
    _ANSI = {k: "" for k in _ANSI}


def _emit(line: str) -> None:
    """Write to stdout and the log file. Log file gets ANSI-stripped text."""
    print(line)
    plain = line
    for code in ("\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[1m", "\033[0m"):
        plain = plain.replace(code, "")
    _LOG_FILE.write(plain + "\n")
    _LOG_FILE.flush()


def section(name: str) -> None:
    global _CURRENT_SECTION
    _CURRENT_SECTION = name
    bar = "─" * 70
    _emit("")
    _emit(f"{_ANSI['cyan']}{bar}{_ANSI['reset']}")
    _emit(f"{_ANSI['cyan']}{_ANSI['bold']}  {name}{_ANSI['reset']}")
    _emit(f"{_ANSI['cyan']}{bar}{_ANSI['reset']}")


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        _emit(f"  {_ANSI['green']}PASS{_ANSI['reset']}  {name}")
        if detail:
            _emit(f"        {_ANSI['dim']}{detail}{_ANSI['reset']}")
        return True
    _FAIL += 1
    _FAIL_DETAILS.append((f"[{_CURRENT_SECTION}] {name}", detail or "(no detail)"))
    _emit(f"  {_ANSI['red']}FAIL{_ANSI['reset']}  {name}")
    if detail:
        _emit(f"        {_ANSI['red']}{detail}{_ANSI['reset']}")
    return False


def run_check(name: str, fn: Callable[[], None]) -> None:
    """Wrap a callable that should not raise. Any exception is a FAIL."""
    try:
        fn()
        check(name, True)
    except AssertionError as e:
        check(name, False, f"AssertionError: {e}")
    except Exception as e:
        tb = traceback.format_exc()
        check(name, False, f"{type(e).__name__}: {e}")
        _LOG_FILE.write(tb + "\n")
        _LOG_FILE.flush()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — config
# ─────────────────────────────────────────────────────────────────────────────
def s1_config() -> None:
    section("1. config — load/save round-trip, validation, safe_repr")
    from gpualert.config import (
        GPUAlertConfig,
        SMTPConfig,
        EmailConfig,
        get_config_path,
        load_config,
        save_config,
        validate_config,
    )

    # 1a. defaults
    cfg = GPUAlertConfig()
    check("defaults: SMTP port is 587", cfg.smtp.port == 587)
    check("defaults: subject_prefix is [GPUAlert]", cfg.email.subject_prefix == "[GPUAlert]")
    check("defaults: is_configured() False on empty", cfg.is_configured() is False)

    # 1b. validate_config rejects empty
    ok, errors = validate_config(cfg)
    check("validate_config rejects empty config", ok is False and len(errors) >= 4,
          detail=f"errors={errors}")

    # 1c. validate_config accepts a populated config
    cfg.smtp.username = "you@example.com"
    cfg.smtp.password = "secret"
    cfg.email.from_address = "you@example.com"
    cfg.email.to_addresses = ["you@example.com"]
    ok, errors = validate_config(cfg)
    check("validate_config accepts populated config", ok is True, detail=f"errors={errors}")

    # 1d. invalid recipient flagged
    bad = GPUAlertConfig(
        smtp=SMTPConfig(username="u", password="p"),
        email=EmailConfig(from_address="u@x.com", to_addresses=["not-an-email"]),
    )
    ok, errors = validate_config(bad)
    check("validate_config flags malformed recipient", ok is False and any("not-an-email" in e for e in errors),
          detail=f"errors={errors}")

    # 1e. port out of range
    bad2 = GPUAlertConfig(
        smtp=SMTPConfig(username="u", password="p", port=99999),
        email=EmailConfig(from_address="u@x.com", to_addresses=["x@y.com"]),
    )
    ok, errors = validate_config(bad2)
    check("validate_config flags port out of range", ok is False and any("port" in e for e in errors))

    # 1f. safe_repr masks password
    cfg.smtp.password = "supersecret-app-pw"
    repr_out = cfg.safe_repr()
    check("safe_repr masks password", "supersecret-app-pw" not in repr_out and "***" in repr_out)
    check("safe_repr is valid JSON", repr_out.startswith("{") and repr_out.endswith("}"))

    # 1g. save → load round-trip
    save_config(cfg)
    path = get_config_path()
    check("config file created", path.exists() and path.is_file(),
          detail=f"path={path}")
    loaded = load_config()
    check("loaded config matches saved username", loaded.smtp.username == cfg.smtp.username)
    check("loaded config matches saved password", loaded.smtp.password == cfg.smtp.password)
    check("loaded config matches recipients", loaded.email.to_addresses == cfg.email.to_addresses)

    # 1h. corrupt file → defaults, no crash
    with open(path, "w") as f:
        f.write("this is not valid TOML {{{{\n")
    fallback = load_config()
    check("corrupt config falls back to defaults", fallback.smtp.port == 587)

    # 1i. permissions (POSIX only)
    if hasattr(os, "geteuid"):
        save_config(cfg)
        mode = oct(path.stat().st_mode)[-3:]
        check("config file is mode 600", mode == "600", detail=f"mode={mode}")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — log_manager
# ─────────────────────────────────────────────────────────────────────────────
def s2_log_manager() -> None:
    section("2. log_manager — log dir creation, thread-safe writes, tail")
    from gpualert.log_manager import (
        create_job_log_dir,
        get_job_log_paths,
        get_log_dir,
        get_tail,
        list_recent_logs,
        write_to_log,
    )

    log_root = get_log_dir()
    check("log root created under HOME", str(log_root).startswith(str(_TEST_HOME)),
          detail=f"log_root={log_root}")

    job_dir = create_job_log_dir("a1b2c3d4-e5f6", "python train.py")
    check("job dir created", job_dir.exists() and job_dir.is_dir())

    stdout_p, stderr_p, combined_p = get_job_log_paths(job_dir)
    for label, p in [("stdout.log", stdout_p), ("stderr.log", stderr_p), ("combined.log", combined_p)]:
        check(f"{label} exists before any write", os.path.isfile(p))

    # Thread-safe writes
    lock = threading.Lock()
    def writer(tag: str):
        for i in range(50):
            write_to_log(combined_p, f"[{tag}] line {i}\n", lock)

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("A", "B", "C")]
    for t in threads: t.start()
    for t in threads: t.join()

    with open(combined_p) as f:
        content = f.read()
    check("concurrent writes preserved all 150 lines", content.count("line ") == 150,
          detail=f"observed={content.count('line ')}")

    # write_to_log never raises
    try:
        write_to_log("/nonexistent/dir/whatever.log", "this should be silently dropped")
        write_to_log("", "empty path")
        check("write_to_log never raises on bad path", True)
    except Exception as e:
        check("write_to_log never raises on bad path", False, detail=f"raised: {e}")

    # get_tail
    tail = get_tail(combined_p, 10)
    check("get_tail returns last N lines", tail.count("\n") <= 10 and "line " in tail)
    check("get_tail on missing file returns ''", get_tail("/nope/nope.log", 10) == "")

    # list_recent_logs
    recent = list_recent_logs(50)
    check("list_recent_logs returns the job dir", any(str(job_dir) == str(r["dir"]) for r in recent),
          detail=f"found={len(recent)} entries")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — launcher
# ─────────────────────────────────────────────────────────────────────────────
def s3_launcher() -> None:
    section("3. launcher — run_job for success / failure / not-found / timeout")
    from gpualert.launcher import run_job

    # 3a. success
    r = run_job([sys.executable, "-c", "print('hello'); print('Accuracy: 0.92')"])
    check("success: status == success", r.status == "success", detail=f"status={r.status}")
    check("success: exit_code == 0", r.exit_code == 0)
    check("success: logs on disk", all(os.path.isfile(p) for p in r.log_files()))
    check("success: stdout_tail captured", "hello" in r.stdout_tail)
    check("success: error_summary picked up metric", "0.92" in (r.error_summary or ""),
          detail=f"error_summary={r.error_summary!r}")

    # 3b. failure (non-zero exit)
    r = run_job([sys.executable, "-c", "import sys; sys.exit(2)"])
    check("failure: status == failed", r.status == "failed")
    check("failure: exit_code == 2", r.exit_code == 2)
    check("failure: logs still on disk", all(os.path.isfile(p) for p in r.log_files()))

    # 3c. command not found
    r = run_job(["definitely-not-a-real-binary-xyz-12345"])
    check("not-found: status == failed", r.status == "failed")
    check("not-found: exit_code == 127", r.exit_code == 127)
    check("not-found: log files still on disk", all(os.path.isfile(p) for p in r.log_files()))

    # 3d. failure with Python traceback
    r = run_job([sys.executable, "-c", "raise RuntimeError('boom')"])
    check("traceback: status == failed", r.status == "failed")
    check("traceback: error_summary mentions runtime", bool(r.error_summary),
          detail=f"error_summary={r.error_summary!r}")

    # 3e. timeout
    r = run_job([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    check("timeout: status == timeout", r.status == "timeout", detail=f"status={r.status}")
    check("timeout: log mentions kill", "killed after" in open(r.combined_log_path).read().lower())

    # 3f. CUDA OOM simulation
    r = run_job([sys.executable, "-c",
                 "import sys; sys.stderr.write('RuntimeError: CUDA out of memory\\n'); sys.exit(1)"])
    check("CUDA OOM detected in error_summary",
          "out-of-memory" in (r.error_summary or "").lower() or "OOM" in (r.error_summary or ""),
          detail=f"error_summary={r.error_summary!r}")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — parse_errors
# ─────────────────────────────────────────────────────────────────────────────
def s4_parse_errors() -> None:
    section("4. parse_errors — error classification + success metrics")
    from gpualert.parse_errors import (
        extract_success_metrics,
        extract_traceback,
        get_error_confidence,
        parse_errors,
    )

    cases = [
        ("CUDA out of memory", "GPU out-of-memory", "CUDA OOM"),
        ("NCCL error: timeout", "NCCL", "NCCL"),
        ("RuntimeError: CUDA error: device-side", "CUDA runtime", "CUDA runtime"),
        ("MemoryError: cannot allocate", "RAM", "system OOM"),
        ("Segmentation fault (core dumped)", "Segmentation fault", "segfault"),
        ("FileNotFoundError: foo.csv", "File not found", "missing file"),
        ("ModuleNotFoundError: No module named torch", "Missing Python module", "missing module"),
        ("loss is nan", "NaN", "NaN loss"),
        ("Out of memory: Kill process 9999", "OOM", "OOMKiller"),
        ("Traceback (most recent call last):\n  File ...", "Python exception", "generic traceback"),
    ]
    for stderr, expect_substr, label in cases:
        out = parse_errors(stdout="", stderr=stderr, exit_code=1)
        check(f"detects: {label}", expect_substr.lower() in out.lower(),
              detail=f"got={out!r}")

    # Exit-code fallback when no pattern matches
    out = parse_errors(stdout="", stderr="absolutely no signal here", exit_code=42)
    check("falls back to exit code message", "42" in out, detail=f"got={out!r}")

    # No errors → empty
    out = parse_errors(stdout="all good", stderr="", exit_code=0)
    check("clean run → empty summary", out == "", detail=f"got={out!r}")

    # Success metrics
    out = extract_success_metrics("epoch 10 loss: 0.123 accuracy: 0.95 F1: 0.88")
    check("metrics: accuracy extracted", "0.95" in out)
    check("metrics: loss extracted", "0.123" in out)
    check("metrics: F1 extracted", "0.88" in out)

    # Traceback extraction
    tb_text = "noise\nTraceback (most recent call last):\n  File 'x.py', line 1\nRuntimeError: x\n"
    tb = extract_traceback(tb_text)
    check("traceback extracted", "Traceback" in tb)

    # Confidence levels
    check("confidence HIGH when suggestion present",
          get_error_confidence("X\nSuggestion: do Y") == "HIGH")
    check("confidence NONE on empty", get_error_confidence("") == "NONE")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — artifacts
# ─────────────────────────────────────────────────────────────────────────────
def s5_artifacts() -> None:
    section("5. artifacts — find, budget, overflow zip")
    from gpualert.artifacts import (
        compress_artifacts,
        find_artifacts,
        prepare_attachments,
        summarize_artifacts,
    )
    from gpualert.types import ArtifactFile

    work = Path(tempfile.mkdtemp(prefix="gpualert_artifacts_"))
    start = datetime.now()
    time.sleep(0.05)

    # Files matching patterns and after start_time
    (work / "metrics.csv").write_text("epoch,loss\n1,0.5\n")
    (work / "loss.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 256)
    (work / "results.json").write_text('{"acc": 0.9}')

    # File predating start_time — should be excluded
    old = work / "old.csv"
    old.write_text("old data")
    os.utime(old, (start.timestamp() - 600, start.timestamp() - 600))

    # File too big — should be excluded
    big = work / "big.npz"
    big.write_bytes(b"\x00" * (30 * 1024 * 1024))

    # File inside excluded dir
    (work / ".git").mkdir()
    (work / ".git" / "head.log").write_text("nope")

    # Nested directory — depth-limited scan should still pick this up at depth 1
    (work / "sub").mkdir()
    (work / "sub" / "nested.csv").write_text("a,b\n1,2\n")

    found = find_artifacts(start_time=start, cwd=str(work), max_single_mb=25)
    names = {os.path.basename(a.path) for a in found}
    check("finds metrics.csv", "metrics.csv" in names)
    check("finds loss.png", "loss.png" in names)
    check("finds results.json", "results.json" in names)
    check("finds nested.csv (depth 1)", "nested.csv" in names)
    check("excludes file predating start_time", "old.csv" not in names)
    check("excludes oversize file", "big.npz" not in names,
          detail=f"max_single_mb=25, big.npz is 30MB")
    check("excludes files inside .git", "head.log" not in names)

    # Summary
    summary = summarize_artifacts(found)
    check("summarize_artifacts non-empty", summary.startswith(f"{len(found)} files"))

    # prepare_attachments: logs forced on failure
    fake_log = work / "combined.log"
    fake_log.write_text("a log")
    attach, skipped = prepare_attachments(
        artifacts=found, log_files=[str(fake_log)], job_failed=True,
    )
    check("logs included on failure", str(fake_log) in attach)
    check("artifacts included when under budget", any("metrics.csv" in a for a in attach))

    # Budget overflow → zip
    huge_artifacts = []
    for i in range(5):
        p = work / f"out{i}.csv"
        p.write_bytes(b"x" * (15 * 1024 * 1024))   # 15 MB each = 75 MB total
        huge_artifacts.append(ArtifactFile(path=str(p), size_bytes=p.stat().st_size, extension="csv"))
    attach2, skipped2 = prepare_attachments(
        artifacts=huge_artifacts, log_files=[str(fake_log)], job_failed=False,
        max_total_mb=20.0, attach_logs=True,
    )
    zip_in_attach = any(a.endswith("artifacts_overflow.zip") for a in attach2)
    zip_in_skipped = any(a.endswith("artifacts_overflow.zip") for a in skipped2)
    check("overflow handling produced a result", zip_in_attach or zip_in_skipped or len(skipped2) > 0,
          detail=f"attach={[os.path.basename(a) for a in attach2]} skipped_count={len(skipped2)}")

    # compress_artifacts standalone
    zpath = work / "manual.zip"
    out = compress_artifacts([str(work / "metrics.csv"), str(work / "results.json")], str(zpath))
    check("compress_artifacts produces a zip", out == str(zpath) and zpath.exists())

    shutil.rmtree(work, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — slurm
# ─────────────────────────────────────────────────────────────────────────────
def s6_slurm() -> None:
    section("6. slurm — availability detection and graceful fallback")
    from gpualert.slurm import (
        SlurmNotAvailableError,
        _parse_elapsed,
        _parse_exit_code,
        is_slurm_available,
        poll_job,
    )

    available = is_slurm_available()
    check("is_slurm_available returns a bool", isinstance(available, bool),
          detail=f"available={available}")

    if not available:
        try:
            poll_job(1, interval=0)
            check("poll_job raises SlurmNotAvailableError when sacct missing", False)
        except SlurmNotAvailableError:
            check("poll_job raises SlurmNotAvailableError when sacct missing", True)
        except Exception as e:
            check("poll_job raises SlurmNotAvailableError when sacct missing", False,
                  detail=f"got {type(e).__name__}: {e}")
    else:
        _emit(f"        {_ANSI['dim']}(skipping fallback check — sacct is available){_ANSI['reset']}")

    # parse_elapsed
    check("_parse_elapsed 1-02:03:04 == 1*86400+2*3600+3*60+4",
          _parse_elapsed("1-02:03:04") == 1 * 86400 + 2 * 3600 + 3 * 60 + 4)
    check("_parse_elapsed 02:03 == 2*60+3", _parse_elapsed("02:03") == 2 * 60 + 3)
    check("_parse_elapsed empty == 0", _parse_elapsed("") == 0.0)
    check("_parse_elapsed garbage == 0", _parse_elapsed("oops") == 0.0)

    # parse_exit_code
    check("_parse_exit_code '0:0' == 0", _parse_exit_code("0:0") == 0)
    check("_parse_exit_code '137:9' == 137", _parse_exit_code("137:9") == 137)
    check("_parse_exit_code '' == 0", _parse_exit_code("") == 0)
    check("_parse_exit_code 'bad' == 0", _parse_exit_code("bad") == 0)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — notifier (mocked SMTP)
# ─────────────────────────────────────────────────────────────────────────────
def s7_notifier() -> None:
    section("7. notifier — DryRun, mocked SMTP, never-raises contract")
    from gpualert.config import EmailConfig, GPUAlertConfig, SMTPConfig
    from gpualert.notifier.base import BaseNotifier
    from gpualert.notifier.email_notifier import (
        DryRunNotifier,
        EmailNotifier,
        get_notifier,
    )
    from gpualert.types import JobResult

    good_cfg = GPUAlertConfig(
        smtp=SMTPConfig(server="smtp.example.com", port=587,
                        username="u@example.com", password="x"),
        email=EmailConfig(from_address="u@example.com", to_addresses=["dest@example.com"]),
    )
    fail_result = JobResult(
        command="python train.py", job_id="job-1",
        start_time=datetime.now(), end_time=datetime.now(),
        duration_seconds=42, status="failed", exit_code=1,
        stdout_tail="some output", stderr_tail="RuntimeError: boom\n",
        error_summary="GPU out-of-memory\nSuggestion: smaller batch size",
    )
    ok_result = JobResult(
        command="python train.py", job_id="job-2",
        start_time=datetime.now(), end_time=datetime.now(),
        duration_seconds=10, status="success", exit_code=0,
    )

    # 7a. get_notifier dispatch
    check("get_notifier returns DryRun when dry_run=True",
          isinstance(get_notifier(good_cfg, dry_run=True), DryRunNotifier))
    check("get_notifier returns EmailNotifier by default",
          isinstance(get_notifier(good_cfg, dry_run=False), EmailNotifier))

    # 7b. unconfigured email notifier returns a helpful failure, doesn't raise
    bad_cfg = GPUAlertConfig()
    nr = EmailNotifier(bad_cfg).send(fail_result, [])
    check("unconfigured EmailNotifier returns success=False",
          nr.success is False and "config" in nr.message.lower(),
          detail=f"message={nr.message!r}")

    # 7c. DryRun prints and returns success=True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        nr = DryRunNotifier(good_cfg).send(fail_result, [])
    check("DryRun returns success=True", nr.success is True)
    check("DryRun output contains 'DRY RUN'", "DRY RUN" in buf.getvalue())
    check("DryRun output contains recipient", "dest@example.com" in buf.getvalue())

    # 7d. EmailNotifier with mocked SMTP — success path
    fake_log = Path(tempfile.mkdtemp(prefix="gpualert_notif_")) / "combined.log"
    fake_log.write_text("a tiny log")
    with patch("gpualert.notifier.email_notifier.smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.__exit__.return_value = False
        mock_smtp.return_value = instance
        nr = EmailNotifier(good_cfg).send(fail_result, [str(fake_log)])
    check("EmailNotifier success with mocked SMTP", nr.success is True,
          detail=f"message={nr.message!r}")
    check("EmailNotifier called login + send_message",
          instance.login.called and instance.send_message.called)

    # 7e. EmailNotifier with mocked SMTP — auth error
    import smtplib as _smtplib
    with patch("gpualert.notifier.email_notifier.smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        instance.__enter__.return_value = instance
        instance.__exit__.return_value = False
        instance.login.side_effect = _smtplib.SMTPAuthenticationError(535, b"auth failed")
        mock_smtp.return_value = instance
        nr = EmailNotifier(good_cfg).send(fail_result, [])
    check("EmailNotifier auth error → success=False",
          nr.success is False and "auth" in nr.message.lower())
    check("EmailNotifier auth error mentions App Password",
          "app password" in nr.message.lower())

    # 7f. Truly broken SMTP host — must NOT raise
    crash_cfg = GPUAlertConfig(
        smtp=SMTPConfig(server="this-host-does-not-exist.invalid",
                        port=587, username="u@x.com", password="p"),
        email=EmailConfig(from_address="u@x.com", to_addresses=["x@y.com"]),
    )
    try:
        with patch("gpualert.notifier.email_notifier.smtplib.SMTP",
                   side_effect=OSError("name resolution failed")):
            nr = EmailNotifier(crash_cfg).send(fail_result, [])
        check("EmailNotifier never raises on network error",
              nr.success is False, detail=f"message={nr.message!r}")
    except Exception as e:
        check("EmailNotifier never raises on network error", False,
              detail=f"raised {type(e).__name__}: {e}")

    # 7g. Subject/body builders
    base = DryRunNotifier(good_cfg)
    subj_fail = base._build_subject(fail_result)
    subj_ok = base._build_subject(ok_result)
    check("subject has FAILED marker on failure", "FAILED" in subj_fail)
    check("subject has COMPLETED marker on success", "COMPLETED" in subj_ok)
    body_fail = base._build_body(fail_result, [str(fake_log)])
    check("body includes job command", "python train.py" in body_fail)
    check("body includes error summary on failure", "out-of-memory" in body_fail.lower())
    check("body includes attachment listing", "ATTACHED FILES" in body_fail)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — CLI subprocess
# ─────────────────────────────────────────────────────────────────────────────
def s8_cli() -> None:
    section("8. CLI — gpualert binary via subprocess")
    env = os.environ.copy()
    env["HOME"] = str(_TEST_HOME)
    env["USERPROFILE"] = str(_TEST_HOME)
    env["NO_COLOR"] = "1"
    # Repointing HOME also repoints Python's user-site directory, which can
    # hide an editable install. Force the project root onto PYTHONPATH so the
    # subprocess can always import `gpualert` regardless of where HOME ends up.
    # Order: project root first (so we test THIS source), then the parent's
    # sys.path snapshot (so deps like pydantic remain importable under the
    # relocated HOME), then anything PYTHONPATH already had.
    parts = [str(_ROOT), _PYPATH_SNAPSHOT]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(p for p in parts if p)
    # Invoke via `python -m gpualert.cli` so we exercise the same import path
    # users will hit after `pip install gpualert`, without depending on the bin
    # shim resolving under the relocated HOME.
    base_cmd = [sys.executable, "-m", "gpualert.cli"]
    _emit(f"        {_ANSI['dim']}invoking: python -m gpualert.cli{_ANSI['reset']}")

    def run(args: List[str], timeout: int = 20) -> subprocess.CompletedProcess:
        return subprocess.run(
            base_cmd + args, env=env, capture_output=True, text=True, timeout=timeout,
        )

    # version
    p = run(["version"])
    check("gpualert version exits 0", p.returncode == 0)
    check("gpualert version prints 'gpualert <semver>'",
          "gpualert" in p.stdout and "0.1." in p.stdout,
          detail=f"stdout={p.stdout.strip()!r}")

    # --help
    p = run(["--help"])
    check("gpualert --help exits 0", p.returncode == 0)
    for cmd in ("run", "slurm", "config", "test-email", "logs", "version"):
        check(f"--help lists '{cmd}'", cmd in p.stdout)

    # config --check on unpopulated config — should report errors and exit nonzero.
    # First wipe the config file so we're testing the unconfigured path.
    cfg_path = _TEST_HOME / ".gpualert" / "config.toml"
    if cfg_path.exists():
        cfg_path.unlink()
    p = run(["config", "--check"])
    check("config --check on empty config exits non-zero", p.returncode != 0,
          detail=f"returncode={p.returncode} stdout={p.stdout[:120]!r}")
    check("config --check explains the problem",
          "empty" in p.stdout.lower() or "problem" in p.stdout.lower())

    # config --show
    p = run(["config", "--show"])
    check("config --show exits 0", p.returncode == 0)
    check("config --show masks password (shows '***' or empty)",
          '"***"' in p.stdout or '"password": ""' in p.stdout)

    # logs — empty state
    p = run(["logs"])
    check("gpualert logs exits 0 in empty state", p.returncode == 0)

    # run --no-notify: should run echo, write logs, exit 0, NOT contact SMTP
    p = run(["run", "--no-notify", "--", sys.executable, "-c", "print('cli-smoke-ok')"])
    check("run --no-notify exits 0 for successful command", p.returncode == 0,
          detail=f"returncode={p.returncode} stderr={p.stderr[:200]!r}")
    check("run --no-notify printed success status", "SUCCESS" in p.stdout)
    check("run --no-notify printed log paths", "Log files written" in p.stdout)

    # run --no-notify on failing command: exit non-zero
    p = run(["run", "--no-notify", "--", sys.executable, "-c", "import sys; sys.exit(7)"])
    check("run --no-notify exits non-zero for failed command",
          p.returncode != 0, detail=f"returncode={p.returncode}")

    # run --dry-run: should succeed even though SMTP is not configured (no actual SMTP call)
    p = run(["run", "--dry-run", "--", sys.executable, "-c", "print('dry-run-smoke')"])
    check("run --dry-run exits 0 (no real SMTP call)",
          p.returncode == 0, detail=f"returncode={p.returncode} stderr={p.stderr[:200]!r}")
    check("run --dry-run output mentions dry run", "DRY RUN" in p.stdout or "Dry run" in p.stdout)

    # slurm on a non-Slurm host
    from gpualert.slurm import is_slurm_available
    if not is_slurm_available():
        p = run(["slurm", "9999"])
        check("slurm on non-Slurm host exits non-zero", p.returncode != 0)
        check("slurm error message names sacct",
              "sacct" in p.stdout.lower() or "sacct" in p.stderr.lower())


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — end-to-end ML scenario
# ─────────────────────────────────────────────────────────────────────────────
def s9_end_to_end() -> None:
    section("9. end-to-end — simulate a real training job, dry-run notify")
    from gpualert.artifacts import find_artifacts, prepare_attachments
    from gpualert.config import EmailConfig, GPUAlertConfig, SMTPConfig
    from gpualert.launcher import run_job
    from gpualert.notifier.email_notifier import DryRunNotifier

    work = Path(tempfile.mkdtemp(prefix="gpualert_e2e_"))
    train_py = work / "fake_train.py"
    train_py.write_text(
        "import csv, json, sys\n"
        "from pathlib import Path\n"
        "out = Path(sys.argv[0]).resolve().parent\n"
        "with open(out / 'metrics.csv', 'w', newline='') as f:\n"
        "    w = csv.writer(f); w.writerow(['epoch','loss','accuracy'])\n"
        "    for i in range(5): w.writerow([i, 0.5/(i+1), 0.7 + i*0.05])\n"
        "(out / 'results.json').write_text(json.dumps({'final_acc': 0.92}))\n"
        "print('Epoch 5 accuracy: 0.92')\n"
        "print('Epoch 5 loss: 0.123')\n"
        "print('Training complete')\n"
    )

    result = run_job([sys.executable, str(train_py)], cwd=str(work))
    check("e2e: training succeeded", result.is_success(),
          detail=f"status={result.status} exit={result.exit_code}")
    check("e2e: metrics extracted into summary", "0.92" in (result.error_summary or ""),
          detail=f"error_summary={result.error_summary!r}")

    artifacts = find_artifacts(start_time=result.start_time, cwd=str(work))
    names = {os.path.basename(a.path) for a in artifacts}
    check("e2e: metrics.csv discovered", "metrics.csv" in names)
    check("e2e: results.json discovered", "results.json" in names)

    attach, skipped = prepare_attachments(
        artifacts=artifacts, log_files=result.log_files(),
        job_failed=False, attach_logs=True,
    )
    check("e2e: log files in attachment list",
          any(p in attach for p in result.log_files()))
    check("e2e: artifacts in attachment list",
          any("metrics.csv" in a for a in attach) and any("results.json" in a for a in attach))

    good_cfg = GPUAlertConfig(
        smtp=SMTPConfig(username="u@example.com", password="x"),
        email=EmailConfig(from_address="u@example.com",
                          to_addresses=["dest@example.com"], subject_prefix="[E2E]"),
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        nr = DryRunNotifier(good_cfg).send(result, attach)
    check("e2e: dry-run notification succeeded", nr.success is True)
    check("e2e: dry-run body shows attachments", "metrics.csv" in buf.getvalue())

    shutil.rmtree(work, ignore_errors=True)

    # Failure scenario — make sure logs are still produced and email body has stderr
    fail_py = Path(tempfile.mkdtemp(prefix="gpualert_e2e_fail_")) / "boom.py"
    fail_py.write_text("import sys\nsys.stderr.write('RuntimeError: training diverged: loss is nan\\n')\nsys.exit(1)\n")
    result = run_job([sys.executable, str(fail_py)])
    check("e2e-fail: status == failed", result.is_failed())
    check("e2e-fail: logs still on disk", all(os.path.isfile(p) for p in result.log_files()))
    check("e2e-fail: stderr_tail has the error", "nan" in result.stderr_tail.lower())
    check("e2e-fail: error_summary classified the failure", bool(result.error_summary))
    shutil.rmtree(fail_py.parent, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────
SECTIONS: Dict[str, Callable[[], None]] = {
    "config":      s1_config,
    "log_manager": s2_log_manager,
    "launcher":    s3_launcher,
    "parse_errors": s4_parse_errors,
    "artifacts":   s5_artifacts,
    "slurm":       s6_slurm,
    "notifier":    s7_notifier,
    "cli":         s8_cli,
    "end_to_end":  s9_end_to_end,
}


def main() -> int:
    if ARGS.list:
        print("Available sections:")
        for k in SECTIONS:
            print(f"  {k}")
        return 0

    _emit(f"{_ANSI['bold']}GPUAlert local test harness{_ANSI['reset']}")
    _emit(f"{_ANSI['dim']}Test HOME: {_TEST_HOME}{_ANSI['reset']}")
    _emit(f"{_ANSI['dim']}Log file:  {_LOG_PATH}{_ANSI['reset']}")
    _emit(f"{_ANSI['dim']}Python:    {sys.version.split()[0]} at {sys.executable}{_ANSI['reset']}")

    to_run = SECTIONS
    if ARGS.only:
        if ARGS.only not in SECTIONS:
            _emit(f"{_ANSI['red']}No such section: {ARGS.only}. Use --list.{_ANSI['reset']}")
            return 2
        to_run = {ARGS.only: SECTIONS[ARGS.only]}

    started = time.time()
    for name, fn in to_run.items():
        try:
            fn()
        except Exception as e:
            check(f"{name} crashed", False, detail=f"{type(e).__name__}: {e}")
            _LOG_FILE.write(traceback.format_exc() + "\n")
    elapsed = time.time() - started

    _emit("")
    bar = "═" * 70
    _emit(f"{_ANSI['bold']}{bar}{_ANSI['reset']}")
    if _FAIL == 0:
        _emit(f"{_ANSI['green']}{_ANSI['bold']}  ALL {_PASS} CHECKS PASSED{_ANSI['reset']}  "
              f"({elapsed:.1f}s)")
    else:
        _emit(f"{_ANSI['red']}{_ANSI['bold']}  {_FAIL} FAILED, {_PASS} PASSED{_ANSI['reset']}  "
              f"({elapsed:.1f}s)")
        _emit("")
        _emit(f"{_ANSI['red']}Failed checks:{_ANSI['reset']}")
        for name, detail in _FAIL_DETAILS:
            _emit(f"  - {name}")
            _emit(f"      {_ANSI['dim']}{detail}{_ANSI['reset']}")
    _emit(f"{_ANSI['bold']}{bar}{_ANSI['reset']}")
    _emit(f"{_ANSI['dim']}Full log: {_LOG_PATH}{_ANSI['reset']}")

    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        _LOG_FILE.close()
        if not ARGS.keep_tmp:
            shutil.rmtree(_TEST_HOME, ignore_errors=True)
    sys.exit(rc)
