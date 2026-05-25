# GPUAlert

A CLI for long-running GPU and Slurm jobs that emails you when they finish — with the full
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

## Features

- Wraps any command and emails on completion: success, failure, timeout, or Ctrl+C.
- Polls Slurm jobs via `sacct` so you can monitor jobs you already submitted with `sbatch`.
- Writes log files to disk *before* the process starts, so they exist even on segfault.
- Always attaches logs to failure emails. Non-negotiable.
- Auto-detects ML metrics in successful runs (`accuracy`, `loss`, `F1`, `mAP`, ...) and surfaces
  them in the email body.
- Scans the working directory for output artifacts after the job ends; budgets the email and
  zips the overflow.
- `--dry-run` prints the email it would send without touching SMTP — useful for debugging.

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

## Requirements

- Python 3.10+
- Linux or macOS
- An SMTP account you can authenticate to

## License

MIT. See [LICENSE](LICENSE).
