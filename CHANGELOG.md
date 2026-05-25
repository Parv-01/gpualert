# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Parv-01/gpualert/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Parv-01/gpualert/releases/tag/v0.1.0
