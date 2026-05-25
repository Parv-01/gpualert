# GPUAlert Examples

Three demo scripts that exercise the package's main paths. Run them through
`gpualert run` to see the full success/failure/timeout flows without writing
any real training code.

## Success demo

```bash
gpualert run -- python examples/simple_training.py
gpualert run --dry-run -- python examples/simple_training.py 20
```

Simulates 10 epochs of ML training. Writes `outputs/metrics.csv` and
`outputs/summary.txt`. GPUAlert detects both as artifacts and attaches them
to the email along with the three log files.

## Failure demo (CUDA OOM)

```bash
gpualert run -- python examples/failing_training.py
gpualert run --dry-run -- python examples/failing_training.py
```

Simulates a CUDA OOM crash mid-training. The error parser identifies the
failure mode, the email subject reads `❌ FAILED`, and the email body
includes the LAST 15 LINES OF STDERR with the relevant traceback.

## Timeout demo

```bash
gpualert run --timeout 5 -- python examples/long_running_job.py 60
gpualert run --dry-run -- python examples/long_running_job.py 10
```

A 60-step job killed after 5 seconds. Status reads `TIMEOUT`, logs still
attached.

## Notes

- All three are safe to run without `gpualert config --init` if you pass `--dry-run`.
- `simple_training.py` creates an `outputs/` directory in your current working
  directory. Clean it up afterwards if you don't want it sitting around.
- These scripts use the standard library only — no torch, no numpy, no GPU
  needed. The point is to exercise GPUAlert's wrapper, not to actually train
  anything.
