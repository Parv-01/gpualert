# Robustness harness

Fault injection and contract tests. These do not check features; they check that the
wrapper keeps its promises while the world misbehaves.

## Layout

| File | Covers |
| --- | --- |
| `probes.py` | observation — descendants, survivors, fd/thread counts, out-of-process wrapper, log integrity |
| `faults.py` | misbehaving child programs — bad bytes, huge output, grandchildren, double-forks, SIGTERM-ignorers |
| `test_contracts.py` | the invariants: never raises, log guarantee, exit-code fidelity, non-silent budget |
| `test_output_shapes.py` | non-UTF8 bytes, unterminated lines, ANSI/CRLF, volume, tail reader |
| `test_signals.py` | SIGTERM / SIGINT / SIGHUP / SIGKILL against a running wrapper |
| `test_process_shapes.py` | grandchildren, daemonised workers, shutdown ladder, resource hygiene |
| `test_filesystem.py` | unwritable home, ENOSPC, log directory removed mid-run |
| `test_concurrency.py` | artifact scoping and parallel wrappers sharing a node |
| `test_artifact_safety.py` | ownership, run window, file types, tool state, read-time containment |

## Reading the results

A known defect is recorded as `xfail(strict=True)` naming its defect id. Strict matters:
when a fix lands, the test starts passing and pytest reports it as a failure, which is the
signal to delete the marker. Nothing here is allowed to pass quietly for the wrong reason.

## Running

```bash
pytest tests/robustness -m "not slow"     # quick pass, seconds
pytest tests/robustness                   # everything, ~2 minutes
pytest tests/robustness -m slow           # only the long ones
python tests/robustness/soak.py --minutes 60
```

## Open defect ids

| Id | Defect |
| --- | --- |
| D1 | a non-UTF8 byte silently discards all following output |
| D2 | SIGTERM / SIGHUP unhandled — no result, no footer |
| D3 | only the direct child is killed; workers are orphaned |
| D4 | artifact scanning claims files this job did not create |
| D5 | `run_job` raises when the log directory is unwritable |
| D6 | `get_tail()` loads the whole file into memory |
| D7 | no log rotation or size cap |
| D10 | timeout goes straight to SIGKILL with no graceful SIGTERM first |
| D11 | a FIFO named like an output hangs the wrapper |
| D12 | a non-executable command exits 1 instead of 126 |
| D13 | `run_job` raises from a deleted working directory |
| D14 | the artifact scan reads `~/.gpualert` and hidden directories |
| D15 | attachments are re-opened by path, so a post-scan symlink swap is followed |
| D16 | the overflow zip can be written into the working directory |

D8 (no GPU awareness) and D9 (no job registry) are absences rather than defects and are
covered by the 0.3.0 and 0.4.0 designs rather than by tests here. The full ledger,
with reproductions and status, is `DEFECTS.md` at the repo root.

Tests that change permission bits skip under root, because root ignores them and
the test would pass for the wrong reason. CI runs as an ordinary user.
