# Contributing

## Dev setup

```bash
git clone https://github.com/Parv-01/gpualert.git
cd gpualert
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The `[dev]` extra pulls in pytest, pytest-cov, pytest-mock, black, ruff, and mypy.

## Running tests

```bash
pytest                          # full suite with coverage
pytest tests/test_day4.py       # one file
pytest -k "test_email"          # by name
pytest -x --ff                  # stop on first failure, run failures first
```

The suite is fully offline — no SMTP, no network, no real Slurm. Anything network-shaped is
mocked (see `tests/test_day4.py` for the SMTP patches). Tests should keep this property; if you
need to add an integration test, gate it behind an env var and skip by default.

## Style

- `black` for formatting (line length 100).
- `ruff` for lint (`E`, `F`, `W`, `I` rule families).
- Comments explain *why*, not *what*. The code already says what.
- No filler in docstrings. Lead with the contract, then notes.
- Conventional Commits for messages: `feat(scope): …`, `fix(scope): …`, `docs(scope): …`,
  `chore(scope): …`, `test(scope): …`. No emoji-heavy subjects.

Run the checks before opening a PR:

```bash
black --check gpualert tests
ruff check gpualert tests
pytest
```

## Architecture rules to preserve

1. **Logs exist before the process starts.** Don't move log file creation later in the call path.
2. **The notifier never raises.** Every new failure mode in `email_notifier.py` (or any future
   notifier) must be caught and turned into a `NotificationResult(success=False, ...)`.
3. **`load_config` never raises.** Corrupt files fall back to defaults with a logged warning.
4. **CLI exit code follows the job, not the notifier.** A failed notification must not turn a
   successful job into a non-zero exit.
5. **Failure emails always include logs.** `attach_logs_on_failure` exists in the config schema
   for symmetry; it is ignored at runtime.

If you find yourself wanting to break any of these, open an issue first.

## Adding a notifier backend

1. Subclass `gpualert.notifier.base.BaseNotifier`.
2. Implement `send(self, result, attachments) -> NotificationResult`. It must not raise.
3. Wire it into `gpualert.notifier.email_notifier.get_notifier` (or a new `get_notifier` if
   you're factoring backends out of that file — that refactor is welcome).
4. Add a config section to `gpualert.config` for the backend's settings.
5. Tests: mock the network layer the same way `test_day4.py` mocks `smtplib.SMTP`.

## Filing issues

Useful issue reports include: GPUAlert version (`gpualert version`), Python version, OS, the
command you ran, the relevant lines of `combined.log`, and a redacted `gpualert config --show`
output. Don't paste real passwords; `--show` masks them but double-check before submitting.
