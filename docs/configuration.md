# Configuration

GPUAlert reads `~/.gpualert/config.toml`. The file is created with safe defaults on first run and
chmod'd to 600. If the file is corrupt at load time, GPUAlert falls back to in-memory defaults
rather than crashing.

## Example

```toml
[smtp]
server = "smtp.gmail.com"
port = 587
use_tls = true
username = "you@gmail.com"
password = "your-16-char-app-password"

[email]
from_address = "you@gmail.com"
to_addresses = ["you@gmail.com", "advisor@university.edu"]
subject_prefix = "[GPUAlert]"
notify_on_success = true
notify_on_failure = true
attach_logs_on_success = true
attach_logs_on_failure = true   # forced true at runtime — cannot disable

[artifacts]
attach_artifacts = true
patterns = ["*.csv", "*.png", "*.jpg", "*.txt", "*.json", "*.log", "*.npz"]
max_single_file_mb = 25
max_total_mb = 45
scan_depth = 3

verbose = false
dry_run = false
log_dir = "~/.gpualert/logs"
```

## Fields

### `[smtp]`

| Field              | Type   | Default            | Notes |
|--------------------|--------|--------------------|-------|
| `server`           | string | `smtp.gmail.com`   | SMTP server hostname. |
| `port`             | int    | `587`              | 587 is STARTTLS, 465 is SMTPS (set `use_ssl = true`). |
| `use_tls`          | bool   | `true`             | STARTTLS the connection after EHLO. Ignored when `use_ssl` is set. |
| `use_ssl`          | bool   | `false`            | **0.1.5+**: implicit SSL (SMTPS) for port 465. TLS from the first byte — no STARTTLS. Try this when cluster firewalls break port 587. The wizard sets it automatically for port 465. |
| `username`         | string | `""`               | Usually your email address. |
| `password`         | string | `""`               | **0.1.4+: no longer stored on disk.** In-memory-only fallback field. The wizard sends the password to the secret store instead. |
| `password_backend` | string | `""`               | **0.1.4+**: where the live password is stored — `"keyring"` (OS keyring), `"file"` (`~/.gpualert/secret.enc`, Fernet, machine-bound), or `"env"` (`GPUALERT_EMAIL_PASSWORD`). Auto-set by wizard/migration. |

**Password storage** (0.1.4+). On resolve, GPUAlert tries `GPUALERT_EMAIL_PASSWORD`
first, then the OS keyring, then `~/.gpualert/secret.enc`. On HPC compute nodes
where the keyring backend is absent, the Fernet file path is used automatically.
To use the env-var path on a headless node, `export GPUALERT_EMAIL_PASSWORD=...`
before `gpualert run`. Upgrading from 0.1.3 or earlier auto-migrates the
plaintext password out of `config.toml` on first load and prints one stdout line.

### `[email]`

| Field                    | Type     | Default        | Notes |
|--------------------------|----------|----------------|-------|
| `from_address`           | string   | `""`           | The wizard copies this from `smtp.username`. |
| `to_addresses`           | string[] | `[]`           | Recipient list. Override per-run with `--email-to`. |
| `subject_prefix`         | string   | `[GPUAlert]`   | Prepended to every subject. |
| `notify_on_success`      | bool     | `true`         | |
| `notify_on_failure`      | bool     | `true`         | |
| `attach_logs_on_success` | bool     | `true`         | If false, logs are not attached when the job succeeds. |
| `attach_logs_on_failure` | bool     | `true`         | Ignored. Failure logs are always attached. |

### `[artifacts]`

| Field                | Type     | Default                 | Notes |
|----------------------|----------|-------------------------|-------|
| `attach_artifacts`   | bool     | `true`                  | Master on/off. When `false`, no output files are scanned or attached; logs still attach per `email.attach_logs_on_success`. Email body carries an explicit `NOTES` line when off. (Added in 0.1.2.) |
| `patterns`           | string[] | see example above       | Glob patterns to scan for after the job ends. |
| `max_single_file_mb` | int      | `25`                    | Skip any single file larger than this. |
| `max_total_mb`       | int      | `45`                    | Total attachment budget. Overflow is zipped. |
| `scan_depth`         | int      | `3`                     | Directory depth for the artifact scan. |

### Top-level

| Field     | Type   | Default              | Notes |
|-----------|--------|----------------------|-------|
| `verbose` | bool   | `false`              | Currently set per-run via `--verbose`. |
| `dry_run` | bool   | `false`              | If `true`, all runs are dry-run by default. |
| `log_dir` | string | `~/.gpualert/logs`   | Where per-job log directories live. |

## Inspecting and resetting

```bash
gpualert config --show     # print current config; password is masked
gpualert config --check    # offline validation; no network
gpualert config --reset    # delete the file (you get a confirm prompt)
```

`--show` prints the config as JSON with `smtp.password` replaced by `***`. The actual file on disk
is unchanged.

## Permissions

The config file is stored at `~/.gpualert/config.toml` with mode 600 and its parent directory with
mode 700. If you mount this directory into a container, make sure your container user can read it.
