<p align="center">
  <img src="https://raw.githubusercontent.com/Parv-01/gpualert/main/assets/banner.png" alt="GPUAlert Banner"/>
</p>

[![CI](https://github.com/Parv-01/gpualert/actions/workflows/ci.yml/badge.svg)](https://github.com/Parv-01/gpualert/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/gpualert.svg)](https://pypi.org/project/gpualert/)
[![Python](https://img.shields.io/pypi/pyversions/gpualert.svg)](https://pypi.org/project/gpualert/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

# <img src="https://raw.githubusercontent.com/Parv-01/gpualert/main/assets/logo.png" width="75" align="top" alt="Logo"/> GPUAlert

A CLI for long-running GPU and Slurm jobs that emails you when they finish - with the full
stdout/stderr logs and any output artifacts attached.

```bash
pip install gpualert
gpualert config --init
gpualert run -- python train.py
```

## Why

You've kicked off training, it'll take twelve hours, and you want to know whether it crashed at
hour two or finished cleanly at hour eleven. SSH'ing back in to find out is a tax. GPUAlert
wraps the job, writes structured logs to disk, classifies common failure modes (CUDA OOM, NCCL,
NaN loss, OOMKiller, etc.), and emails you the result with logs attached.

<p align="center">
  <img src="https://raw.githubusercontent.com/Parv-01/gpualert/main/assets/demo.gif" alt="GPUAlert Demo"/>
</p>

## Features

- Wraps any command and emails on completion: success, failure, timeout, or Ctrl+C.
- Polls Slurm jobs via `sacct` so you can monitor jobs you already submitted with `sbatch`.
- Writes log files to disk *before* the process starts, so they exist even on segfault.
- Always attaches logs to failure emails. Non-negotiable.
- Auto-detects ML metrics in successful runs (`accuracy`, `loss`, `F1`, `mAP`, ...) and surfaces
  them in the email body.
- Scans the working directory for output artifacts after the job ends; budgets the email and
  zips the overflow.
- `--dry-run` prints the email it would send without touching SMTP - useful for debugging.

## Quick start

Install and configure:

```bash
pip install gpualert
gpualert config --init     # interactive SMTP wizard
gpualert test-email        # verify it actually works
```

For Gmail, generate an App Password at <https://myaccount.google.com/apppasswords> (requires
2FA on the account). Paste it at the password prompt.

Wrap a local job:

```bash
gpualert run -- python train.py --epochs 50
gpualert run --timeout 7200 -- bash train.sh
gpualert run --dry-run -- python smoke.py
```

Monitor a Slurm job you've already submitted:

```bash
gpualert slurm 12345
gpualert slurm 12345 --interval 30 --timeout 86400
```

List recent log directories:

```bash
gpualert logs --last 20
```

## Configuration

Stored at `~/.gpualert/config.toml` (mode 600), created on first run.

```toml
[smtp]
server = "smtp.gmail.com"
port = 587
use_tls = true
username = "you@gmail.com"
password = "your-app-password"

[email]
to_addresses = ["you@gmail.com"]
attach_logs_on_success = true

[artifacts]
patterns = ["*.csv", "*.png", "*.json", "*.log", "*.npz"]
max_single_file_mb = 25
max_total_mb = 45
```

Full reference: [docs/configuration.md](docs/configuration.md).

## Documentation

- [Getting Started](docs/getting-started.md)
- [CLI Reference](docs/cli-reference.md)
- [Python API](docs/api-reference.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [Runbook](docs/runbook.md)
- [Contributing](docs/contributing.md)
- [Releasing](docs/releasing.md)
- [Local testing guide](docs/local_test.md)

## Community

GPUAlert is built in the open. If you find it useful, run into a bug, or have an idea, here's
how to get involved:

- **Star the repo** if you'd like more updates - it helps other ML researchers find the project.
- **Questions, ideas, war stories?** Open a thread in
  [GitHub Discussions](https://github.com/Parv-01/gpualert/discussions). Anything from
  "does this work with X scheduler" to "I wrote a notifier for Y" - happy to hear it.
- **Bug reports** go to
  [Issues](https://github.com/Parv-01/gpualert/issues/new/choose). The template asks for
  `gpualert version`, your OS, and the relevant `combined.log` lines so triage is fast.
- **Feature requests** also live in Issues. Tell me what's painful in your current workflow
  and what would make it less painful.
- **Pull requests welcome.** [Contributing](docs/contributing.md) has the dev setup. Small
  fixes (typos, docs, error messages) - open a PR directly. Larger changes - open an issue
  first so we can agree on the shape before code happens.

Looking for collaborators on: a Slack / Discord / Telegram notifier backend, multi-job
dashboards, and a Prometheus exporter for cluster-wide stats. If any of those scratch your
itch, say so in a Discussion thread.

## Requirements

- Python 3.10+
- Linux or macOS
- An SMTP account you can authenticate to

## License

MIT. See [LICENSE](LICENSE).
