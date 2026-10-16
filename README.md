<p align="center">
  <img src="https://raw.githubusercontent.com/Parv-01/gpualert/main/assets/banner.png" alt="GPUAlert Banner"/>
</p>

[![CI](https://github.com/Parv-01/gpualert/actions/workflows/ci.yml/badge.svg)](https://github.com/Parv-01/gpualert/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/gpualert.svg)](https://pypi.org/project/gpualert/)
[![Python](https://img.shields.io/pypi/pyversions/gpualert.svg)](https://pypi.org/project/gpualert/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

# <img src="https://raw.githubusercontent.com/Parv-01/gpualert/main/assets/logo.png" width="75" align="top" alt="Logo"/> GPUAlert

GPUAlert tells you why your GPU training job failed. Wrap any PyTorch, JAX or Slurm job with
`gpualert run -- python train.py` and get an email with the failure cause (CUDA out of memory,
NCCL error, OOM killer, segfault and more), the full stdout/stderr logs and your output files.
No code changes.

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

## Paper

GPUAlert is described in
[GPUAlert: A Zero-Instrumentation Process-Boundary Monitor for Diagnosing GPU Training-Job Failures](https://arxiv.org/abs/2607.01409)
(Agarwal and Ekbal, IIT Patna, 2026). The labelled corpus and evaluation harness are in
[gpualert-eval](https://github.com/Parv-01/gpualert-eval).

If you use GPUAlert in research, please cite:

```bibtex
@misc{agarwal2026gpualert,
  title         = {GPUAlert: A Zero-Instrumentation Process-Boundary Monitor for Diagnosing GPU Training-Job Failures},
  author        = {Agarwal, Parv and Ekbal, Asif},
  year          = {2026},
  eprint        = {2607.01409},
  archivePrefix = {arXiv},
  primaryClass  = {cs.SE},
  url           = {https://arxiv.org/abs/2607.01409}
}
```

## How it compares

| | Code change needed | Classifies the failure | Emails logs and output files |
|---|---|---|---|
| GPUAlert | No | Yes, 15 classes | Yes |
| [knockknock](https://github.com/huggingface/knockknock) | Yes, a decorator | No | No |
| `cmd > log 2>&1` or `nohup` | No | No | No |

## Features

- Wraps any command and emails on completion: success, failure, timeout, or Ctrl+C.
- Polls Slurm jobs via `sacct` so you can monitor jobs you already submitted with `sbatch`.
- Writes log files to disk *before* the process starts, so they exist even on segfault.
- Always attaches logs to failure emails. Non-negotiable.
- Auto-detects ML metrics in successful runs (`accuracy`, `loss`, `F1`, `mAP`, ...) and surfaces
  them in the email body.
- Attaches the output files your job wrote in its own directory during the run (never another
  user's, never hidden directories); budgets the email, zips the overflow, and names anything it
  left out. Two of your own jobs sharing one directory can still pick up each other's outputs, so
  give parallel runs their own directories.
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

Uninstall — always run this **before** `pip uninstall gpualert`, otherwise
your OS keyring entry and `~/.gpualert/secret.enc` will be orphaned:

```bash
gpualert uninstall            # prompts; use --yes for scripted teardown
pip uninstall gpualert
```

Headless / CI nodes without an OS keyring can bypass the on-disk secret file
by setting `GPUALERT_EMAIL_PASSWORD` in the environment — the resolver picks
it up first, before keyring and the encrypted file.

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
