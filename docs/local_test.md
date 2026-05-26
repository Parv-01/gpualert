# Local Testing Guide

Before publishing to PyPI, run the full local test suite on a real machine to confirm every
component works against your Python and your OS. The repo ships `local_test.py` for this.

The script exercises every module (config, log_manager, launcher, parse_errors, artifacts, slurm,
notifier, CLI, end-to-end), prints PASS/FAIL per check, and writes a full log to
`local_test_runs/<timestamp>.log` for post-mortem. Exit code is 0 iff every check passed.

## TL;DR

```bash
git clone https://github.com/Parv-01/gpualert.git
cd gpualert
python -m venv .venv
source .venv/bin/activate              # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python local_test.py
```

Expected last line:

```
  ALL 131 CHECKS PASSED  (~3s)
```

If you see `N FAILED, M PASSED`, scroll up to the `Failed checks:` block — each entry names the
section and gives a one-line reason. The full traceback for every failure is in the run log under
`local_test_runs/`.

## What the script does

The 131 checks are grouped into 9 sections:

| # | Section       | What it verifies                                                                 |
|---|---------------|----------------------------------------------------------------------------------|
| 1 | config        | Defaults, validation rules, password masking, save/load round-trip, corrupt-file fallback, mode 600 on POSIX. |
| 2 | log_manager   | Log directory creation, files-exist-before-write guarantee, thread-safe concurrent writes, tail extraction, recent-logs listing. |
| 3 | launcher      | `run_job` for success / non-zero exit / command-not-found / Python traceback / timeout / CUDA OOM signature. Confirms log files exist on disk in every case. |
| 4 | parse_errors  | CUDA OOM, NCCL, segfault, file not found, missing module, NaN loss, OOMKiller, generic traceback, exit-code fallback. Plus accuracy / loss / F1 metric extraction. |
| 5 | artifacts     | Pattern matching, mtime filter, depth filter, excluded directories (`.git`, `__pycache__`, ...), per-file size limit, total-size budget, overflow zipping, summarise output. |
| 6 | slurm         | `is_slurm_available` returns a bool, `poll_job` raises `SlurmNotAvailableError` on non-Slurm hosts, elapsed-time / exit-code parsers handle the formats `sacct` actually emits. |
| 7 | notifier      | `DryRunNotifier` returns success and prints the email body. `EmailNotifier` mocks SMTP for the success path, auth-error path, and network-error path. Confirms the never-raises contract. Subject/body builders produce expected output. |
| 8 | CLI           | Invokes `python -m gpualert.cli` as a subprocess for `version`, `--help`, `config --check/--show`, `logs`, `run --no-notify`, `run --dry-run`, `slurm <id>`. |
| 9 | end_to_end    | Spawns a fake training script that produces a metrics CSV and a results JSON, then runs the full pipeline: `run_job` → `find_artifacts` → `prepare_attachments` → `DryRunNotifier.send`. Also a failing scenario that proves logs+stderr+classification all land. |

The script isolates its own HOME so it does **not** touch your real `~/.gpualert/config.toml`
or `~/.gpualert/logs/`. A temp directory is created at startup and deleted at exit (use
`--keep-tmp` to inspect it).

## Useful flags

```bash
python local_test.py --list                # show section names
python local_test.py --only config         # run one section
python local_test.py --keep-tmp            # leave the test HOME on disk
python local_test.py --no-color            # plain output (CI / log files)
```

## When a check fails

1. **Read the failed check name and detail line.** The script names the assertion and prints the
   observed value alongside the expected one.
2. **Open the run log.** `local_test_runs/<timestamp>.log` has every check's output plus the
   tracebacks for any exceptions. Search for `FAIL` to jump to the failures.
3. **Re-run the single section.** `python local_test.py --only <section>` — faster iteration than
   rerunning the whole suite while you fix the issue.
4. **Inspect the test HOME.** `python local_test.py --only <section> --keep-tmp` then `cd` into the
   directory it prints — you'll see `~/.gpualert/config.toml` and `~/.gpualert/logs/...` as if a
   user had just used GPUAlert.

## The one manual step

The automated suite mocks SMTP — it does not connect to a real mail server. To verify that your
*specific* SMTP setup (Gmail App Password, an internal relay, ...) actually works, run the wizard
and send a real test email yourself:

```bash
gpualert config --init       # interactive: SMTP server, username, password, recipient
gpualert test-email          # one-line message to confirm delivery
```

Check your inbox. If the test email arrives, your setup is good. If not:

- `gpualert config --check` — does the config validate offline?
- `gpualert config --show` — are the username and recipient(s) correct? Password is masked.
- Spam / promotions folder — Gmail buckets self-sent messages aggressively.
- See [runbook.md](runbook.md) for the auth-failure and connection-refused recovery paths.

This manual step is the only thing the automated suite cannot do for you — you need a real SMTP
credential to prove the email path works end to end.

## CI usage

`local_test.py` is suitable for CI. It exits 0 on green and 1 on any failure. Add to a workflow:

```yaml
- run: pip install -e ".[dev]"
- run: python local_test.py --no-color
```

The run log lands in `local_test_runs/`. Upload it as an artifact if you want failure context
preserved across CI runs.

## Pytest

The pytest suite under `tests/` is finer-grained — unit tests for individual functions and a few
integration scenarios. `local_test.py` is the higher-level "is this build shippable?" gate. Both
should be green before any release:

```bash
pytest
python local_test.py
```

Pytest covers 73 tests at 82% coverage; `local_test.py` adds 131 end-to-end checks against the
installed package.
