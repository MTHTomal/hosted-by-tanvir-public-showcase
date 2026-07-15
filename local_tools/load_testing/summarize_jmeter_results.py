#!/usr/bin/env python
"""Summarize local JMeter CSV/JTL output without importing Django.

The helper intentionally omits raw URLs, cookies, headers, request bodies, and
failure messages from generated summaries. Keep raw JMeter files local and
review them before sharing any evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


SCRIPT_NAME = "local_tools/load_testing/summarize_jmeter_results.py"
SUMMARY_VERSION = "phase-4.5-jmeter-summary-v1"

SUMMARY_CSV_HEADERS = [
    "label",
    "request_count",
    "success_count",
    "failure_count",
    "error_rate_percent",
    "avg_elapsed_ms",
    "min_elapsed_ms",
    "max_elapsed_ms",
    "p50_elapsed_ms",
    "p90_elapsed_ms",
    "p95_elapsed_ms",
    "p99_elapsed_ms",
    "throughput_requests_per_second",
]


class LoadTestSummaryError(Exception):
    """Raised when a JMeter result file cannot be summarized safely."""


@dataclass(frozen=True)
class JMeterSample:
    label: str
    elapsed_ms: float
    success: bool
    response_code: str
    start_timestamp_ms: float | None


@dataclass(frozen=True)
class SummaryOutput:
    output_dir: Path
    output_files: dict[str, Path]
    summary: dict[str, object]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _field(row: dict[str, str], normalized_headers: dict[str, str], *names: str) -> str:
    for name in names:
        actual_name = normalized_headers.get(name.lower())
        if actual_name is not None:
            return row.get(actual_name, "")
    return ""


def _parse_float(value: str, *, field_name: str, row_number: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise LoadTestSummaryError(
            f"Row {row_number} has invalid {field_name!r} value: {value!r}."
        ) from exc


def _parse_success(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _round_float(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100) * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def _duration_seconds(samples: list[JMeterSample]) -> float | None:
    timestamped = [
        sample for sample in samples if sample.start_timestamp_ms is not None
    ]
    if not timestamped:
        return None
    start_ms = min(float(sample.start_timestamp_ms) for sample in timestamped)
    end_ms = max(
        float(sample.start_timestamp_ms) + sample.elapsed_ms
        for sample in timestamped
    )
    duration = (end_ms - start_ms) / 1000
    return duration if duration > 0 else None


def _throughput(samples: list[JMeterSample]) -> float | None:
    duration = _duration_seconds(samples)
    if duration is None:
        return None
    return len(samples) / duration


def read_jmeter_csv(path: str | Path) -> list[JMeterSample]:
    source = Path(path).expanduser()
    if not source.exists():
        raise LoadTestSummaryError(f"JMeter result file does not exist: {source}")

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise LoadTestSummaryError("JMeter result file has no header row.")

        normalized_headers = {
            header.strip().lower(): header
            for header in reader.fieldnames
            if header is not None
        }
        if "elapsed" not in normalized_headers:
            raise LoadTestSummaryError(
                "JMeter result file must include an 'elapsed' column."
            )

        samples: list[JMeterSample] = []
        for row_number, row in enumerate(reader, start=2):
            elapsed = _parse_float(
                _field(row, normalized_headers, "elapsed"),
                field_name="elapsed",
                row_number=row_number,
            )
            timestamp_value = _field(row, normalized_headers, "timestamp", "timeStamp")
            timestamp_ms = (
                _parse_float(
                    timestamp_value,
                    field_name="timeStamp",
                    row_number=row_number,
                )
                if timestamp_value
                else None
            )
            samples.append(
                JMeterSample(
                    label=(
                        _field(row, normalized_headers, "label").strip()
                        or "unlabeled"
                    ),
                    elapsed_ms=elapsed,
                    success=_parse_success(
                        _field(row, normalized_headers, "success") or "false"
                    ),
                    response_code=(
                        _field(row, normalized_headers, "responseCode").strip()
                        or "unknown"
                    ),
                    start_timestamp_ms=timestamp_ms,
                )
            )

    if not samples:
        raise LoadTestSummaryError("JMeter result file has no sample rows.")
    return samples


def _summarize_samples(samples: list[JMeterSample]) -> dict[str, object]:
    elapsed_values = [sample.elapsed_ms for sample in samples]
    success_count = sum(1 for sample in samples if sample.success)
    failure_count = len(samples) - success_count
    throughput = _throughput(samples)
    return {
        "request_count": len(samples),
        "success_count": success_count,
        "failure_count": failure_count,
        "error_rate_percent": _round_float((failure_count / len(samples)) * 100),
        "avg_elapsed_ms": _round_float(mean(elapsed_values)),
        "min_elapsed_ms": _round_float(min(elapsed_values)),
        "max_elapsed_ms": _round_float(max(elapsed_values)),
        "p50_elapsed_ms": _round_float(_percentile(elapsed_values, 50)),
        "p90_elapsed_ms": _round_float(_percentile(elapsed_values, 90)),
        "p95_elapsed_ms": _round_float(_percentile(elapsed_values, 95)),
        "p99_elapsed_ms": _round_float(_percentile(elapsed_values, 99)),
        "duration_seconds": _round_float(_duration_seconds(samples)),
        "throughput_requests_per_second": _round_float(throughput),
    }


def _group_by_label(samples: list[JMeterSample]) -> dict[str, list[JMeterSample]]:
    grouped: dict[str, list[JMeterSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.label, []).append(sample)
    return grouped


def build_summary(samples: list[JMeterSample], *, source_csv: Path) -> dict[str, object]:
    response_codes: dict[str, int] = {}
    for sample in samples:
        response_codes[sample.response_code] = response_codes.get(sample.response_code, 0) + 1

    by_label = {
        label: _summarize_samples(label_samples)
        for label, label_samples in sorted(_group_by_label(samples).items())
    }
    overall = _summarize_samples(samples)
    warnings = [
        "Summaries intentionally omit raw URLs, cookies, request headers, and failure messages.",
        "Keep raw JMeter JTL/CSV files local unless they have been reviewed for sensitive data.",
    ]
    if overall["failure_count"]:
        warnings.append("One or more samples failed; inspect the raw local JMeter file.")
    if overall["throughput_requests_per_second"] is None:
        warnings.append(
            "No usable timeStamp column was found; throughput could not be calculated."
        )

    return {
        "generated_at": _utc_now_iso(),
        "summary_version": SUMMARY_VERSION,
        "script_name": SCRIPT_NAME,
        "source_csv": str(source_csv.resolve()),
        "overall": overall,
        "by_label": by_label,
        "response_codes": dict(sorted(response_codes.items())),
        "warnings": warnings,
    }


def _csv_summary_rows(summary: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    overall = summary["overall"]
    assert isinstance(overall, dict)
    rows.append({"label": "__overall__", **overall})
    by_label = summary["by_label"]
    assert isinstance(by_label, dict)
    for label, label_summary in by_label.items():
        assert isinstance(label_summary, dict)
        rows.append({"label": label, **label_summary})
    return rows


def _write_summary_csv(path: Path, summary: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=SUMMARY_CSV_HEADERS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in _csv_summary_rows(summary):
            writer.writerow(row)


def _write_summary_json(path: Path, summary: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _value(value: object) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _write_summary_markdown(path: Path, summary: dict[str, object]) -> None:
    overall = summary["overall"]
    assert isinstance(overall, dict)
    by_label = summary["by_label"]
    assert isinstance(by_label, dict)
    response_codes = summary["response_codes"]
    assert isinstance(response_codes, dict)
    warnings = summary["warnings"]
    assert isinstance(warnings, list)

    lines = [
        "# JMeter Load Test Summary",
        "",
        f"Generated at: `{summary['generated_at']}`",
        f"Source CSV/JTL: `{summary['source_csv']}`",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Requests | {_value(overall['request_count'])} |",
        f"| Successes | {_value(overall['success_count'])} |",
        f"| Failures | {_value(overall['failure_count'])} |",
        f"| Error rate | {_value(overall['error_rate_percent'])}% |",
        f"| Average latency | {_value(overall['avg_elapsed_ms'])} ms |",
        f"| p50 latency | {_value(overall['p50_elapsed_ms'])} ms |",
        f"| p90 latency | {_value(overall['p90_elapsed_ms'])} ms |",
        f"| p95 latency | {_value(overall['p95_elapsed_ms'])} ms |",
        f"| p99 latency | {_value(overall['p99_elapsed_ms'])} ms |",
        f"| Throughput | {_value(overall['throughput_requests_per_second'])} req/s |",
        "",
        "## By Label",
        "",
        "| Label | Requests | Error % | Avg ms | p95 ms | Throughput req/s |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]

    for label, label_summary in by_label.items():
        assert isinstance(label_summary, dict)
        lines.append(
            "| {label} | {requests} | {error} | {avg} | {p95} | {throughput} |".format(
                label=label.replace("|", "\\|"),
                requests=_value(label_summary["request_count"]),
                error=_value(label_summary["error_rate_percent"]),
                avg=_value(label_summary["avg_elapsed_ms"]),
                p95=_value(label_summary["p95_elapsed_ms"]),
                throughput=_value(label_summary["throughput_requests_per_second"]),
            )
        )

    lines.extend(
        [
            "",
            "## Response Codes",
            "",
            "| Code | Count |",
            "| --- | ---: |",
        ]
    )
    for code, count in response_codes.items():
        lines.append(f"| {code} | {count} |")

    lines.extend(["", "## Notes", ""])
    for warning in warnings:
        lines.append(f"- {warning}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_jmeter_results(
    input_csv: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> SummaryOutput:
    source = Path(input_csv).expanduser()
    samples = read_jmeter_csv(source)
    destination = Path(output_dir).expanduser() if output_dir else source.parent
    destination.mkdir(parents=True, exist_ok=True)

    summary = build_summary(samples, source_csv=source)
    output_files = {
        "jmeter_summary.csv": destination / "jmeter_summary.csv",
        "jmeter_summary.json": destination / "jmeter_summary.json",
        "jmeter_summary.md": destination / "jmeter_summary.md",
    }
    _write_summary_csv(output_files["jmeter_summary.csv"], summary)
    _write_summary_json(output_files["jmeter_summary.json"], summary)
    _write_summary_markdown(output_files["jmeter_summary.md"], summary)

    return SummaryOutput(
        output_dir=destination,
        output_files=output_files,
        summary=summary,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize local JMeter CSV/JTL output for Hosted by Tanvir."
    )
    parser.add_argument("input_csv", help="Path to a JMeter CSV/JTL result file")
    parser.add_argument(
        "-o",
        "--output-dir",
        help=(
            "Directory for jmeter_summary.csv, jmeter_summary.json, and "
            "jmeter_summary.md. Defaults beside the input file."
        ),
    )
    args = parser.parse_args(argv)

    try:
        result = summarize_jmeter_results(
            args.input_csv,
            output_dir=args.output_dir,
        )
    except LoadTestSummaryError as exc:
        print(f"JMeter summary failed: {exc}", file=sys.stderr)
        return 1

    print("JMeter summary complete")
    print(f"Output directory: {result.output_dir}")
    print(f"Summary CSV: {result.output_files['jmeter_summary.csv']}")
    print(f"Summary JSON: {result.output_files['jmeter_summary.json']}")
    print(f"Summary Markdown: {result.output_files['jmeter_summary.md']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
