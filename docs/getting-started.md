# Getting Started

## Install

```bash
pip install gpualert
```

Or from source:

```bash
git clone https://github.com/Parv-01/gpualert.git
cd gpualert
pip install -e .
```

Python 3.10+ is required. Tested on Linux and macOS.

## Configure

Run the interactive wizard once:

```bash
gpualert config --init
```

You'll be prompted for SMTP server, port, username (your email), password, and recipient list.
If your username looks like Gmail, the wizard prints the App Password URL — Gmail requires an
App Password rather than your account password and that requires 2FA on the account.

In 0.1.4+, the password never lands on disk in plaintext. The wizard sends it to your OS
keyring (Windows Credential Locker, macOS Keychain, Linux Secret Service). If your host has
no keyring (typical on HPC compute nodes), it falls back to a machine-bound encrypted file
at `~/.gpualert/secret.enc`. On headless CI, `export GPUALERT_EMAIL_PASSWORD=...` before
running `gpualert` skips both — the env var wins.

Verify the config without sending anything:

```bash
gpualert config --check
```

Then send a test message to confirm SMTP actually works:

```bash
gpualert test-email
```

## Run a job

Wrap any command:

```bash
gpualert run -- python train.py --epochs 50
```

The `--` separator tells the CLI that everything after it is the command to run, not flags for
GPUAlert itself. When the job ends — success, failure, timeout, or Ctrl+C — you get an email with
the logs attached.

Some useful flags:

```bash
gpualert run --timeout 7200 -- python train.py        # kill after 2 hours
gpualert run --dry-run -- python train.py             # print the email, don't send
gpualert run --verbose -- python train.py             # also stream output to your terminal
gpualert run --attach 'results/*.csv' -- python train.py
gpualert run --email-to alt@example.com -- python train.py
```

## Monitor a Slurm job

If you already submitted with `sbatch`, hand the job ID to GPUAlert:

```bash
gpualert slurm 12345
```

It polls `sacct` every 10 seconds (override with `--interval`) until the job reaches a terminal state, then emails.

## Uninstall

`pip uninstall gpualert` removes only the package files. It does not touch
your OS keyring entry, your encrypted secret file, or `~/.gpualert/`.

Run this **first**:

```bash
gpualert uninstall            # asks for confirmation
gpualert uninstall --yes      # scripted / no prompt
gpualert uninstall --keep-logs   # preserve ~/.gpualert/logs/
```

Then remove the package:

```bash
pip uninstall gpualert
```

Reversing the order leaves secrets orphaned on disk. `gpualert uninstall`
scrubs them cleanly. `purge` is an alias for the same command.
