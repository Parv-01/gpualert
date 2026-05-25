# Python API Reference

GPUAlert is primarily a CLI, but the building blocks are importable. The intended use cases are
wrapping a job from inside a larger Python orchestrator, or sending notifications about events
that did not come from `subprocess.Popen`.

```python
from gpualert import (
    JobResult,
    ArtifactFile,
    NotificationResult,
    SlurmJobInfo,
    GPUAlertConfig,
    load_config,
)
```

## `gpualert.launcher.run_job`

```python
from gpualert.launcher import run_job

result = run_job(
    cmd=["python", "train.py"],
    cwd=None,
    timeout=None,
    env=None,
    verbose=False,
)
```

Runs `cmd` and returns a `JobResult`. Log files at `result.stdout_log_path`, `result.stderr_log_path`,
and `result.combined_log_path` are guaranteed to exist on disk before this returns, even if the
process crashed. The function never raises — every failure path goes through `result.status`.

## `gpualert.slurm.poll_job`

```python
from gpualert.slurm import poll_job, SlurmNotAvailableError

try:
    result = poll_job(
        job_id=12345,
        interval=10,
        timeout=None,
        on_update=lambda info: print(info.state, info.elapsed_seconds),
    )
except SlurmNotAvailableError:
    ...
```

Polls `sacct` for a Slurm job until it reaches a terminal state. The `on_update` callback fires
each poll with the latest `SlurmJobInfo`. Exceptions raised inside `on_update` are swallowed —
your callback should not crash the poll loop.

## `gpualert.artifacts`

```python
from datetime import datetime
from gpualert.artifacts import (
    find_artifacts,
    prepare_attachments,
    summarize_artifacts,
)

artifacts = find_artifacts(
    start_time=datetime.now(),
    cwd=".",
    patterns=["*.csv", "*.png"],
    max_single_mb=25.0,
    max_depth=3,
)

attach_list, skipped = prepare_attachments(
    artifacts=artifacts,
    log_files=["/path/to/combined.log"],
    job_failed=False,
    max_total_mb=45.0,
    attach_logs=True,
)
```

`find_artifacts` walks `cwd` (skipping `.git`, `__pycache__`, virtualenvs, etc.) and returns files
newer than `start_time` that match `patterns`. `prepare_attachments` decides which of those — plus
the log files — actually go into the email, enforcing the per-file and total-size budgets. Any
files that don't fit get zipped into `artifacts_overflow.zip` alongside the logs.

## `gpualert.notifier`

```python
from gpualert.notifier.email_notifier import get_notifier

notifier = get_notifier(config, dry_run=False)
notification = notifier.send(result, attachments=["/path/to/combined.log"])
print(notification.success, notification.message)
```

`get_notifier` returns a `DryRunNotifier` if `dry_run=True` or `config.dry_run=True`, otherwise an
`EmailNotifier`. Both expose the same `send(result, attachments) -> NotificationResult` interface.
Neither raises.

## `gpualert.config`

```python
from gpualert.config import (
    load_config,
    save_config,
    validate_config,
    get_config_path,
)

cfg = load_config()
ok, errors = validate_config(cfg)
if not ok:
    for e in errors:
        print(e)
```

`load_config()` reads `~/.gpualert/config.toml`, creating it with defaults if missing and falling
back to defaults on parse error. `validate_config()` is offline and synchronous; it only checks
field presence and shape, not SMTP reachability.

## Data structures

### `JobResult`

```python
@dataclass
class JobResult:
    command: str
    job_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    status: str = "pending"        # pending | success | failed | timeout | interrupted | error
    exit_code: Optional[int] = None
    stdout_log_path: str = ""
    stderr_log_path: str = ""
    combined_log_path: str = ""
    stdout_tail: str = ""          # last 50 lines
    stderr_tail: str = ""          # last 50 lines
    error_summary: str = ""        # human-readable, derived from parse_errors
    artifacts: list = []
```

Helpers:

- `is_success()` — true iff `status == "success"`.
- `is_failed()` — true for `failed`, `timeout`, `interrupted`, `error`.
- `duration_human()` — `"2h 15m 3s"` formatted.
- `log_files()` — list of log paths that exist on disk.

### `ArtifactFile`

```python
@dataclass
class ArtifactFile:
    path: str
    size_bytes: int = 0
    extension: str = ""
```

Helpers: `size_mb()`, `filename()`.

### `NotificationResult`

```python
@dataclass
class NotificationResult:
    success: bool
    notifier_type: str    # "email" | "dry_run" | future backends
    message: str = ""
    timestamp: Optional[datetime] = None
```

### `SlurmJobInfo`

```python
@dataclass
class SlurmJobInfo:
    job_id: int
    state: str = "UNKNOWN"   # RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, ...
    exit_code: int = 0
    elapsed_seconds: float = 0.0
    job_name: str = ""
    partition: str = ""
    node_list: str = ""
```
