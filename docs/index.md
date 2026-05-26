# GPUAlert Documentation

GPUAlert wraps long-running GPU or Slurm jobs and emails you when they finish — with the full
stdout/stderr logs and any output artifacts attached. The point is so you never have to SSH back
in just to find out whether last night's training finished or crashed at hour two.

```bash
gpualert run -- python train.py
gpualert slurm 12345
```

## Pages

- [Getting Started](getting-started.md) — install, configure, run your first job.
- [Configuration](configuration.md) — `~/.gpualert/config.toml` reference.
- [CLI Reference](cli-reference.md) — every command and flag.
- [Python API](api-reference.md) — for use inside scripts.
- [Architecture](architecture.md) — how the log guarantee, polling, and attachment budget work.
- [Runbook](runbook.md) — common failures and how to recover.
- [Contributing](contributing.md) — dev setup and test loop.
- [Releasing](releasing.md) — PyPI publish recipe (maintainers).

## Design promises

1. **Logs always exist on disk.** The launcher opens the log files *before* starting your job, so
   even on segfault or `kill -9` you have something to read.
2. **Logs always get attached to failure emails.** This is non-negotiable and not behind a flag.
3. **The notifier never raises.** A failed email never masks the underlying job result. The CLI
   exits with the *job's* status, not the notifier's.
4. **No silent data loss.** If artifacts exceed the email budget they get zipped; if the zip is
   still too big you're told which files were skipped.
