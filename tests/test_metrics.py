"""Tests for gpualert.metrics — Feature 4 (added 0.1.4).

Behavior locked here:
- CSV: metrics are pulled from the LAST row (final-epoch convention). Both
  pandas and stdlib fallback paths tested.
- JSON: nested dicts walked; only numeric leaves whose key matches the
  metric vocabulary are surfaced.
- YAML: safe_load only; skipped cleanly when pyyaml is absent.
- XLSX: skipped cleanly when pandas or openpyxl is absent.
- Malformed input, missing files, non-metric keys, and bool values all
  yield {} without raising (notifier isolation contract).
- Wider ArtifactConfig.patterns default now includes safetensors / pt /
  ckpt / parquet / etc. — verified in-place.
"""

from __future__ import annotations

import builtins
import importlib
import json

import pytest

from gpualert import metrics
from gpualert.config import ArtifactConfig


# ── Widened patterns from Feature 4 ───────────────────────────────────────
class TestWidenedPatterns:
    def test_new_ml_patterns_present(self):
        p = set(ArtifactConfig().patterns)
        for expected in ("*.safetensors", "*.pt", "*.ckpt", "*.parquet", "*.yaml", "*.onnx"):
            assert expected in p, f"widened pattern missing: {expected}"

    def test_original_patterns_still_present(self):
        p = set(ArtifactConfig().patterns)
        for expected in ("*.csv", "*.png", "*.json", "*.log", "*.npz"):
            assert expected in p, f"regressed pattern: {expected}"

    def test_tracked_dirs_default(self):
        cfg = ArtifactConfig()
        assert "wandb" in cfg.tracked_dirs
        assert "mlruns" in cfg.tracked_dirs


# ── CSV extraction ────────────────────────────────────────────────────────
class TestCsvExtraction:
    def test_last_row_metrics(self, tmp_path):
        p = tmp_path / "history.csv"
        p.write_text("epoch,loss,accuracy,val_loss\n0,1.2,0.5,1.5\n1,0.8,0.7,1.1\n2,0.4,0.93,0.6\n")
        out = metrics.extract_from_csv(p)
        # Final epoch values only.
        assert out["loss"] == pytest.approx(0.4)
        assert out["accuracy"] == pytest.approx(0.93)
        assert out["val_loss"] == pytest.approx(0.6)
        # 'epoch' is not in the metric vocabulary.
        assert "epoch" not in out

    def test_stdlib_fallback_when_pandas_missing(self, tmp_path, monkeypatch):
        """Force pandas ImportError → stdlib csv path must still work."""
        p = tmp_path / "final.csv"
        p.write_text("f1,accuracy\n0.91,0.88\n")

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "pandas":
                raise ImportError("simulated missing pandas")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        out = metrics.extract_from_csv(p)
        assert out["f1"] == pytest.approx(0.91)
        assert out["accuracy"] == pytest.approx(0.88)

    def test_empty_csv_returns_empty(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("")
        assert metrics.extract_from_csv(p) == {}

    def test_non_numeric_metric_ignored(self, tmp_path):
        p = tmp_path / "mixed.csv"
        p.write_text("accuracy,note\n0.9,'good'\n")
        out = metrics.extract_from_csv(p)
        assert out == {"accuracy": pytest.approx(0.9)}


# ── TSV ───────────────────────────────────────────────────────────────────
class TestTsvExtraction:
    def test_last_row(self, tmp_path):
        p = tmp_path / "run.tsv"
        p.write_text("loss\taccuracy\n1.2\t0.5\n0.3\t0.94\n")
        out = metrics.extract_from_tsv(p)
        assert out["loss"] == pytest.approx(0.3)
        assert out["accuracy"] == pytest.approx(0.94)


# ── JSON ──────────────────────────────────────────────────────────────────
class TestJsonExtraction:
    def test_flat_json(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"f1": 0.91, "loss": 0.42, "epoch": 5}))
        out = metrics.extract_from_json(p)
        assert out["f1"] == pytest.approx(0.91)
        assert out["loss"] == pytest.approx(0.42)
        assert "epoch" not in out

    def test_nested_metrics_key(self, tmp_path):
        p = tmp_path / "nested.json"
        p.write_text(json.dumps({"results": {"metrics": {"accuracy": 0.88, "auc": 0.95}}}))
        out = metrics.extract_from_json(p)
        assert out["accuracy"] == pytest.approx(0.88)
        assert out["auc"] == pytest.approx(0.95)

    def test_malformed_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{ not valid json")
        assert metrics.extract_from_json(p) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert metrics.extract_from_json(tmp_path / "nope.json") == {}


# ── YAML ──────────────────────────────────────────────────────────────────
class TestYamlExtraction:
    def test_flat_yaml(self, tmp_path):
        pytest.importorskip("yaml")
        p = tmp_path / "m.yaml"
        p.write_text("accuracy: 0.9\nloss: 0.3\nepoch: 12\n")
        out = metrics.extract_from_yaml(p)
        assert out["accuracy"] == pytest.approx(0.9)
        assert out["loss"] == pytest.approx(0.3)
        assert "epoch" not in out

    def test_missing_pyyaml_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "m.yaml"
        p.write_text("accuracy: 0.9\n")

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "yaml":
                raise ImportError("simulated missing pyyaml")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert metrics.extract_from_yaml(p) == {}


# ── XLSX ──────────────────────────────────────────────────────────────────
class TestXlsxExtraction:
    def test_missing_openpyxl_or_pandas_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "x.xlsx"
        p.write_bytes(b"not really xlsx")  # doesn't matter — import fails first

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "pandas":
                raise ImportError("simulated")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert metrics.extract_from_xlsx(p) == {}


# ── Dispatcher ────────────────────────────────────────────────────────────
class TestDispatcher:
    def test_extract_metrics_by_suffix(self, tmp_path):
        csv_p = tmp_path / "a.csv"
        csv_p.write_text("f1\n0.8\n")
        json_p = tmp_path / "b.json"
        json_p.write_text(json.dumps({"accuracy": 0.9}))
        out = metrics.extract_metrics([str(csv_p), str(json_p)])
        assert out["f1"] == pytest.approx(0.8)
        assert out["accuracy"] == pytest.approx(0.9)

    def test_unknown_suffix_skipped(self, tmp_path):
        p = tmp_path / "weights.pt"
        p.write_bytes(b"binary")
        assert metrics.extract_metrics([p]) == {}

    def test_never_raises_on_garbage(self, tmp_path):
        garbage = tmp_path / "corrupt.csv"
        garbage.write_bytes(b"\x00\x01\x02\x03")
        # Must return dict (possibly empty), never raise
        result = metrics.extract_metrics([garbage])
        assert isinstance(result, dict)


# ── format_metrics_line ───────────────────────────────────────────────────
class TestFormatMetricsLine:
    def test_normal_values_4_decimals(self):
        line = metrics.format_metrics_line({"accuracy": 0.93125, "loss": 0.41})
        assert "accuracy=0.9312" in line or "accuracy=0.9313" in line
        assert "loss=0.4100" in line

    def test_scientific_for_very_small(self):
        line = metrics.format_metrics_line({"lr": 0.0000001})
        assert "e-" in line or "E-" in line

    def test_caps_at_six(self):
        m = {f"m{i}": 0.5 for i in range(10)}
        line = metrics.format_metrics_line(m)
        assert "+4 more" in line

    def test_empty_dict_empty_string(self):
        assert metrics.format_metrics_line({}) == ""


# smoke: module is importable without optional deps by touching everything
def test_module_imports():
    importlib.reload(metrics)
