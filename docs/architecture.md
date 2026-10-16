# Architecture

GPUAlert is built around three guarantees: logs always land on disk, logs always get attached to
failure emails, and the notifier never raises. The module layout reflects that.

```
gpualert/
├── cli.py              # Typer entrypoint
├── launcher.py         # subprocess wrapper for `gpualert run`
├── slurm.py            # sacct poller for `gpualert slurm`
├── log_manager.py      # log directory creation + thread-safe writes
├── artifacts.py        # post-job file scan + attachment budget
├── parse_errors.py     # regex-based failure classification + metrics extraction
├── config.py           # pydantic models, TOML load/save, offline validation
├── config_manager.py   # interactive setup wizard
├── types.py            # JobResult, ArtifactFile, NotificationResult, SlurmJobInfo
└── notifier/
    ├── base.py         # abstract base, shared subject/body builders
    └── email_notifier.py  # SMTP + DryRun notifier
```

## The log guarantee

Every code path that produces a `JobResult` goes through `log_manager.create_job_log_dir`, which:

1. Creates `~/.gpualert/logs/<YYYYMMDD_HHMMSS>_<job_short>/` with mode 700.
2. Touches `stdout.log`, `stderr.log`, `combined.log` so the files exist immediately.
3. Returns the directory path; the caller stores absolute file paths on `JobResult` *before*
   doing any other work.

Only then does `launcher.run_job` start the subprocess. If `Popen` fails with `FileNotFoundError`,
the log files are already on disk and ready to receive a `[SYSTEM] ERROR` message. If the user
hits Ctrl+C, the same applies. There is no path where a `JobResult` is returned without log files
on disk.

`write_to_log` is the only writer. It takes an optional `threading.Lock` so the streaming readers
in `launcher.py` can write concurrently to `combined.log` without interleaving. It catches every
exception silently — a log write failure must never mask the actual job result.

## Subprocess streaming

`launcher.run_job` starts the child with binary, unbuffered pipes and hands each one to a
`capture.StreamReader` running in a daemon thread. The reader decodes the bytes itself, with an
incremental decoder, so:

- no byte sequence can stop the stream: invalid bytes become U+FFFD and are counted;
- a multi-byte character or a CRLF split across two reads comes out whole;
- a bare `\r` still ends a line, as universal newlines did, so progress bars stay readable;
- a line longer than 64 KiB is written in pieces instead of being held in memory.

Each line gets an `HH:MM:SS` prefix and is written to its own log and to `combined.log` through a
`LogSink`, which keeps the three files open for the job and writes without a userspace buffer.

Anything that goes wrong is reported, not swallowed: decoding replacements, a reader that stops
early (it keeps draining the pipe so the child cannot block), a log that rejects writes (disk full),
and a pipe still held open by a background process after the child exits. Each becomes a note in
the result, the log footer and the email.

After `proc.wait()` (or `proc.kill()` on timeout) both readers are joined under one 5-second
deadline. If the wrapper itself fails after the child started, the child is killed and reaped.

## Where logs go

`$GPUALERT_LOG_DIR` if set, else `~/.gpualert/logs`. If that cannot be created or written, a
private `mkdtemp` directory (mode 700, random name); if that fails too, memory only, and the report
still carries the stream tails. The report says which of these happened.

## Slurm polling

`slurm.poll_job` does not submit jobs. It assumes you already ran `sbatch` and just need a
notification when the job ends. The loop calls `sacct -j <id> -X --parsable2 --noheader` every
`interval` seconds, parses the pipe-delimited output, and breaks when the state is in
`TERMINAL_STATES`.

State to status mapping:

| Slurm state          | GPUAlert status |
|----------------------|-----------------|
| `COMPLETED`          | `success`       |
| `TIMEOUT`            | `timeout`       |
| `FAILED`, `CANCELLED`, `NODE_FAIL`, `PREEMPTED`, `OUT_OF_MEMORY` | `failed` |
| anything else        | `failed`        |

If `sacct` is missing entirely, `is_slurm_available()` returns false and the CLI errors out
before polling starts.

`ExitCode` is `EXIT:SIGNAL`. A non-zero EXIT is the exit code; `0:N` with N a signal becomes -N,
which the CLI turns into 128+N like a shell. A result that is not `success` never exits 0. The
`--timeout` clock is monotonic and the poll interval is at least one second.

## Artifact scanning and the attachment budget

After the job finishes, `artifacts.find_artifacts` walks the job's launch directory (recorded by
`run_job`, depth-limited, skipping cache/build dirs and every hidden directory). A file is collected
only if it matches a pattern and is:

- a regular file (pipes, sockets and devices are refused);
- owned by the user running the wrapper;
- modified between the job's start and end (plus 2 s for coarse filesystem timestamps);
- inside the run directory (a symlink pointing out of it is refused);
- no larger than `max_single_file_mb`.

Each file that matched a pattern but failed a rule is recorded by reason and reported in the email's
NOTES section. Other users' files are counted, never named.

It cannot tell apart two of the same user's jobs writing into one directory; that needs to know which
process created each file. Give concurrent jobs their own directories.

`prepare_attachments` then packs the list into the email:

1. Log files are added first. On failure, this is unconditional; on success, it follows
   `attach_logs_on_success`.
2. Artifacts are added in size order until `max_total_mb` would be exceeded.
3. Whatever didn't fit goes into `artifacts_overflow.zip` in the job's log directory (never the
   working directory). If the zip itself busts the budget, its contents are listed as skipped.

Files are opened again when they are attached, zipped or parsed for metrics. Those reads go through
`safe_io.open_verified`, which refuses a symlink, a non-regular file, another user's file, and (on
Linux and macOS, checked on the open descriptor) anything outside the run and log directories. A file
swapped after the scan is refused and listed.

The email is bounded in size and never silently drops files: everything is either attached or named
in NOTES.

## Error classification

`parse_errors.parse_errors` runs a priority-ordered list of regexes against the stdout+stderr tail
and returns the first match with a short label and a one-line suggestion. The patterns cover
common ML/HPC failure modes: CUDA OOM, NCCL, NaN loss, OOMKiller, missing modules, segfaults,
generic tracebacks. The result becomes `JobResult.error_summary` and is included in the failure
email body.

On success, `extract_success_metrics` runs a different set of regexes to pull `accuracy`, `loss`,
`F1`, `mAP`, etc. from the stdout tail. The top four metrics become the success-email subtitle.

## Notifier contract

`BaseNotifier.send(result, attachments)` returns a `NotificationResult` and must not raise.
`EmailNotifier` enforces this with a final `except Exception` guard — even ill-formed config or
broken DNS resolves to `success=False` with a human-readable `message`. The CLI prints the
message and continues; it exits with the *job's* status, not the notifier's.

`DryRunNotifier` prints the email it would have sent and returns `success=True`. Useful for
debugging the body builder, smoke-testing in CI, and the `--dry-run` flag.

## Configuration loading

`config.load_config` always returns a `GPUAlertConfig`. If the file is missing, it creates one
with defaults. If the file is corrupt, it logs a warning and returns defaults. There is no path
where `load_config` raises — startup is never blocked by config problems.

`validate_config` is offline. It checks that required fields are populated, that the port is in
range, and that recipient addresses look syntactically valid. It does not connect anywhere; for
that, use `gpualert test-email`.

## Secret storage (0.1.4+)

SMTP passwords are never persisted in `config.toml`. `gpualert.secrets.load_secret`
resolves the password at the SMTP boundary from a three-tier chain:

1. `GPUALERT_EMAIL_PASSWORD` environment variable (headless / CI / explicit override).
2. OS keyring — Windows Credential Locker, macOS Keychain, Linux Secret Service via `keyring`.
3. `~/.gpualert/secret.enc` — Fernet ciphertext whose key is derived per-machine via
   PBKDF2-HMAC-SHA256 (600,000 iterations, OWASP 2023/2025) from `/etc/machine-id`,
   `IOPlatformUUID`, or `MachineGuid`. When no stable identifier exists (containers), a
   per-install random key file is used instead. Copying `secret.enc` to another host
   fails to decrypt with a helpful "re-run `gpualert config --init`" message.

`config.load_config` runs a one-shot silent migration on upgrade: any plaintext password
in `config.toml` is moved into the secret store, `password_backend` is set to `"keyring"`
or `"file"`, and the password field is blanked on disk. Existing 0.1.3 configs upgrade
in place with one stdout notice and zero user action.

## Uninstall + purge

`pip uninstall` runs no post-uninstall code — documented pip limitation, not a bug we
can fix. `gpualert uninstall` (alias `purge`) exists to scrub the pieces pip cannot:
the OS keyring entry (via `keyring.delete_password`), `~/.gpualert/secret.enc`
(best-effort overwrite then `unlink`), `key.bin`, `salt.bin`, and the config directory.
`--keep-logs` preserves `~/.gpualert/logs/`. The wizard prints a reminder at the end
of `config --init` so users learn the correct removal order.
