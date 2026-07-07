"""gpualert.metrics — Extract ML metrics from artifact files (0.1.4+).

Reads the final row / relevant keys from CSV, TSV, XLSX, JSON, and YAML
output artifacts and returns a flat {metric_name: value} dict for the
email body. Every function is defensive: import errors, parse errors,
and malformed files all yield {} rather than raising, so the notifier
isolation contract is preserved.

Optional dependencies (kept behind the `gpualert[metrics]` extra):
- pandas: preferred CSV/XLSX reader; falls back to stdlib `csv` for CSV.
- openpyxl: required for XLSX (via pandas). Missing → XLSX returns {}.
- pyyaml: required for YAML. Missing → YAML returns {}.
- pyarrow: required for parquet via pandas. Missing → parquet returns {}.

The core install stays dependency-light. The stdlib paths cover the
most common cases (CSV + JSON) with zero extra deps.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable

# Author signature in an internal constant.
_PARV_METRIC_VOCAB = (
    "loss|acc|accuracy|f1|map|auc|bleu|rouge|"
    "perplexity|ppl|psnr|ssim|iou|dice|"
    "mae|mse|rmse|r2|top1|top5|em"
)
METRIC_RE = re.compile(
    r"^(val_|test_|train_|eval_)?(" + _PARV_METRIC_VOCAB + r")$",
    re.IGNORECASE,
)


def _is_metric_name(name: Any) -> bool:
    if name is None:
        return False
    return bool(METRIC_RE.match(str(name).strip()))


def _coerce_numeric(v: Any) -> float | None:
    if isinstance(v, bool):
        return None  # avoid True→1.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def extract_from_csv(path: Path) -> Dict[str, float]:
    """Read final data row; return numeric columns whose header matches
    the metric vocabulary. Tries pandas first, falls back to stdlib csv."""
    try:
        import pandas as pd  # type: ignore

        df = pd.read_csv(path)
        if df.empty:
            return {}
        last = df.tail(1).to_dict("records")[0]
        out: Dict[str, float] = {}
        for k, v in last.items():
            if _is_metric_name(k):
                num = _coerce_numeric(v)
                if num is not None:
                    out[str(k)] = num
        return out
    except Exception:
        # stdlib fallback: read last data row only
        try:
            with open(path, newline="", encoding="utf-8", errors="replace") as fh:
                rows = list(csv.DictReader(fh))
            if not rows:
                return {}
            last_row = rows[-1]
            out = {}
            for k, v in last_row.items():
                if _is_metric_name(k):
                    num = _coerce_numeric(v)
                    if num is not None:
                        out[str(k)] = num
            return out
        except OSError:
            return {}
        except Exception:
            return {}


def extract_from_tsv(path: Path) -> Dict[str, float]:
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
        if not rows:
            return {}
        last_row = rows[-1]
        out: Dict[str, float] = {}
        for k, v in last_row.items():
            if _is_metric_name(k):
                num = _coerce_numeric(v)
                if num is not None:
                    out[str(k)] = num
        return out
    except Exception:
        return {}


def extract_from_json(path: Path) -> Dict[str, float]:
    """Walk the JSON tree; collect numeric leaves whose key matches metric vocab."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    out: Dict[str, float] = {}

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if _is_metric_name(k):
                    num = _coerce_numeric(v)
                    if num is not None:
                        out[str(k)] = num
                    else:
                        walk(v)
                else:
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    try:
        walk(data)
    except Exception:
        return {}
    return out


def extract_from_xlsx(path: Path) -> Dict[str, float]:
    try:
        import pandas as pd  # type: ignore

        df = pd.read_excel(path, engine="openpyxl")
        if df.empty:
            return {}
        last = df.tail(1).to_dict("records")[0]
        out: Dict[str, float] = {}
        for k, v in last.items():
            if _is_metric_name(k):
                num = _coerce_numeric(v)
                if num is not None:
                    out[str(k)] = num
        return out
    except Exception:
        return {}


def extract_from_yaml(path: Path) -> Dict[str, float]:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in data.items():
        if _is_metric_name(k):
            num = _coerce_numeric(v)
            if num is not None:
                out[str(k)] = num
    return out


DISPATCH = {
    ".csv": extract_from_csv,
    ".tsv": extract_from_tsv,
    ".json": extract_from_json,
    ".xlsx": extract_from_xlsx,
    ".yaml": extract_from_yaml,
    ".yml": extract_from_yaml,
}


def extract_metrics(paths: Iterable[str | Path]) -> Dict[str, float]:
    """Merge metrics found across all provided artifact paths. Later files
    win on duplicate keys. Never raises."""
    merged: Dict[str, float] = {}
    for p in paths or []:
        try:
            path = Path(p)
            fn = DISPATCH.get(path.suffix.lower())
            if fn is None:
                continue
            merged.update(fn(path))
        except Exception:
            continue
    return merged


def format_metrics_line(metrics: Dict[str, float]) -> str:
    """Render metrics as `key1=0.9312, key2=0.5040` — capped at 6 to keep
    the email body readable."""
    if not metrics:
        return ""
    items = list(metrics.items())[:6]
    parts = []
    for k, v in items:
        # 4 sig-figs is enough for a status email
        if abs(v) < 0.001 or abs(v) >= 10000:
            parts.append(f"{k}={v:.3e}")
        else:
            parts.append(f"{k}={v:.4f}")
    line = ", ".join(parts)
    if len(metrics) > 6:
        line += f" (+{len(metrics) - 6} more)"
    return line
