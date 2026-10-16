# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.6] — 2026-10-16

Correctness and data-safety release. Everyone on 0.1.5 should upgrade. Defect
ids refer to `DEFECTS.md`, which has the reproduction and the test for each.

### Were you affected?

- **Lost log output (D1).** If a job printed a byte that is not valid UTF-8
  (common from C and CUDA extensions), everything after it was missing from
  the logs and the email, and the run still reported success.
- **Other people's files in your email (D4, D14).** If your job shared a
  working directory with another user, any of their output files written
  during your run could be attached to your notification. If you ran jobs from
  your home directory, other jobs' logs under `~/.gpualert/logs` could be
  attached too. Nothing was sent anywhere except your own configured
  recipients, but if that is a shared list, check past emails.
- **Wrong exit status.** `gpualert run` exited 1 for every failure, and with
  only the exit-status fix applied `gpualert slurm` would have exited 0 for a
  cancelled job (D18). Scripts that branch on the status should upgrade.

### Fixed

- `gpualert run` and `gpualert slurm` exit with the job's own status: N stays
  N, death by signal N gives 128+N (137 for SIGKILL, 139 for a segfault), a
  missing command or a missing `#!` interpreter gives 127, a command that exists
  but cannot be executed gives 126 (D12), `--timeout` gives 124 and Ctrl+C
  gives 130. A failed job never exits 0: Slurm's `0:N` signal codes and
  NODE_FAIL / PREEMPTED map to failure statuses (D18). A failed notification
  never changes the exit status.
- Output is captured as bytes and decoded in the wrapper, so no byte sequence
  can stop the log (D1). Replacements, a reader that stops early, a log that
  cannot be written, and a pipe held open by a background process are each
  reported in the email and the log footer. Capture is also 5–7× faster on
  chatty jobs, because the logs are opened once per job instead of once per
  line.
- Artifacts are limited to regular files owned by you, inside the job's
  launch directory, modified during the run (D4). Hidden directories such as
  `~/.gpualert`, `.ssh` and `.git` are never scanned (D14). Pipes and sockets
  are refused, so a FIFO named like an output can no longer hang the wrapper
  (D11). Symlinks that leave the run directory are skipped.
- Attachments, the overflow zip and metric files are re-checked when they are
  opened, so a file swapped for a symlink or a pipe after the scan is refused
  (D15). The overflow zip is never written into the working directory (D16).
- Every file left out of the email is now listed in it: over the per-file
  limit, over the total budget, refused at send time, or (counted, not named)
  owned by another user (D17).
- A read-only or over-quota home no longer makes `run_job` raise. Logs fall
  back to a private temp directory, or to memory, and the email says where they
  went (D5). Starting from a deleted directory no longer raises either (D13).
- `get_tail()` reads from the end of the file, so a multi-GB log costs at most
  2 MiB to summarise (D6).
- `gpualert slurm` uses a real clock for `--timeout` and refuses
  `--interval` below 1, so it can no longer call `sacct` in a tight loop (D19).
- `artifacts.max_single_file_mb`, `max_total_mb` and `scan_depth` are read from
  the config. They were documented but ignored (D20). `artifacts.patterns` and
  `log_dir` are still not read; the docs now say so.
- A command with a newline in it (any multi-line `python -c` script) or an
  output file with one in its name no longer makes the whole email fail (D21).
- A read-only or missing home no longer stops `gpualert run` before the job
  starts; the config falls back to defaults (D22).
- Text from the job (the command, file names, error lines) can no longer crash
  the CLI through Rich markup. `data.dirs=[/scratch/a]` in a command used to
  stop the job from starting (D23).
- The exit status is fixed the moment the job ends. Nothing after that, not a
  failing artifact scan, a Ctrl+C while the email is sending, or a closed
  stdout pipe (`gpualert run ... | head`), can change it any more.
- If the wrapper itself fails after the job started, the job is stopped
  instead of being left running with nobody reading its output.
- When the disk fills mid-run, the report's tails and error class come from
  the last lines in memory instead of the stale part of the log on disk.
- Run notes (log fallback, decoding replacements) are printed on the console
  too, so `--no-notify` users see them.
- The release branch passes CI's `black --check` again.

### Added

- `GPUALERT_LOG_DIR` to put logs somewhere other than `~/.gpualert/logs`.
- `JobResult.cwd`, the job's absolute launch directory.
- `tests/robustness/`, the fault-injection harness, and `DEFECTS.md`. CI runs
  the quick pass on every push and the full pass nightly and on tags. The test
  suite no longer writes into the developer's real `~/.gpualert`.
- `CITATION.cff`, `llms.txt`, a Paper section with BibTeX in the README, and a
  Paper link on the PyPI page.

### Known limits

- Two of your own jobs writing into the same directory still cannot be told
  apart: each may attach the other's outputs. Give concurrent jobs their own
  directories. Fixing it needs to know which process created each file
  (planned for 0.2.5).
- SIGTERM from `scancel` or preemption, orphaned launcher workers, the
  graceful shutdown ladder and log size caps (D2, D3, D10, D7) are 0.2.0 work.

## [0.1.5] — 2026-07-10

SMTP reliability release, driven by "Server not connected" failures on shared
GPU clusters where every user exits through one NAT IP and providers drop
simultaneous connections.

### Added

- **`smtp.use_ssl` config flag** — implicit SSL (SMTPS) via `smtplib.SMTP_SSL`,
  for port 465. Useful on clusters whose firewalls interfere with STARTTLS on
  587. The wizard auto-enables it when port 465 is entered; `config --check`
  flags a 465/`use_ssl = false` mismatch.

### Changed

- **SMTP retry reworked**: 4 attempts (was 2) with exponential backoff and
  random jitter (3 → 6 → 12 s) so parallel jobs desynchronize instead of
  retrying in lockstep. The retry loop now also covers `SMTPConnectError`,
  `ConnectionResetError`, and `TimeoutError` — transient drops that previously
  bypassed the retry entirely. Failure message now explains the shared-IP
  cause and suggests the port 465 fallback.
- SMTP session logic extracted into `EmailNotifier._deliver()`.

## [0.1.4] — 2026-07-07

Three-feature release: encrypted secret storage (Feature 1), wider artifact
patterns + metric parsing (Feature 4), and a first-class `gpualert uninstall`
command (Feature 3). Existing configs auto-migrate silently on first
`load_config()` after upgrade — one stdout line, nothing to configure.

### Added

- **Encrypted secret storage** (`gpualert/secrets.py`, `gpualert/crypto.py`).
  Passwords now live in a three-tier resolver: `GPUALERT_EMAIL_PASSWORD` env
  var → OS keyring (Windows Credential Locker / macOS Keychain / Linux Secret
  Service) → `~/.gpualert/secret.enc` (Fernet, key derived from a machine-bound
  identifier via PBKDF2-HMAC-SHA256 at 600,000 iterations — OWASP 2023/2025 /
  Django 4.2 default). Copied config files fail to decrypt on another host
  with a helpful "re-run `gpualert config --init`" message. `SecretStr` wraps
  the plaintext so it never surfaces in `repr`/`str`/logs.
- **`gpualert uninstall`** (alias `gpualert purge`) — CLI command that scrubs
  the OS keyring entry, secure-overwrites and deletes `~/.gpualert/secret.enc`
  / `key.bin` / `salt.bin`, and removes `~/.gpualert/`. `--keep-logs`
  preserves `~/.gpualert/logs/`. `--yes` skips the prompt. Because
  `pip uninstall` runs no arbitrary code, this must be run manually before
  `pip uninstall gpualert` — the wizard now prints a reminder at the end of
  `config --init`.
- **Wider artifact pattern set** — added `.tsv, .xlsx, .xls, .parquet, .yaml,
  .yml, .toml, .npy, .pt, .pth, .ckpt, .safetensors, .h5, .onnx, .gguf, .jpeg,
  .svg, .pdf, .tfevents, events.out.tfevents.*`. Model checkpoints stay under
  the existing 25 MB / 45 MB size budget so oversize weights still flow to the
  overflow zip, not the email attachment list.
- **`artifacts.tracked_dirs`** — informational-only field listing experiment
  tracker directories (`runs`, `lightning_logs`, `wandb`, `mlruns`, `outputs`,
  `checkpoints`). Not attached wholesale.
- **`gpualert/metrics.py`** — CSV/TSV/JSON/YAML/XLSX metric extraction. CSV
  and JSON work with stdlib only; XLSX/YAML/Parquet require the new optional
  extra `gpualert[metrics]` (pandas, openpyxl, pyyaml, pyarrow). Metric
  vocabulary covers `loss/acc/f1/auc/map/bleu/rouge/perplexity/psnr/ssim/iou/
  dice/mae/mse/rmse/r2/top1/top5/em` with `val_`/`test_`/`train_`/`eval_`
  prefixes. Metrics land in the email `NOTES` section as `key=0.9312, …`.
- New test suites: `tests/test_uninstall.py`, `tests/test_metrics.py`,
  `tests/test_crypto.py`, `tests/test_secrets.py`. 169 tests total (was 116),
  coverage 87%.

### Changed

- **`SMTPConfig` gains `password_backend`** (`""`, `"keyring"`, `"file"`, or
  `"env"`). Set by the wizard and the silent migration path. When set, the
  loader skips migration; when empty and a plaintext `smtp.password` is
  present, the loader moves the password into the secret store, blanks the
  in-memory field, rewrites `config.toml` without it, and prints one stdout
  line. Fully backward compatible: pre-0.1.4 configs upgrade in place with
  zero user action.
- **Wizard (`gpualert config --init`)** no longer echoes or persists the
  password to `config.toml`. Enters via `getpass`, sends to `store_secret`,
  prints only the backend name.
- **`email_notifier.EmailNotifier.send`** now resolves the password at the
  SMTP boundary via `secrets.load_secret()` — legacy `cfg.smtp.password` is
  still respected (for tests and pre-migration configs).
- **`validate_config`** accepts a set `password_backend` (or `GPUALERT_EMAIL_
  PASSWORD` env var) as evidence the password exists. Prevents `config
  --check` from complaining post-migration.
- **`pyproject.toml`** — `license = "MIT"` (SPDX string, replaces deprecated
  TOML-table form); removed the deprecated `License :: OSI Approved :: MIT
  License` classifier. Cleans up the setuptools warnings you saw on the
  0.1.3 build.
- New hard deps: `cryptography>=42`, `keyring>=24`. New optional extra:
  `gpualert[metrics] = [pandas>=2, openpyxl>=3.1, pyyaml>=6, pyarrow>=15]`.

### Migration notes

- Upgrading from 0.1.3: first command after `pip install -U gpualert` prints
  `[gpualert] Migrated SMTP password to encrypted storage (backend=…)`. No
  action needed. To roll back to 0.1.3 you'd re-run `gpualert config --init`
  after downgrading (the on-disk plaintext password is gone).
- The old plaintext password is best-effort-overwritten before being blanked,
  but this is not a guarantee on journaling / copy-on-write filesystems or
  SSDs. The real protection is that the secret is not stored plaintext going
  forward.
- On HPC compute nodes without a D-Bus Secret Service session, `keyring` will
  fail and the resolver falls back to the Fernet file automatically. This is
  the expected path there, not an error.

## [0.1.3] — 2026-07-07

### Fixed

- PyPI upload metadata: switched to SPDX-string `license` and dropped the
  deprecated `License :: OSI Approved :: MIT License` classifier that PyPI
  Warehouse was warning about on 0.1.2 uploads.

## [0.1.2] — 2026-06-07

### Fixed

- `EmailNotifier.send()` now retries once after a 3-second sleep when the
  first SMTP connection dies with `SMTPServerDisconnected`. Common when two
  `gpualert run` processes complete simultaneously and both open a Gmail SMTP
  session at the same instant. If both attempts fail, the message explicitly
  says the retry was tried and hints at the parallel-job cause.
- Generic `SMTPException` errors now include the exception subclass name so
  users can distinguish auth / recipient / HELO problems.
- New test suite `tests/test_smtp_retry.py` locks the retry contract.

### Added

- `artifacts.attach_artifacts` (default `true`) — master on/off for output-file
  attachment. When `false`, `gpualert run` does not scan the working directory,
  no artifacts attach, and the email body carries an explicit `NOTES` line.
  Logs continue to attach per `email.attach_logs_on_success`.
- `JobResult.notes: list[str]` — free-form annotations rendered in a `NOTES`
  section of the email body.
- Regression-lock test `tests/test_prelaunch_guarantee.py` that monkeypatches
  `subprocess.Popen` to fail and asserts the three log files already exist
  with the header line.

### Changed

- `notifier.base._build_body` renders an optional `NOTES` section when
  `JobResult.notes` is non-empty. Bodies without notes are unchanged.

## [0.1.1] — 2026-05-29

### Fixed

- `gpualert config --init` rejects an email address, an empty value, a
  hostname with no dot, or whitespace at the "SMTP server" prompt.

### Added

- GitHub Actions CI, README badges, issue + PR templates, `CODE_OF_CONDUCT.md`,
  root `CONTRIBUTING.md` stub, runbook section for HPC `gaierror`.

## [0.1.0] — 2026-05-25

Initial release. `gpualert run` / `slurm` / `config` / `test-email` / `logs` /
`version`. Pre-launch log guarantee. 45 MB attachment budget with overflow
zip. Pattern-based error classification. ML metric extraction.

[Unreleased]: https://github.com/Parv-01/gpualert/compare/v0.1.5...HEAD
[0.1.5]: https://github.com/Parv-01/gpualert/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/Parv-01/gpualert/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/Parv-01/gpualert/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Parv-01/gpualert/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Parv-01/gpualert/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Parv-01/gpualert/releases/tag/v0.1.0
