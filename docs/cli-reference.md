# CLI Reference

Run `gpualert --help` for the top-level menu, or `gpualert <command> --help` for any subcommand.
Exit codes follow the wrapped job: `gpualert run -- ...` exits 0 on success, non-zero on failure.
Notifier failures do not change the exit code.

## `gpualert run`

Run a command and send a notification when it ends.

```bash
gpualert run [OPTIONS] -- CMD...
```

| Flag                | Default | Description |
|---------------------|---------|-------------|
| `--attach`, `-a`    | `[]`    | Extra glob patterns to attach. Repeatable. |
| `--email-to`, `-e`  | —       | Override the recipient for this run only. |
| `--timeout`, `-t`   | none    | Kill the job after N seconds; status becomes `timeout`. |
| `--dry-run`         | false   | Print the email that would be sent; no SMTP call. |
| `--verbose`, `-v`   | false   | Also stream job output to your terminal. |
| `--no-notify`       | false   | Run the job, write logs, do not send anything. |

Examples:

```bash
gpualert run -- python train.py
gpualert run --timeout 7200 --attach 'checkpoints/*.pt' -- bash train.sh
gpualert run --dry-run -- python -c "print('hi')"
gpualert run --no-notify -- python smoke_test.py
```

Use `--` to separate GPUAlert flags from your command's flags. Without it, anything starting with
`--` will be parsed by GPUAlert.

## `gpualert slurm`

Poll an already-submitted Slurm job until it reaches a terminal state, then notify.

```bash
gpualert slurm JOB_ID [OPTIONS]
```

| Flag              | Default | Description |
|-------------------|---------|-------------|
| `--interval`, `-i`| `10`    | Seconds between `sacct` calls. |
| `--timeout`       | none    | Stop polling after this many wall-clock seconds. |
| `--email-to`      | —       | Override the recipient for this run. |
| `--dry-run`       | false   | Print the email, don't send. |

Exits with `1` immediately if `sacct` is not on `PATH`. Terminal Slurm states that count as failure
include `FAILED`, `CANCELLED`, `TIMEOUT`, `NODE_FAIL`, `PREEMPTED`, and `OUT_OF_MEMORY`; only
`COMPLETED` is treated as success.

```bash
gpualert slurm 12345
gpualert slurm 12345 --interval 30 --timeout 86400
```

## `gpualert config`

Manage `~/.gpualert/config.toml`.

```bash
gpualert config [--init | --show | --check | --test-email | --reset]
```

| Flag           | Description |
|----------------|-------------|
| `--init`       | Interactive setup wizard. |
| `--show`       | Print current config (password masked). |
| `--check`      | Offline validation: checks fields, no network call. |
| `--test-email` | Send a test email. Same as `gpualert test-email`. |
| `--reset`      | Delete the config file after a confirmation prompt. |

Run with no flags to see the menu.

## `gpualert test-email`

Send a one-line test message to verify SMTP works end to end. Exits non-zero if the config is
invalid or the SMTP call fails.

```bash
gpualert test-email
```

This is the simplest way to confirm the wizard's output is correct before running a real job.

## `gpualert logs`

List recent job log directories.

```bash
gpualert logs [--last N]
```

| Flag         | Default | Description |
|--------------|---------|-------------|
| `--last`, `-n` | `10`  | How many directories to show, newest first. |

Output columns: directory path, creation time, total size. The directory contains
`stdout.log`, `stderr.log`, and `combined.log` for that run.

## `gpualert version`

Print the installed version.

```bash
gpualert version
```
