#!/usr/bin/env python
"""Inspect a downloaded Phase 4 prediction dataset package.

This script is intentionally local-only. It does not import Django, read the
database, or require application settings.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable


REQUIRED_CSV_FILES = [
    "approved_results.csv",
    "fixtures.csv",
    "tournaments.csv",
    "official_player_stats.csv",
    "team_standings_history.csv",
    "head_to_head.csv",
]
REQUIRED_FILES = [*REQUIRED_CSV_FILES, "manifest.json", "data_dictionary.md"]
SENSITIVE_HEADER_TOKENS = {
    "email",
    "password",
    "is_staff",
    "is_superuser",
    "permission",
    "user_permissions",
    "groups",
    "last_login",
    "session",
}
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@dataclass(frozen=True)
class CsvSummary:
    filename: str
    header: list[str]
    rows: int


@dataclass(frozen=True)
class InspectionResult:
    files: list[str]
    csv_summaries: list[CsvSummary]
    errors: list[str]
    warnings: list[str]
    manifest: dict

    @property
    def ok(self) -> bool:
        return not self.errors


def _read_text(package: zipfile.ZipFile, filename: str) -> str:
    return package.read(filename).decode("utf-8-sig")


def _parse_dictionary_columns(dictionary_text: str) -> dict[str, list[str]]:
    columns_by_file: dict[str, list[str]] = {}
    current_file = None
    for raw_line in dictionary_text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_file = line[3:].strip()
            columns_by_file.setdefault(current_file, [])
            continue
        if current_file and line.startswith("- `"):
            match = re.match(r"- `([^`]+)`:", line)
            if match:
                columns_by_file[current_file].append(match.group(1))
    return columns_by_file


def _csv_summary(filename: str, text: str) -> CsvSummary:
    reader = csv.reader(text.splitlines())
    try:
        header = next(reader)
    except StopIteration:
        return CsvSummary(filename=filename, header=[], rows=0)
    return CsvSummary(filename=filename, header=header, rows=sum(1 for _row in reader))


def _sensitive_headers(headers: Iterable[str]) -> list[str]:
    matches = []
    for header in headers:
        normalized = header.strip().lower()
        if normalized in SENSITIVE_HEADER_TOKENS or any(
            token in normalized for token in ("password", "email")
        ):
            matches.append(header)
    return matches


def _inspect_open_package(package: zipfile.ZipFile) -> InspectionResult:
    errors: list[str] = []
    warnings: list[str] = []
    manifest: dict = {}
    summaries: list[CsvSummary] = []

    files = sorted(name for name in package.namelist() if not name.endswith("/"))
    missing = [filename for filename in REQUIRED_FILES if filename not in files]
    if missing:
        errors.append(f"Missing required file(s): {', '.join(missing)}")

    if "manifest.json" in files:
        try:
            manifest = json.loads(_read_text(package, "manifest.json"))
        except json.JSONDecodeError as exc:
            errors.append(f"manifest.json is not valid JSON: {exc}")
            manifest = {}

    dictionary_columns = {}
    if "data_dictionary.md" in files:
        dictionary_columns = _parse_dictionary_columns(
            _read_text(package, "data_dictionary.md")
        )

    row_counts = manifest.get("row_counts", {}) if isinstance(manifest, dict) else {}
    manifest_files = manifest.get("files", {}) if isinstance(manifest, dict) else {}

    for filename in REQUIRED_CSV_FILES:
        if filename not in files:
            continue
        text = _read_text(package, filename)
        summary = _csv_summary(filename, text)
        summaries.append(summary)

        if not summary.header:
            errors.append(f"{filename} has no header row")
            continue
        if summary.rows == 0:
            warnings.append(f"{filename} has a header but no data rows")

        sensitive_headers = _sensitive_headers(summary.header)
        if sensitive_headers:
            errors.append(
                f"{filename} exposes sensitive-looking column(s): "
                f"{', '.join(sensitive_headers)}"
            )

        if EMAIL_RE.search(text):
            errors.append(f"{filename} appears to contain an email address")

        if filename in row_counts and row_counts[filename] != summary.rows:
            errors.append(
                f"{filename} row count mismatch: manifest says "
                f"{row_counts[filename]}, actual is {summary.rows}"
            )

        manifest_columns = manifest_files.get(filename, {}).get("columns")
        if manifest_columns is not None and manifest_columns != summary.header:
            errors.append(f"{filename} header does not match manifest columns")

        dictionary_file_columns = dictionary_columns.get(filename)
        if dictionary_file_columns is not None and dictionary_file_columns != summary.header:
            errors.append(f"{filename} header does not match data_dictionary.md")

    for filename in REQUIRED_CSV_FILES:
        if filename in files and filename not in row_counts:
            errors.append(f"manifest.json row_counts is missing {filename}")
        if filename in files and filename not in manifest_files:
            errors.append(f"manifest.json files metadata is missing {filename}")
        if filename in files and filename not in dictionary_columns:
            errors.append(f"data_dictionary.md is missing a section for {filename}")

    if not manifest.get("known_limitations"):
        errors.append("manifest.json must list known_limitations")
    if not manifest.get("source_rules"):
        errors.append("manifest.json must list source_rules")

    return InspectionResult(
        files=files,
        csv_summaries=summaries,
        errors=errors,
        warnings=warnings,
        manifest=manifest,
    )


def inspect_zip(zip_path: str | Path | bytes) -> InspectionResult:
    if isinstance(zip_path, bytes):
        try:
            with zipfile.ZipFile(BytesIO(zip_path)) as package:
                return _inspect_open_package(package)
        except zipfile.BadZipFile:
            return InspectionResult(
                files=[],
                csv_summaries=[],
                errors=["Not a valid ZIP package"],
                warnings=[],
                manifest={},
            )

    path = Path(zip_path)
    if not path.exists():
        return InspectionResult(
            files=[],
            csv_summaries=[],
            errors=[f"Package does not exist: {path}"],
            warnings=[],
            manifest={},
        )

    try:
        with zipfile.ZipFile(path) as package:
            return _inspect_open_package(package)
    except zipfile.BadZipFile:
        return InspectionResult(
            files=[],
            csv_summaries=[],
            errors=[f"Not a valid ZIP package: {path}"],
            warnings=[],
            manifest={},
        )


def _print_result(result: InspectionResult) -> None:
    print("Prediction package inspection")
    print("")
    print("Included files:")
    for filename in result.files:
        print(f"- {filename}")

    print("")
    print("CSV summary:")
    print(f"{'file':32} {'rows':>8} columns")
    print(f"{'-' * 32} {'-' * 8} {'-' * 7}")
    for summary in result.csv_summaries:
        print(f"{summary.filename:32} {summary.rows:8} {len(summary.header)}")

    if result.warnings:
        print("")
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")

    if result.errors:
        print("")
        print("Errors:")
        for error in result.errors:
            print(f"- {error}")

    print("")
    print("Result:", "PASS" if result.ok else "FAIL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a downloaded Hosted by Tanvir prediction dataset ZIP."
    )
    parser.add_argument("zip_path", help="Path to prediction-dataset ZIP package")
    args = parser.parse_args(argv)

    result = inspect_zip(args.zip_path)
    _print_result(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
