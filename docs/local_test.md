# GPUAlert — Local Testing Guide

A working checklist for exercising every part of the package on your own machine,
in PowerShell or WSL Ubuntu. Work through it top to bottom the first time;
afterwards use it as a smoke-test when something feels off.

Tested against Python 3.10 / 3.11 / 3.12 on Windows 10, Windows 11, and Ubuntu 22.04.

---

## 0. Prerequisites

You need one of:

- **PowerShell** (Windows native, recommended on Windows)
- **WSL Ubuntu** (Windows Subsystem for Linux — better for matching the CI environment)
- **macOS / Linux** native terminal — commands are identical to WSL

Plus:

- Python 3.10+ on PATH (`python --version` or `python3 --version`)
- pip
- git (for the source-install path)
- A Gmail account with 2FA enabled (for the optional email test)

Verify before starting:

```powershell
# PowerShell
python --version          # should be >= 3.10
python -m pip --version
git --version
```

```bash
# WSL / Ubuntu / macOS
python3 --version
python3 -m pip --version
git --version
```

If you don't have Python 3.10+, install it (`winget install Python.Python.3.12`
on Windows, `sudo apt install python3.12 python3.12-venv` on Ubuntu).

---

## 1. Get the code

You have two options. Pick one.

### Option A — Clone from GitHub (recommended)

```powershell
cd "E:\Projects\gpualert"
git clone https://github.com/Parv-01/gpualert.git gpualert-test
cd gpualert-test
```

### Option B — Work on the existing workspace folder

```powershell
cd "E:\Projects\gpualert\GPualert (1)\gpualert"
```

WSL equivalent (assuming your Windows E: drive is mounted at `/mnt/e`):

```bash
cd /mnt/e/Projects/gpualert/GPualert\ \(1\)/gpualert
```

---

## 2. Create an isolated environment

**Always use a venv.** Never install gpualert into your system Python during testing.

### PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# Your prompt should now show "(.venv)" prefix
```

If activation is blocked with a script-execution policy error:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
# Then try Activate.ps1 again
```

### WSL / Ubuntu

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Verify the venv is active

```powershell
# Both shells
python -c "import sys; print(sys.prefix)"
# Should print a path ending in ".venv", not your system Python
```

---

## 3. Install the package

From the project root (where `pyproject.toml` lives):

```powershell
pip install -e ".[dev]"
```

Expected output: a wall of "Successfully installed …" with no red errors at the end.

You may see this warning, which is harmless:
```
WARNING: typer 0.25.1 does not provide the extra 'all'
```

### Verify install

```powershell
python -c "import gpualert; print(gpualert.__version__)"
# expect: 0.1.0

gpualert --version
# expect: gpualert 0.1.0

gpualert --help
# expect: a help screen listing 6 commands (run, slurm, config, test-email, logs, version)
```

If `gpualert: command not found`:

- **PowerShell:** close and reopen the shell; your venv's `Scripts/` folder should
  put `gpualert.exe` on PATH automatically when active.
- **WSL/Linux:** confirm `.venv/bin` is on PATH (`echo $PATH | tr ':' '\n' | grep venv`).
  Re-activate the venv if not.

---

## 4. Run the test suite

```powershell
pytest tests/ -v
```

Expected: **73 passed** (or more, as new days land). Coverage should be **>= 80%**.

If anything fails, paste the output into a GitHub issue — don't proceed to manual
tests until pytest is green.

### Quick variants

```powershell
pytest tests/ -q                  # short output
pytest tests/test_day2.py -v      # one file
pytest -k "cuda"                  # match test names
pytest --tb=long                  # full tracebacks on failure
```

### Lint and format (optional but recommended)

```powershell
ruff check gpualert/ tests/
black --check gpualert/ tests/
```

Both should report no errors.

---

## 5. Manual functional tests

This section exercises every command in the user-facing CLI. Mark each with
[ ] or [x] as you go.

### 5.1 — `gpualert version`

```powershell
gpualert version
```

- [ ] Prints `gpualert 0.1.0`
- [ ] Exits with code 0 (`$LASTEXITCODE` in PowerShell, `echo $?` in bash)

### 5.2 — `gpualert --help` and subcommand help

```powershell
gpualert --help
gpualert run --help
gpualert slurm --help
gpualert config --help
gpualert test-email --help
gpualert logs --help
```

- [ ] Each prints a Typer help screen
- [ ] `gpualert --help` lists exactly: run, slurm, config, test-email, logs, version
- [ ] `gpualert config --help` shows: `--init`, `--show`, `--check`, `--test-email`, `--reset`

### 5.3 — `gpualert run` happy path (dry-run)

```powershell
gpualert run --dry-run -- python -c "print('hello from local test')"
```

- [ ] Shows a "Starting Job" panel
- [ ] Status table reads `SUCCESS`, exit code 0
- [ ] Prints "Log files written:" with three paths under `~/.gpualert/logs/`
- [ ] Prints a "DRY RUN — email that would be sent" block
- [ ] Email "To:" matches whatever's currently in your config (empty list if first run)
- [ ] Exits with code 0

### 5.4 — `gpualert run` failure path (dry-run)

```powershell
gpualert run --dry-run -- python -c "raise RuntimeError('intentional failure')"
```

- [ ] Status table reads `FAILED`, exit code 1
- [ ] Error summary mentions "Python exception (traceback)" or similar
- [ ] DRY RUN block shows stderr_tail with "RuntimeError"
- [ ] Log files written and listed
- [ ] Exits with code 1

### 5.5 — `gpualert run` with timeout

```powershell
gpualert run --dry-run --timeout 2 -- python -c "import time; time.sleep(30)"
```

- [ ] Job killed after ~2 seconds
- [ ] Status reads `TIMEOUT`
- [ ] Log files still exist on disk
- [ ] Exits with code 1

### 5.6 — `gpualert run --no-notify` (skip notification entirely)

```powershell
gpualert run --no-notify -- python -c "print('ok')"
```

- [ ] Job runs and reports success
- [ ] Prints "Notification skipped (--no-notify)"
- [ ] Does NOT print any DRY RUN or email-related output
- [ ] Exits 0

### 5.7 — `gpualert run --verbose` (stream output)

```powershell
gpualert run --verbose --dry-run -- python -c "for i in range(5): print('step', i)"
```

- [ ] Each `step N` line appears in real time during the run, prefixed with `OUT |`
- [ ] After the spinner finishes, the normal table + log paths still print
- [ ] Exits 0

### 5.8 — `gpualert run` with extra attach patterns

Create a fake output, then verify the artifact scanner picks it up:

```powershell
# PowerShell
"a,b`n1,2" | Out-File -FilePath metrics.csv -Encoding utf8
gpualert run --dry-run --attach "*.csv" -- python -c "print('done')"

# Cleanup
Remove-Item metrics.csv
```

```bash
# WSL/Ubuntu
echo "a,b
1,2" > metrics.csv
gpualert run --dry-run --attach "*.csv" -- python -c "print('done')"
rm metrics.csv
```

- [ ] DRY RUN block lists `metrics.csv` in the Attach: line
- [ ] Artifacts found: 1 (or more) appears above the email panel

### 5.9 — `gpualert config --show` (initial state)

```powershell
gpualert config --show
```

- [ ] Prints a JSON-ish panel labeled "Current Config (password masked)"
- [ ] Shows `"password": ""` (empty, never your real password)
- [ ] Lists default artifact patterns (`*.csv`, `*.png`, etc.)
- [ ] Exits 0

### 5.10 — `gpualert config --check` on empty config

Before running `--init`, validation should reject:

```powershell
gpualert config --check
```

- [ ] Prints "Config has problems:"
- [ ] Lists at least these errors: smtp.username is empty, smtp.password is empty,
      email.from_address is empty, email.to_addresses is empty
- [ ] Exits with code 1

### 5.11 — `gpualert config --init` (the wizard)

> **Skip this if you don't want to type real credentials.** You can do steps 5.12
> and 5.13 with a hand-edited config file instead (see 5.14).

```powershell
gpualert config --init
```

Walk through the prompts. Use defaults for server/port (smtp.gmail.com / 587).

- [ ] Header reads "=== GPUAlert Configuration Wizard ==="
- [ ] When you type a `@gmail.com` username, you see the Gmail-detected hint
      with the URL `https://myaccount.google.com/apppasswords`
- [ ] Password prompt does NOT echo what you type
- [ ] After completing all prompts, prints "Configuration saved."
- [ ] Config file appears at `~/.gpualert/config.toml`

PowerShell check:
```powershell
Test-Path "$HOME\.gpualert\config.toml"
# expect: True
```

WSL/Linux check:
```bash
ls -la ~/.gpualert/config.toml
# expect: -rw------- (mode 600)
```

### 5.12 — `gpualert config --check` after init

```powershell
gpualert config --check
```

- [ ] Prints "Config is valid (offline check)."
- [ ] Mentions running `gpualert test-email` as the next step
- [ ] Exits 0

### 5.13 — `gpualert test-email`

> **For Gmail, you MUST have an App Password.** Regular Gmail passwords are
> rejected by Google's SMTP. Generate one at
> https://myaccount.google.com/apppasswords (requires 2FA enabled).

```powershell
gpualert test-email
```

- [ ] Prints "Test email sent."
- [ ] Check your inbox: a `[GPUAlert] ✅ COMPLETED: gpualert test-email` email arrives
- [ ] Email body has Status: SUCCESS, the test job ID, timestamps

If it fails with `SMTPAuthenticationError`:
- [ ] Confirm you used an **App Password**, not your regular Gmail password
- [ ] Confirm 2FA is enabled on your Google account
- [ ] Run `gpualert config --show` and verify the username matches the account

### 5.14 — Hand-edit config (alternative to wizard)

If you don't want to use Gmail, edit `~/.gpualert/config.toml` directly:

```toml
[smtp]
server = "smtp.your-isp.com"
port = 587
use_tls = true
username = "you@example.com"
password = "your-smtp-password"

[email]
from_address = "you@example.com"
to_addresses = ["you@example.com"]
subject_prefix = "[GPUAlert]"
notify_on_success = true
notify_on_failure = true
```

Then re-run `gpualert config --check` and `gpualert test-email`.

### 5.15 — End-to-end real email on a real command

```powershell
gpualert run -- python -c "print('Local E2E test from gpualert')"
```

- [ ] Job runs, status SUCCESS
- [ ] Log paths printed
- [ ] Prints "Email: Email sent to [...]."
- [ ] Inbox: email with subject `[GPUAlert] ✅ COMPLETED: python -c "print(...)"`
- [ ] Attachments: stdout.log, stderr.log, combined.log

### 5.16 — End-to-end real email on a failing command

```powershell
gpualert run -- python -c "import sys; sys.stderr.write('CUDA out of memory\n'); sys.exit(1)"
```

- [ ] Status FAILED, exit code 1
- [ ] Error summary mentions "GPU out-of-memory (CUDA OOM)"
- [ ] Email subject: `[GPUAlert] ❌ FAILED: …`
- [ ] Email body includes the LAST 15 LINES OF STDERR section with the CUDA line
- [ ] All three log files still attached

### 5.17 — `gpualert logs`

```powershell
gpualert logs
gpualert logs --last 3
```

- [ ] Shows a table with Directory / Created / Size columns
- [ ] Lists the runs you've done in steps 5.3 – 5.16
- [ ] `--last 3` limits to three rows

### 5.18 — Inspect a log directory directly

```powershell
# PowerShell
Get-ChildItem "$HOME\.gpualert\logs" | Select-Object -Last 5
Get-Content "$HOME\.gpualert\logs\<one-of-the-dirs>\combined.log"
```

```bash
# WSL / Linux
ls -la ~/.gpualert/logs/ | tail -5
cat ~/.gpualert/logs/<one-of-the-dirs>/combined.log
```

- [ ] Each log dir contains: stdout.log, stderr.log, combined.log
- [ ] combined.log has the GPUAlert header, [OUT]/[ERR] tagged lines, and a "Job Complete" footer
- [ ] stdout.log contains only the stdout lines, no stderr

### 5.19 — `gpualert slurm` on a non-Slurm machine

This is expected to fail cleanly on your laptop (you don't have sacct):

```powershell
gpualert slurm 12345
```

- [ ] Prints "Error: Slurm (sacct) not found in PATH."
- [ ] Suggests using `gpualert run` for local jobs
- [ ] Exits with code 1

### 5.20 — `gpualert config --reset` (cleanup)

```powershell
gpualert config --reset
# Type 'y' when prompted
```

- [ ] Prompts "Delete config file? [y/N]:"
- [ ] After 'y', prints "Config deleted."
- [ ] `~/.gpualert/config.toml` no longer exists

---

## 6. Behavioural contracts to verify

These are the guarantees the package promises. Each one is testable from the
command line.

### 6.1 — Log files exist even when the process is killed

```powershell
# Start a long-running job, then Ctrl+C it after a couple seconds
gpualert run --dry-run -- python -c "import time; time.sleep(60); print('done')"
# Press Ctrl+C
```

- [ ] Process exits with status `INTERRUPTED`
- [ ] All three log files for this job still exist under `~/.gpualert/logs/`

### 6.2 — Log files exist even when the command doesn't exist

```powershell
gpualert run --dry-run -- this_command_does_not_exist
```

- [ ] Status FAILED (exit code 127 or similar)
- [ ] Log files still created and listed

### 6.3 — Notification failure doesn't lose logs

Force a notification failure by pointing at a bad SMTP server:

```powershell
# Backup current config
Copy-Item "$HOME\.gpualert\config.toml" "$HOME\.gpualert\config.backup.toml"

# Edit config to use a bad SMTP host (or run config --init with bogus values)
# Then:
gpualert run -- python -c "print('ok')"

# Restore
Move-Item "$HOME\.gpualert\config.backup.toml" "$HOME\.gpualert\config.toml" -Force
```

- [ ] Prints "Notification failed: …"
- [ ] Still prints "Logs are still saved locally (see paths above)"
- [ ] Log paths above are real files on disk

### 6.4 — Password never appears in `--show` output

```powershell
gpualert config --show
```

- [ ] Wherever a password appears in the JSON, it reads `"***"` not the real value

### 6.5 — Config file permissions

WSL / Linux only:

```bash
stat -c '%a' ~/.gpualert/config.toml
# expect: 600
```

On Windows, NTFS permissions handle this differently — gpualert just calls
`os.chmod(path, 0o600)` which is a no-op on Windows. The file is owned by your
user account, which is the practical equivalent.

---

## 7. Cleanup

When you're done testing:

```powershell
# Deactivate venv
deactivate

# Remove the venv
Remove-Item -Path .venv -Recurse -Force

# (Optional) wipe gpualert logs and config
Remove-Item -Path "$HOME\.gpualert" -Recurse -Force
```

WSL / Linux:

```bash
deactivate
rm -rf .venv ~/.gpualert
```

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `gpualert: command not found` | venv not active, or install failed | Re-activate venv; `pip install -e ".[dev]"` |
| `SMTPAuthenticationError: Username and Password not accepted` | Used Gmail password instead of App Password | Generate App Password at https://myaccount.google.com/apppasswords |
| `Connection refused` on test-email | Bad SMTP server or port in config | `gpualert config --show`, verify, re-run `--init` |
| Email arrives in Spam | Common for first-time mail from unknown senders | Mark as "Not spam" once; subsequent emails land in inbox |
| Long file paths look broken in CLI output | Rich's terminal-width wrapping | Cosmetic only — the actual files exist on disk |
| `pytest` hangs on a particular test | Probably a real-network test that wasn't mocked | Run with `-v --tb=long` and report the test name |
| `pip install -e .` fails on Python 3.10 with TOML errors | `tomllib` is 3.11+ | We include `tomli` as a 3.10 fallback in pyproject.toml; check it installed |
| Windows: `Activate.ps1 cannot be loaded` | PowerShell execution policy | `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |

---

## 9. What to report when something breaks

Open an issue at https://github.com/Parv-01/gpualert/issues with:

1. OS (Windows / WSL / Linux / macOS) and Python version (`python --version`)
2. The exact command you ran
3. The full output (use `--verbose` if it's a `gpualert run`)
4. Relevant log files from `~/.gpualert/logs/<the-failing-run>/`
5. `gpualert config --show` output (password is masked, safe to paste)
