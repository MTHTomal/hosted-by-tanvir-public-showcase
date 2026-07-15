#!/usr/bin/env python
"""Benchmark local prediction processing from a Phase 4.2 ZIP package.

This script is intentionally local-only. It does not import Django, read
application settings, open a database connection, call external services, or
require PyTorch/CUDA. Optional tensor modes are probes only when PyTorch is
already installed on the local machine.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Callable

from local_tools.run_prediction_baseline import (
    FORMULA_VERSION,
    BaselineError,
    PackageData,
    eligible_prediction_fixture_rows,
    generate_predictions,
    load_package,
    predict_fixture_from_package_rows,
)


SCRIPT_NAME = "local_tools/benchmark_prediction_pipeline.py"
BENCHMARK_VERSION = "phase-4.4-local-benchmark-v1"
DEFAULT_REPEAT_COUNT = 5

BENCHMARK_RESULT_HEADERS = [
    "mode",
    "repeat_index",
    "input_fixture_count",
    "prediction_count",
    "elapsed_ms",
    "throughput_predictions_per_second",
    "available",
    "notes",
]

BENCHMARK_MODES = [
    "sequential_cpu",
    "multiprocessing_cpu",
    "torch_cpu_tensor",
    "torch_cuda_tensor",
]


class BenchmarkError(Exception):
    """Raised when the benchmark cannot run against the input package."""


class BenchmarkModeUnavailable(Exception):
    """Raised when an optional benchmark mode cannot run locally."""


@dataclass(frozen=True)
class TorchProbe:
    available: bool
    cuda_available: bool
    module: object | None
    notes: str


@dataclass(frozen=True)
class BenchmarkSample:
    mode: str
    repeat_index: int
    input_fixture_count: int
    prediction_count: int
    elapsed_ms: float | None
    throughput_predictions_per_second: float | None
    available: bool
    notes: str


@dataclass(frozen=True)
class BenchmarkRunResult:
    output_dir: Path
    output_files: dict[str, Path]
    samples: list[BenchmarkSample]
    summary: dict[str, object]
    manifest: dict[str, object]


_WORKER_ROWS_BY_FILE: dict[str, list[dict[str, str]]] | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _round_float(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _elapsed_ms(start_time: float) -> float:
    return (time.perf_counter() - start_time) * 1000


def _throughput(prediction_count: int, elapsed_ms: float | None) -> float | None:
    if elapsed_ms is None or elapsed_ms <= 0:
        return None
    return prediction_count / (elapsed_ms / 1000)


def _prediction_numeric_row(row: dict[str, str]) -> list[float]:
    return [
        float(row.get("home_win_probability") or 0),
        float(row.get("draw_probability") or 0),
        float(row.get("away_win_probability") or 0),
        float(row.get("expected_home_goals") or 0),
        float(row.get("expected_away_goals") or 0),
    ]


def _probe_torch() -> TorchProbe:
    if importlib.util.find_spec("torch") is None:
        return TorchProbe(
            available=False,
            cuda_available=False,
            module=None,
            notes="PyTorch is not installed locally.",
        )

    try:
        import torch  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on local install health.
        return TorchProbe(
            available=False,
            cuda_available=False,
            module=None,
            notes=f"PyTorch import failed: {exc}",
        )

    cuda_available = False
    try:
        cuda_available = bool(torch.cuda.is_available())
    except Exception as exc:  # pragma: no cover - depends on local driver state.
        return TorchProbe(
            available=True,
            cuda_available=False,
            module=torch,
            notes=f"PyTorch imported, but CUDA probe failed: {exc}",
        )

    return TorchProbe(
        available=True,
        cuda_available=cuda_available,
        module=torch,
        notes=(
            "PyTorch imported; CUDA is available."
            if cuda_available
            else "PyTorch imported; CUDA is not available."
        ),
    )


def _run_sequential_cpu(
    package_data: PackageData,
    _eligible_fixtures: list[dict[str, str]],
) -> tuple[int, str]:
    predictions, _eligible_count, warnings = generate_predictions(package_data)
    notes = "Sequential CPU baseline using Phase 4.3 package logic."
    if warnings:
        notes = f"{notes} Warnings: {' | '.join(warnings)}"
    return len(predictions), notes


def _init_worker(rows_by_file: dict[str, list[dict[str, str]]]) -> None:
    global _WORKER_ROWS_BY_FILE
    _WORKER_ROWS_BY_FILE = rows_by_file


def _multiprocessing_predict_fixture(
    fixture_row: dict[str, str],
) -> tuple[dict[str, str] | None, str | None]:
    if _WORKER_ROWS_BY_FILE is None:
        raise RuntimeError("Multiprocessing worker was not initialized.")
    prediction, unavailable_reason, _details = predict_fixture_from_package_rows(
        fixture_row,
        _WORKER_ROWS_BY_FILE,
    )
    return prediction, unavailable_reason


def _run_multiprocessing_cpu(
    package_data: PackageData,
    eligible_fixtures: list[dict[str, str]],
) -> tuple[int, str]:
    if not eligible_fixtures:
        return (
            0,
            "Multiprocessing CPU available; no unresolved eligible fixtures to process.",
        )

    worker_count = min(len(eligible_fixtures), os.cpu_count() or 1)
    if worker_count < 1:
        raise BenchmarkModeUnavailable("No local CPU worker is available.")

    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=worker_count,
        mp_context=context,
        initializer=_init_worker,
        initargs=(package_data.rows_by_file,),
    ) as executor:
        results = list(executor.map(_multiprocessing_predict_fixture, eligible_fixtures))

    skipped = [
        reason
        for prediction, reason in results
        if prediction is None and reason
    ]
    prediction_count = sum(1 for prediction, _reason in results if prediction is not None)
    notes = (
        f"Multiprocessing CPU baseline using {worker_count} worker process(es)."
    )
    if skipped:
        notes = f"{notes} Skipped fixtures: {' | '.join(skipped)}"
    return prediction_count, notes


def _run_torch_tensor_probe(
    package_data: PackageData,
    _eligible_fixtures: list[dict[str, str]],
    *,
    torch_probe: TorchProbe,
    device: str,
) -> tuple[int, str]:
    if not torch_probe.available or torch_probe.module is None:
        raise BenchmarkModeUnavailable(torch_probe.notes)
    if device == "cuda" and not torch_probe.cuda_available:
        raise BenchmarkModeUnavailable("CUDA is not available to PyTorch locally.")

    torch = torch_probe.module
    predictions, _eligible_count, warnings = generate_predictions(package_data)
    numeric_rows = [_prediction_numeric_row(row) for row in predictions]
    if numeric_rows:
        tensor = torch.tensor(numeric_rows, dtype=torch.float32, device=device)
    else:
        tensor = torch.empty((0, 5), dtype=torch.float32, device=device)
    if device == "cuda":
        torch.cuda.synchronize()
    _checksum = float(tensor.sum().item()) if tensor.numel() else 0.0
    if device == "cuda":
        torch.cuda.synchronize()

    notes = (
        f"Optional PyTorch {device.upper()} tensor probe over Phase 4.3 baseline "
        "numeric outputs; no model is trained or loaded."
    )
    if warnings:
        notes = f"{notes} Warnings: {' | '.join(warnings)}"
    return len(predictions), notes


def _sample_mode(
    *,
    mode: str,
    repeat_index: int,
    input_fixture_count: int,
    runner: Callable[[], tuple[int, str]],
    required: bool = False,
) -> BenchmarkSample:
    start_time = time.perf_counter()
    try:
        prediction_count, notes = runner()
    except BenchmarkModeUnavailable as exc:
        return BenchmarkSample(
            mode=mode,
            repeat_index=repeat_index,
            input_fixture_count=input_fixture_count,
            prediction_count=0,
            elapsed_ms=None,
            throughput_predictions_per_second=None,
            available=False,
            notes=str(exc),
        )
    except Exception as exc:
        if required:
            raise
        return BenchmarkSample(
            mode=mode,
            repeat_index=repeat_index,
            input_fixture_count=input_fixture_count,
            prediction_count=0,
            elapsed_ms=None,
            throughput_predictions_per_second=None,
            available=False,
            notes=f"Mode failed locally: {exc}",
        )

    elapsed = _elapsed_ms(start_time)
    return BenchmarkSample(
        mode=mode,
        repeat_index=repeat_index,
        input_fixture_count=input_fixture_count,
        prediction_count=prediction_count,
        elapsed_ms=elapsed,
        throughput_predictions_per_second=_throughput(prediction_count, elapsed),
        available=True,
        notes=notes,
    )


def _write_results_csv(path: Path, samples: list[BenchmarkSample]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=BENCHMARK_RESULT_HEADERS,
            lineterminator="\n",
        )
        writer.writeheader()
        for sample in samples:
            writer.writerow(
                {
                    "mode": sample.mode,
                    "repeat_index": sample.repeat_index,
                    "input_fixture_count": sample.input_fixture_count,
                    "prediction_count": sample.prediction_count,
                    "elapsed_ms": (
                        f"{sample.elapsed_ms:.3f}"
                        if sample.elapsed_ms is not None
                        else ""
                    ),
                    "throughput_predictions_per_second": (
                        f"{sample.throughput_predictions_per_second:.3f}"
                        if sample.throughput_predictions_per_second is not None
                        else ""
                    ),
                    "available": str(sample.available).lower(),
                    "notes": sample.notes.replace("\r", " ").replace("\n", " "),
                }
            )


def _write_json(path: Path, data: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _mode_summary(samples: list[BenchmarkSample]) -> dict[str, dict[str, object]]:
    summary: dict[str, dict[str, object]] = {}
    for mode in BENCHMARK_MODES:
        mode_samples = [sample for sample in samples if sample.mode == mode]
        available_samples = [
            sample for sample in mode_samples if sample.available and sample.elapsed_ms is not None
        ]
        elapsed_values = [sample.elapsed_ms for sample in available_samples]
        throughput_values = [
            sample.throughput_predictions_per_second
            for sample in available_samples
            if sample.throughput_predictions_per_second is not None
        ]
        notes = sorted({sample.notes for sample in mode_samples if sample.notes})
        summary[mode] = {
            "available": bool(available_samples),
            "attempted_repeats": len(mode_samples),
            "measured_repeats": len(available_samples),
            "avg_elapsed_ms": _round_float(mean(elapsed_values)) if elapsed_values else None,
            "min_elapsed_ms": _round_float(min(elapsed_values)) if elapsed_values else None,
            "max_elapsed_ms": _round_float(max(elapsed_values)) if elapsed_values else None,
            "avg_throughput_predictions_per_second": (
                _round_float(mean(throughput_values)) if throughput_values else None
            ),
            "prediction_count": (
                available_samples[-1].prediction_count if available_samples else 0
            ),
            "notes": notes,
        }
    return summary


def _fastest_mode(mode_summary: dict[str, dict[str, object]]) -> str | None:
    measured = [
        (mode, values["avg_elapsed_ms"])
        for mode, values in mode_summary.items()
        if values["available"] and values["avg_elapsed_ms"] is not None
    ]
    if not measured:
        return None
    return min(measured, key=lambda item: float(item[1]))[0]


def _speedup_vs_sequential(
    mode_summary: dict[str, dict[str, object]]
) -> dict[str, float | None]:
    sequential_elapsed = mode_summary.get("sequential_cpu", {}).get("avg_elapsed_ms")
    speedups: dict[str, float | None] = {}
    for mode, values in mode_summary.items():
        elapsed = values.get("avg_elapsed_ms")
        if (
            sequential_elapsed is None
            or elapsed is None
            or float(elapsed) <= 0
        ):
            speedups[mode] = None
            continue
        speedups[mode] = round(float(sequential_elapsed) / float(elapsed), 3)
    return speedups


def _warnings_from_samples(
    package_data: PackageData,
    samples: list[BenchmarkSample],
    eligible_fixture_count: int,
) -> list[str]:
    warnings = list(package_data.warnings)
    if eligible_fixture_count == 0:
        warnings.append(
            "No unresolved eligible fixtures were found; timing mainly reflects "
            "package parsing and empty prediction-loop overhead."
        )
    unavailable_modes = [
        sample.mode
        for sample in samples
        if not sample.available and sample.repeat_index == 1
    ]
    if unavailable_modes:
        warnings.append(
            "Unavailable benchmark mode(s): " + ", ".join(sorted(unavailable_modes))
        )
    return warnings


def run_benchmark(
    zip_path: str | Path,
    *,
    repeat_count: int = DEFAULT_REPEAT_COUNT,
    output_dir: str | Path | None = None,
) -> BenchmarkRunResult:
    if repeat_count < 1:
        raise BenchmarkError("repeat_count must be at least 1.")

    try:
        package_data = load_package(zip_path)
    except BaselineError as exc:
        raise BenchmarkError(str(exc)) from exc

    resolved_input_path = package_data.source_path.resolve()
    destination = (
        Path(output_dir).expanduser()
        if output_dir is not None
        else package_data.source_path.with_suffix("").with_name(
            f"{package_data.source_path.stem}_benchmark_output"
        )
    )
    destination.mkdir(parents=True, exist_ok=True)

    generated_at = _utc_now_iso()
    eligible_fixtures = eligible_prediction_fixture_rows(package_data)
    input_fixture_count = len(eligible_fixtures)
    torch_probe = _probe_torch()
    samples: list[BenchmarkSample] = []

    for repeat_index in range(1, repeat_count + 1):
        samples.append(
            _sample_mode(
                mode="sequential_cpu",
                repeat_index=repeat_index,
                input_fixture_count=input_fixture_count,
                runner=lambda: _run_sequential_cpu(package_data, eligible_fixtures),
                required=True,
            )
        )
        samples.append(
            _sample_mode(
                mode="multiprocessing_cpu",
                repeat_index=repeat_index,
                input_fixture_count=input_fixture_count,
                runner=lambda: _run_multiprocessing_cpu(package_data, eligible_fixtures),
            )
        )
        samples.append(
            _sample_mode(
                mode="torch_cpu_tensor",
                repeat_index=repeat_index,
                input_fixture_count=input_fixture_count,
                runner=lambda: _run_torch_tensor_probe(
                    package_data,
                    eligible_fixtures,
                    torch_probe=torch_probe,
                    device="cpu",
                ),
            )
        )
        samples.append(
            _sample_mode(
                mode="torch_cuda_tensor",
                repeat_index=repeat_index,
                input_fixture_count=input_fixture_count,
                runner=lambda: _run_torch_tensor_probe(
                    package_data,
                    eligible_fixtures,
                    torch_probe=torch_probe,
                    device="cuda",
                ),
            )
        )

    output_files = {
        "benchmark_results.csv": destination / "benchmark_results.csv",
        "benchmark_summary.json": destination / "benchmark_summary.json",
        "benchmark_manifest.json": destination / "benchmark_manifest.json",
    }
    summary_by_mode = _mode_summary(samples)
    warnings = _warnings_from_samples(package_data, samples, input_fixture_count)
    limitations = [
        "Local-only benchmark harness; Render does not run this script.",
        "Sequential CPU is the required reference mode.",
        "Multiprocessing CPU is a local process-pool comparison and may be slower on tiny packages because process startup dominates.",
        "PyTorch CPU/CUDA modes are optional tensor probes over baseline numeric outputs only; they do not train, load, or claim a model.",
        "Timing small datasets can be noisy and should not be treated as stable HPC evidence without larger repeated packages.",
        "The harness uses only the downloaded ZIP package and does not read Django settings or the database.",
    ]
    summary = {
        "generated_at": generated_at,
        "source_package": str(resolved_input_path),
        "repeat_count": repeat_count,
        "fastest_mode": _fastest_mode(summary_by_mode),
        "mode_summary": summary_by_mode,
        "speedup_vs_sequential": _speedup_vs_sequential(summary_by_mode),
        "cuda_available": torch_probe.cuda_available,
        "torch_available": torch_probe.available,
        "limitations": limitations,
        "warnings": warnings,
    }
    manifest = {
        "script_name": SCRIPT_NAME,
        "local_only": True,
        "render_required": False,
        "input_package": str(resolved_input_path),
        "output_files": {
            filename: str(path.resolve())
            for filename, path in output_files.items()
        },
        "benchmark_modes_attempted": BENCHMARK_MODES,
        "formula_pipeline_version": {
            "baseline_formula_version": FORMULA_VERSION,
            "benchmark_version": BENCHMARK_VERSION,
            "package_version": package_data.manifest.get("package_version"),
            "schema_version": package_data.manifest.get("schema_version"),
        },
        "repeat_count": repeat_count,
        "generated_at": generated_at,
    }

    _write_results_csv(output_files["benchmark_results.csv"], samples)
    _write_json(output_files["benchmark_summary.json"], summary)
    _write_json(output_files["benchmark_manifest.json"], manifest)

    return BenchmarkRunResult(
        output_dir=destination,
        output_files=output_files,
        samples=samples,
        summary=summary,
        manifest=manifest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark local Hosted by Tanvir prediction processing from a "
            "Phase 4.2 prediction dataset ZIP."
        )
    )
    parser.add_argument("zip_path", help="Path to prediction-dataset ZIP package")
    parser.add_argument(
        "-r",
        "--repeat-count",
        "--repeats",
        type=int,
        default=DEFAULT_REPEAT_COUNT,
        help=f"Number of repeats per mode. Defaults to {DEFAULT_REPEAT_COUNT}.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        help=(
            "Directory for benchmark_results.csv, benchmark_summary.json, and "
            "benchmark_manifest.json. Defaults beside the ZIP."
        ),
    )
    args = parser.parse_args(argv)

    try:
        result = run_benchmark(
            args.zip_path,
            repeat_count=args.repeat_count,
            output_dir=args.output_dir,
        )
    except BenchmarkError as exc:
        print(f"Benchmark run failed: {exc}", file=sys.stderr)
        return 1

    print("Prediction benchmark run complete")
    print(f"Output directory: {result.output_dir}")
    print(f"Results CSV: {result.output_files['benchmark_results.csv']}")
    print(f"Summary JSON: {result.output_files['benchmark_summary.json']}")
    print(f"Manifest JSON: {result.output_files['benchmark_manifest.json']}")
    print(f"Fastest mode: {result.summary['fastest_mode']}")
    print(f"PyTorch available: {str(result.summary['torch_available']).lower()}")
    print(f"CUDA available: {str(result.summary['cuda_available']).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
