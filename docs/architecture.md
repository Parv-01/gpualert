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

`launcher.run_job` starts the child with piped stdout/stderr, then spawns two daemon threads:

- `reader_thread(stdout, stdout.log, "OUT")`
- `reader_thread(stderr, stderr.log, "ERR")`

Each thread reads lines, timestamps them (`HH:MM:SS`), writes to both the stream-specific log and
the merged `combined.log`. The merged log is the one we usually attach to emails because it
preserves interleaving.

After `proc.wait()` (or `proc.kill()` on timeout) the threads are joined with a 5-second cap to
avoid hanging on stuck pipes.

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

## Artifact scanning and the attachment budget

After the job finishes, `artifacts.find_artifacts` walks the working directory (depth-limited,
skipping known cache/build dirs) and collects files newer than `start_time` that match the
configured glob patterns. Anything larger than `max_single_file_mb` is dropped.

`prepare_attachments` then packs the list into the email:

1. Log files are added first. On failure, this is unconditional; on success, it follows
   `attach_logs_on_success`.
2. Artifacts are added in size order until `max_total_mb` would be exceeded.
3. Whatever didn't fit goes into `artifacts_overflow.zip` next to the log files. If the zip itself
   busts the budget, it's listed under `skipped` and the user is told.

This means the email is bounded in size and never silently drops files — you either see them
attached, or you see them in the `Skipped` line of the email body.

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
