"""
Simulated training run that fails with a CUDA OOM error.

Useful for verifying GPUAlert's failure-path: log attachment, error parsing,
and the email's stderr-tail section.

Run with:
    gpualert run -- python examples/failing_training.py
    gpualert run --dry-run -- python examples/failing_training.py
"""

from __future__ import annotations

import sys
import time

print("Starting training...")
print("Loading model to CUDA...")
time.sleep(0.5)

for step in range(1, 6):
    print(f"Step {step}/5 — loss: {2.0 - step * 0.1:.4f}")
    time.sleep(0.1)

sys.stderr.write("\nRuntimeError: CUDA out of memory. Tried to allocate 8.00 GiB\n")
sys.stderr.write("  (malloc at /opt/conda/lib/python3.10/site-packages/torch/cuda/memory.py)\n")
sys.stderr.flush()
sys.exit(1)
