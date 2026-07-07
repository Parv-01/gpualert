# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Parv-01/gpualert/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/Parv-01/gpualert/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/Parv-01/gpualert/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Parv-01/gpualert/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Parv-01/gpualert/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Parv-01/gpualert/releases/tag/v0.1.0
