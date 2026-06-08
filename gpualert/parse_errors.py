"""
gpualert.parse_errors — Pattern-based error and metric extraction.

Scans stdout/stderr for known failure modes (CUDA OOM, NCCL, tracebacks, ...)
and pulls success metrics (accuracy, loss, F1, etc.) from training logs.

All functions are pure and never raise.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# (regex_pattern, short_label, suggestion) — order = priority (first match wins)
ERROR_PATTERNS: List[Tuple[str, str, str]] = [
    (
        r"CUDA\s+out\s+of\s+memory|cuda out of memory|CUDNN_STATUS_ALLOC_FAILED",
        "GPU out-of-memory (CUDA OOM)",
        "Try reducing batch size, using gradient checkpointing, or a larger GPU.",
    ),
    (
        r"NCCL\s+error|Timeout waiting for NCCL",
        "NCCL communication error",
        "Check network between nodes. Try NCCL_DEBUG=INFO for more details.",
    ),
    (
        r"RuntimeError:\s+CUDA error",
        "CUDA runtime error",
        "Check nvidia-smi. The GPU may be faulty or drivers out of date.",
    ),
    (
        r"MemoryError|Cannot allocate memory|std::bad_alloc",
        "System out-of-memory (RAM)",
        "Reduce dataset size loaded into RAM or add swap space.",
    ),
    (
        r"Segmentation fault|core dumped",
        "Segmentation fault",
        "Likely a C extension bug. Check your C/CUDA library versions.",
    ),
    (
        r"FileNotFoundError:",
        "File not found",
        "Check your data paths and working directory.",
    ),
    (
        r"PermissionError:",
        "Permission denied",
        "Check file/directory permissions.",
    ),
    (
        r"ImportError:|ModuleNotFoundError:",
        "Missing Python module",
        "Run pip install -r requirements.txt",
    ),
    (
        r"DivisionByZero|ZeroDivisionError",
        "Division by zero",
        "Check for zero denominators in your loss or metric calculation.",
    ),
    (
        r"RuntimeError:\s+Expected all tensors.*same device",
        "Tensor device mismatch",
        "Move all tensors to the same device before operations.",
    ),
    (
        r"loss.*nan|NaN.*loss|nan.*detected",
        "NaN detected in loss",
        "Check learning rate, data normalization, and gradient clipping.",
    ),
    (
        r"Killed\s*$|OOM\s+killer|Out of memory: Kill",
        "Process killed by OS (OOM)",
        "System ran out of memory. Request more RAM or reduce memory usage.",
    ),
    (
        r"AssertionError:",
        "Assertion failed",
        "A code assertion failed. Check your data shapes and assumptions.",
    ),
    (
        r"RuntimeError:",
        "Python RuntimeError",
        "Check stderr.log for the full traceback.",
    ),
    (
        r"Traceback \(most recent call last\)",
        "Python exception (traceback)",
        "See traceback in attached stderr.log for details.",
    ),
]


def parse_errors(stdout: str, stderr: str, exit_code: int = 0) -> str:
    """
    Return a human-readable error summary or '' if no errors detected.
    Searches stderr first (typical source), then stdout.
    """
    combined = (stderr or "") + "\n" + (stdout or "")
    for pattern, label, suggestion in ERROR_PATTERNS:
        try:
            if re.search(pattern, combined, re.IGNORECASE):
                return f"{label}\nSuggestion: {suggestion}"
        except re.error:
            continue

    if exit_code not in (0, None):
        return f"Process exited with code {exit_code}. Check stderr.log for details."
    return ""


def extract_traceback(stderr: str, max_lines: int = 20) -> str:
    """Extract the most recent Python traceback. Returns '' if none."""
    if not stderr:
        return ""
    lines = stderr.splitlines()
    last_tb_idx = None
    for i, line in enumerate(lines):
        if "Traceback (most recent call last)" in line:
            last_tb_idx = i
    if last_tb_idx is None:
        return ""
    return "\n".join(lines[last_tb_idx : last_tb_idx + max_lines])


def extract_success_metrics(stdout: str) -> str:
    """Extract ML metrics like accuracy, loss, F1. Returns '' if none."""
    if not stdout:
        return ""
    metrics: List[str] = []
    patterns = [
        (r"accuracy[:\s=]+([0-9.]+%?)", "Accuracy"),
        (r"loss[:\s=]+([0-9.]+)", "Loss"),
        (r"epoch[s]?[:\s]+([0-9]+)", "Epochs"),
        (r"best.*acc.*[:\s=]+([0-9.]+%?)", "Best accuracy"),
        (r"val.*loss[:\s=]+([0-9.]+)", "Val loss"),
        (r"f1[:\s=]+([0-9.]+)", "F1"),
        (r"mAP[:\s=]+([0-9.]+)", "mAP"),
    ]
    for pattern, label in patterns:
        try:
            matches = re.findall(pattern, stdout, re.IGNORECASE)
            if matches:
                metrics.append(f"{label}: {matches[-1]}")
        except re.error:
            continue
    return " | ".join(metrics[:4]) if metrics else ""


def get_error_confidence(error_summary: str) -> str:
    """Return 'HIGH' | 'MEDIUM' | 'LOW' | 'NONE' based on summary specificity."""
    if not error_summary:
        return "NONE"
    if "Suggestion:" in error_summary:
        return "HIGH"
    if "Traceback" in error_summary or "exited with code" in error_summary:
        return "MEDIUM"
    return "LOW"
