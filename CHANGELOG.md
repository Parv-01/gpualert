# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] — 2026-06-07

### Added

- `artifacts.attach_artifacts` (default `true`) — master on/off for output-file
  attachment. When `false`, `gpualert run` does not scan the working directory
  for matching files, no artifacts are attached, and the email body carries an
  explicit `NOTES` line stating the toggle is off. Logs continue to attach per
  `email.attach_logs_on_success` / `attach_logs_on_failure`. The flag is
  additive — existing configs without it keep current behavior.
- `JobResult.notes: list[str]` — free-form annotations the CLI can attach
  before notification. Rendered as a `NOTES` section in the email body. Used
  by the new toggle today; available for future features.
- Regression-lock test suite `tests/test_prelaunch_guarantee.py` that
  monkeypatches `subprocess.Popen` to fail and asserts the three log files
  already exist on disk with the header line. Any future change that moves
  log creation after `Popen` will break this test.

### Changed

- `notifier.base._build_body` now renders an optional `NOTES` section when
  `JobResult.notes` is non-empty. Bodies without notes are unchanged.

## [0.1.1] — 2026-05-29

### Fixed

- `gpualert config --init` now rejects an email address, an empty value, a hostname
  with no dot, or whitespace at the "SMTP server" prompt and re-asks with a clear
  message. Previously the wizard accepted any string, so typing your email at the
  server prompt by mistake silently broke every later notification with
  `gaierror: Name or service not known`.

### Added

- GitHub Actions CI workflow (`.github/workflows/ci.yml`): matrix on Python 3.10 /
  3.11 / 3.12, runs ruff, black --check, pytest with coverage, and `local_test.py`
  on every push and PR. Separate `build` job verifies the sdist + wheel build and
  passes `twine check`.
- Issue + PR templates under `.github/`.
- README badges: CI status, PyPI version, supported Python versions, license.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1) and root `CONTRIBUTING.md` stub
  so GitHub's Community Standards page picks them up.
- Runbook section covering the HPC compute-node `gaierror` failure mode, with the
  two workarounds: submit with `sbatch` and monitor with `gpualert slurm` from the
  login node, or point `smtp.server` at an internal relay.

## [0.1.0] — 2026-05-25

Initial release.

### Added

- `gpualert run` — wraps any command, streams stdout/stderr to per-job log directories, sends
  an email on completion with logs attached. Supports `--timeout`, `--dry-run`, `--verbose`,
  `--attach`, `--email-to`, `--no-notify`.
- `gpualert slurm <job_id>` — polls `sacct` until the job reaches a terminal state, then emails.
- `gpualert config` — interactive setup wizard, offline validation, show/reset.
- `gpualert test-email` — sanity-check SMTP without running a job.
- `gpualert logs` — list recent job log directories.
- `gpualert version` — print version.
- Pattern-based error classification for CUDA OOM, NCCL, NaN loss, OOMKiller, missing modules,
  segfaults, generic tracebacks.
- ML metric extraction: accuracy, loss, F1, mAP, val loss, best accuracy.
- Artifact scanning with per-file and total-size budgets; overflow zipped to
  `artifacts_overflow.zip`.
- Log file guarantee: log files are created before the subprocess starts and always exist
  on disk, even on crash or kill.
- Notifier contract: `send()` never raises; CLI exit code follows the job, not the notifier.

[Unreleased]: https://github.com/Parv-01/gpualert/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/Parv-01/gpualert/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Parv-01/gpualert/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Parv-01/gpualert/releases/tag/v0.1.0
