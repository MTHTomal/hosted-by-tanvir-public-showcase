import csv
import json
import shutil
import uuid
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from local_tools.benchmark_prediction_pipeline import (
    BENCHMARK_RESULT_HEADERS,
    TorchProbe,
    run_benchmark,
)
from tournament.tests_prediction_baseline_consumer import build_package_bytes


UNAVAILABLE_TORCH = TorchProbe(
    available=False,
    cuda_available=False,
    module=None,
    notes="PyTorch is not installed locally.",
)


class LocalPredictionBenchmarkTests(TestCase):
    def run_package(
        self,
        *,
        include_unresolved=True,
        sensitive_player_username=False,
        repeat_count=1,
    ):
        base_path = Path.cwd() / f"phase44_benchmark_test_{uuid.uuid4().hex}"
        base_path.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(base_path, ignore_errors=True))
        package_path = base_path / "prediction_package.zip"
        output_dir = base_path / "benchmark_output"
        package_path.write_bytes(
            build_package_bytes(
                include_unresolved=include_unresolved,
                sensitive_player_username=sensitive_player_username,
            )
        )

        with patch(
            "local_tools.benchmark_prediction_pipeline._probe_torch",
            return_value=UNAVAILABLE_TORCH,
        ):
            result = run_benchmark(
                package_path,
                repeat_count=repeat_count,
                output_dir=output_dir,
            )
        return result

    def test_benchmark_runs_valid_package_and_creates_required_outputs(self):
        result = self.run_package()

        self.assertTrue(result.output_files["benchmark_results.csv"].exists())
        self.assertTrue(result.output_files["benchmark_summary.json"].exists())
        self.assertTrue(result.output_files["benchmark_manifest.json"].exists())

        with result.output_files["benchmark_results.csv"].open(newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, BENCHMARK_RESULT_HEADERS)
            rows = list(reader)

        self.assertTrue(any(row["mode"] == "sequential_cpu" for row in rows))
        sequential_rows = [row for row in rows if row["mode"] == "sequential_cpu"]
        self.assertEqual(sequential_rows[0]["available"], "true")
        self.assertEqual(sequential_rows[0]["input_fixture_count"], "1")
        self.assertEqual(sequential_rows[0]["prediction_count"], "1")

    def test_optional_torch_modes_are_reported_unavailable_without_crashing(self):
        result = self.run_package()

        with result.output_files["benchmark_results.csv"].open(newline="") as handle:
            rows = list(csv.DictReader(handle))

        torch_rows = [
            row for row in rows if row["mode"] in {"torch_cpu_tensor", "torch_cuda_tensor"}
        ]
        self.assertEqual(len(torch_rows), 2)
        for row in torch_rows:
            self.assertEqual(row["available"], "false")
            self.assertIn("PyTorch", row["notes"])

        summary = json.loads(result.output_files["benchmark_summary.json"].read_text())
        self.assertFalse(summary["torch_available"])
        self.assertFalse(summary["cuda_available"])

    def test_summary_includes_speedup_fields_or_safe_nulls(self):
        result = self.run_package()
        summary = json.loads(result.output_files["benchmark_summary.json"].read_text())

        self.assertIn("fastest_mode", summary)
        self.assertIn("mode_summary", summary)
        self.assertIn("speedup_vs_sequential", summary)
        self.assertIn("sequential_cpu", summary["speedup_vs_sequential"])
        self.assertIn("multiprocessing_cpu", summary["speedup_vs_sequential"])
        self.assertIn("torch_cpu_tensor", summary["speedup_vs_sequential"])
        self.assertIsNotNone(summary["mode_summary"]["sequential_cpu"]["avg_elapsed_ms"])

    def test_sensitive_package_fields_do_not_appear_in_benchmark_outputs(self):
        result = self.run_package(sensitive_player_username=True)

        combined_output = "\n".join(
            path.read_text()
            for path in result.output_files.values()
        )
        self.assertNotIn("secret@example.com", combined_output)
        self.assertNotIn("player_username", combined_output)
        self.assertNotIn("email", combined_output.lower())

    def test_empty_fixture_package_keeps_sequential_available(self):
        result = self.run_package(include_unresolved=False)
        summary = json.loads(result.output_files["benchmark_summary.json"].read_text())

        self.assertTrue(summary["mode_summary"]["sequential_cpu"]["available"])
        self.assertEqual(summary["mode_summary"]["sequential_cpu"]["prediction_count"], 0)
        self.assertTrue(
            any("No unresolved eligible fixtures" in warning for warning in summary["warnings"])
        )
