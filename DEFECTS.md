# Defect ledger

One entry per known defect. A defect is closed only when its strict-xfail test
flips to a pass and the marker is deleted in the same commit as the fix. The
harness that pins them lives in `tests/robustness/`; see its README for the
convention.

Status values: **open**, **fixed in X.Y.Z**, **partly fixed in X.Y.Z** (the
entry says which part is left and where it is planned).

| Id | Summary | Severity | Affects | Status |
|---|---|---|---|---|
| D1 | a non-UTF-8 byte silently discards all following output | critical | ≤ 0.1.5 | fixed in 0.1.6 |
| D2 | SIGTERM / SIGHUP unhandled: no result, no footer, no notification | critical | ≤ 0.1.5 | open, planned 0.2.0 |
| D3 | only the direct child is killed; launcher workers are orphaned | critical | ≤ 0.1.5 | open, planned 0.2.0 |
| D4 | artifact scan claims files this job did not create | critical | ≤ 0.1.5 | partly fixed in 0.1.6 |
| D5 | `run_job` raises when the log directory cannot be created | critical | ≤ 0.1.5 | fixed in 0.1.6 |
| D6 | `get_tail()` loads the whole file into memory | serious | ≤ 0.1.5 | fixed in 0.1.6 |
| D7 | no log rotation or size cap | serious | ≤ 0.1.5 | open, planned 0.2.0 |
| D8 | no GPU awareness | serious | ≤ 0.1.5 | open, planned 0.3.0 |
| D9 | no cross-job registry | serious | ≤ 0.1.5 | open, planned 0.4.0 |
| D10 | timeout sends SIGKILL with no SIGTERM first | serious | ≤ 0.1.5 | open, planned 0.2.0 |
| D11 | a FIFO named like an output hangs the wrapper after the job | serious | ≤ 0.1.5 | fixed in 0.1.6 |
| D12 | a non-executable command exits 1 as a launcher error instead of 126 | minor | ≤ 0.1.5 | fixed in 0.1.6 |
| D13 | `run_job` raises when started from a deleted directory | serious | ≤ 0.1.5 | fixed in 0.1.6 |
| D14 | the artifact scan reads `~/.gpualert` and other hidden directories | critical | ≤ 0.1.5 | fixed in 0.1.6 |
| D15 | attachments are re-opened by path, so a post-scan symlink swap is followed | serious | ≤ 0.1.5 | fixed in 0.1.6 |
| D16 | the overflow zip can be written into the working directory | minor | ≤ 0.1.5 | fixed in 0.1.6 |
| D17 | files left out of the email are not named in it | serious | ≤ 0.1.5 | fixed in 0.1.6 |
| D18 | `gpualert slurm` exits 0 for a cancelled, signalled or node-failed job | critical | 0.1.6 kit | fixed in 0.1.6 |
| D19 | the Slurm poll loop can spin on `sacct` and never reach `--timeout` | serious | ≤ 0.1.5 | fixed in 0.1.6 |

---

## D1 — a non-UTF-8 byte silently discards all following output

**Symptom.** The job prints a raw byte that is not valid UTF-8 (C and CUDA
extensions do this routinely). Everything the job writes after that byte is
missing from `stdout.log` and `combined.log`, and the result still says
`success`.

**Cause.** `Popen(..., text=True)` decodes strictly. The first bad byte raises
`UnicodeDecodeError` inside the reader thread, its bare `except Exception: pass`
swallows it, and the thread exits. Status depends only on the exit code.

**Reproduction.** `faults.emits_invalid_utf8()`: `BEFORE` survives, `AFTER 4`
does not.

**Tests.** `test_output_shapes.py::TestInvalidUTF8`, `tests/test_capture.py`.

**Fix (0.1.6).** `gpualert/capture.py`: pipes are read as bytes and decoded
incrementally with a counting replacement handler, so no byte sequence can stop
the stream and each replacement is reported in the result notes and the log
footer. A reader that fails for any other reason records why and keeps
draining its pipe so the child cannot block. Measured side effect: capture is
5–7× faster on chatty jobs (200k short lines: 5.9 s → 1.1 s; 32 MB: 24.8 s →
3.5 s), because the log files are opened once instead of once per line.

## D2 — SIGTERM / SIGHUP unhandled

**Symptom.** `scancel`, Slurm preemption and a dropped SSH session kill the
wrapper at rc −15 / −1 with no footer and no notification.

**Tests.** `test_signals.py::TestSigterm`, `TestSighup`.

## D3 — orphaned workers

**Symptom.** Killing the wrapper, or a timeout, leaves grandchildren running.
A torchrun worker keeps its GPU memory after the wrapper is gone.

**Tests.** `test_signals.py::test_sigint_does_not_orphan_the_job`,
`test_process_shapes.py::TestGrandchildren`.

## D4 — artifact scan claims files this job did not create

**Symptom.** `find_artifacts` takes any file in the working directory whose
name matches a pattern and whose mtime is after the job started. On a shared
directory that attaches, and emails, a co-tenant's outputs.

**Measured.** Job A attached `jobB_other_user_results.json`.

**Tests.** `test_concurrency.py::TestArtifactScoping`,
`test_artifact_safety.py::TestOwnership`, `TestRunWindow`, `tests/test_artifact_scoping.py`.

**Fixed in 0.1.6 (the cross-user part).** A file qualifies only if it is owned
by the user running the wrapper, sits inside the job's launch directory
(recorded at launch, not re-read later), and was modified inside the run window
(start to end plus 2 s for coarse timestamps). Files owned by someone else are
counted in the report, never named.

**Still open (the same-user part).** Two of one user's jobs writing into one
directory, or a pre-existing file rewritten during the run, look identical to
the job's own output. Telling them apart needs to know which process created
each file. `inotify` cannot say (it carries no pid), `fanotify` needs root, and
`LD_PRELOAD` does not work on static binaries, so this waits for the
process-tree work in 0.2.5. The two strict xfails in `test_concurrency.py` pin
it. Until then, give concurrent jobs their own directories.

## D5 — `run_job` raises when the log directory cannot be created

**Symptom.** An unwritable or over-quota home makes `run_job` raise
`PermissionError` (or `NotADirectoryError`) before the job starts, breaking the
never-raises contract. On HPC a full home is normal.

**Tests.** `test_filesystem.py::TestUnwritableHome`, `tests/test_log_fallback.py`.

**Fix (0.1.6).** Same path for `gpualert slurm`, whose monitor log had the
identical problem. `$GPUALERT_LOG_DIR` if set, else `~/.gpualert/logs`; if that
cannot be created *or written* (the header write is part of the attempt, which
is what catches a home over quota), a private `mkdtemp` directory; if that
fails too, memory only, with the stream tails kept for the report. Every
fallback adds a note naming where the logs went.

## D6 — `get_tail()` reads the whole file

**Measured.** A 25 MB log costs about 52 MB extra to read 50 lines.

**Tests.** `test_output_shapes.py::TestTailReader::test_tail_memory_is_independent_of_file_size`,
`tests/test_tail_reader.py`.

**Fix (0.1.6).** Reads backwards in 64 KiB blocks and stops after enough line
breaks or 2 MiB, whichever comes first. Peak memory for 50 lines of a 21 MB log
is under 1 MB. Output is identical to the old reader for any file inside the
budget: checked against it on 20,000 random files mixing CR, LF, CRLF, invalid
bytes and multi-byte characters with block sizes down to one byte.

## D7 — no log rotation or size cap

**Tests.** `test_output_shapes.py::TestVolume::test_log_growth_is_bounded`.

Deferred on purpose. A cap has to keep the head and the tail and survive a
wrapper crash without losing the tail, which is the part that explains the
failure. That needs the supervisor work in 0.2.0.

## D8, D9

Absences rather than defects. Covered by the 0.3.0 GPU observer and the 0.4.0
registry designs.

## D10 — no graceful shutdown ladder

Found by the harness: `test_a_child_ignoring_sigterm_is_escalated` passed,
which disproved the assumption behind it. The timeout path calls
`proc.kill()` directly.

**Tests.** `test_process_shapes.py::TestShutdownLadder`.

## D11 — a FIFO named like an output hangs the wrapper

**Symptom.** Anyone who can write to the run directory creates a named pipe
called `metrics.json`. `find_artifacts` accepts it (it never checks for a
regular file), and `extract_metrics` then blocks forever in `open()`, so the
notification is never sent and the wrapper never exits.

**Reproduction.** `os.mkfifo("metrics.json")` in the run directory; the
metrics reader is still blocked after 3 s.

**Tests.** `test_artifact_safety.py::TestFileTypes`, `tests/test_artifact_scoping.py`
(including an end-to-end CLI run with a FIFO in the directory, under a timeout).

**Fix (0.1.6).** Only regular files qualify (`S_ISREG` after following an
in-tree link). Pipes, sockets and devices are listed in the report.

## D12 — non-executable command reported as a launcher error

**Symptom.** `gpualert run -- ./train.sh` without the execute bit records
"Unexpected launcher error: PermissionError" with exit code −99, which the CLI
maps to 1. A shell reports 126 for the same command.

**Tests.** `test_contracts.py::TestExitCodeFidelity::test_a_non_executable_command_reports_126`,
`tests/test_exec_errors.py`.

**Fix (0.1.6).** Exec failures map to the shell's codes: 126 for EACCES,
EPERM, ENOEXEC and EISDIR, 127 for a missing program or a missing `#!`
interpreter, each with a message that names the actual cause. A missing
working directory stays a launcher error (exit 1).

## D13 — `run_job` raises from a deleted working directory

**Symptom.** The log header calls `os.getcwd()`, which raises
`FileNotFoundError` once the directory has been removed (a common result of
cleaning a scratch directory while a shell is still inside it).

**Tests.** `test_filesystem.py::TestDeletedWorkingDirectory`, `tests/test_log_fallback.py`.

**Fix (0.1.6).** The header records `<unavailable: FileNotFoundError>` instead
of raising, and `JobResult.cwd` is left empty so the artifact scan is skipped
rather than run against whatever directory a later lookup resolves to.

## D14 — the artifact scan reads tool state and hidden directories

**Symptom.** A job started from `$HOME` scans `~/.gpualert/logs/` three levels
deep and attaches every log written during the run, including other jobs'.
Hidden directories such as `.ssh`, `.aws` and `.config` are scanned like any
other.

**Tests.** `test_artifact_safety.py::TestToolState`, `tests/test_artifact_scoping.py::TestHidden`.

**Fix (0.1.6).** Hidden directories are never entered. Hidden files are only
considered when a pattern itself starts with a dot (`--attach '.results*'`).

## D15 — attachments re-opened by path at send time

**Symptom.** The scan rejects outward symlinks, but the notifier opens each
attachment again by path. A file replaced by a symlink between the scan and the
send is followed, so a file outside the run directory can still be attached.

**Tests.** `test_artifact_safety.py::TestReadTimeContainment`, `tests/test_safe_io.py`.

**Fix (0.1.6).** `gpualert/safe_io.py`. The notifier, the overflow zip and the
metrics reader open files with `O_NOFOLLOW | O_NONBLOCK`, then check the
descriptor: regular file, owned by the euid, and (for the notifier and zip)
really located inside the run or log directory. On Linux and macOS the real
location comes from the descriptor (`/proc/self/fd`, `F_GETPATH`), so an
intermediate directory swapped for a symlink is caught, not only the last
component. Refusals are listed in the notifier's skip message.

## D16 — overflow zip written into the working directory

**Symptom.** When `prepare_attachments` has no log file to anchor on, it writes
`artifacts_overflow.zip` into the current directory. That can overwrite a file
of the same name, and the next run in that directory picks the zip up as an
artifact (`*.zip` is a default pattern). Found because the harness left the zip
in the repo root.

**Tests.** `test_contracts.py::TestNonSilentBudget::test_overflow_zip_is_never_written_into_the_working_directory`,
`tests/test_safe_io.py::TestOverflowZip`.

**Fix (0.1.6).** The zip is only built inside the job's log directory. With no
log directory the overflow is returned as skipped, which the report names.
Members with the same basename now get distinct names (`metrics_2.csv`)
instead of duplicate zip entries.

## D17 — files left out of the email are not named in it

**Symptom.** The paper's third primitive is the non-silent budget: anything not
attached is listed. `prepare_attachments` does return the list, but `cli.run`
discarded it, and `find_artifacts` dropped files over the per-file limit
without recording them at all. The email named only what was attached, so an
over-limit checkpoint simply vanished from the report.

**Fix (0.1.6).** The scan records every pattern match it leaves out, by reason;
the CLI turns those and the budget overflow into NOTES lines. Other users'
files are counted, not named.

**Tests.** `tests/test_artifact_scoping.py::TestCliWiring`.

## D18 — `gpualert slurm` exits 0 for a failed job

**Symptom.** Found while checking the 0.1.6 exit-status change against Slurm's
own states. `_parse_exit_code` kept only the EXIT half of `EXIT:SIGNAL`, so a job
killed by a signal (`0:9`, `0:15`) came back as 0. The new `_shell_exit_code`
passed 0 through, and `gpualert slurm` exited 0 for a cancelled job. The same
happened for NODE_FAIL and PREEMPTED, which Slurm reports as `0:0`. A script
that branches on the exit status would treat all of them as success.

**Fix (0.1.6).** `0:N` with N a real signal becomes -N (128+N at the CLI), and a
job whose status is not success never exits 0: the CLI maps a zero code on a
failed result to 1.

**Tests.** `tests/test_slurm_exit.py`.

## D19 — Slurm poll loop can spin and miss its timeout

**Symptom.** `poll_job` added `interval` to an elapsed counter instead of
reading a clock. At `--interval 0` (accepted by the CLI) the counter never moved,
so `--timeout` was never reached and `sacct` ran in a tight loop against the
site's Slurm database for as long as the job stayed unknown. Negative intervals
made it worse. Time spent inside `sacct` (up to 15 s per call) was never counted
either.

**Fix (0.1.6).** Elapsed time comes from `time.monotonic()`, and the CLI refuses
an interval below 1 second.

**Tests.** `tests/test_slurm_exit.py`.
