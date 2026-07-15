import json
import zipfile
from io import BytesIO
from unittest import TestCase

from local_tools.inspect_prediction_package import REQUIRED_CSV_FILES, inspect_zip


def build_package_bytes(*, row_count_override=None, extra_csv_header=None):
    files = {}
    manifest_files = {}
    row_counts = {}

    for filename in REQUIRED_CSV_FILES:
        headers = extra_csv_header if filename == "approved_results.csv" and extra_csv_header else ["id", "name"]
        files[filename] = ",".join(headers) + "\n1,Demo Row\n"
        manifest_files[filename] = {
            "rows": 1,
            "columns": headers,
        }
        row_counts[filename] = 1

    if row_count_override is not None:
        row_counts["approved_results.csv"] = row_count_override
        manifest_files["approved_results.csv"]["rows"] = row_count_override

    manifest = {
        "row_counts": row_counts,
        "files": manifest_files,
        "known_limitations": ["Zero-stat appearances can be missing."],
        "source_rules": ["Only approved results are exported."],
    }

    dictionary_lines = ["# Data Dictionary", ""]
    for filename in REQUIRED_CSV_FILES:
        dictionary_lines.extend([f"## {filename}", ""])
        for column in manifest_files[filename]["columns"]:
            dictionary_lines.append(f"- `{column}`: Demo column.")
        dictionary_lines.append("")

    files["manifest.json"] = json.dumps(manifest)
    files["data_dictionary.md"] = "\n".join(dictionary_lines)

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for filename, content in files.items():
            package.writestr(filename, content)
    return buffer.getvalue()


class PredictionPackageInspectorTests(TestCase):
    def test_valid_package_passes(self):
        package_bytes = build_package_bytes()

        result = inspect_zip(package_bytes)

        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])
        self.assertEqual(
            {summary.filename for summary in result.csv_summaries},
            set(REQUIRED_CSV_FILES),
        )

    def test_row_count_mismatch_fails(self):
        package_bytes = build_package_bytes(row_count_override=99)

        result = inspect_zip(package_bytes)

        self.assertFalse(result.ok)
        self.assertIn("approved_results.csv row count mismatch", " ".join(result.errors))

    def test_sensitive_header_fails(self):
        package_bytes = build_package_bytes(extra_csv_header=["id", "email"])

        result = inspect_zip(package_bytes)

        self.assertFalse(result.ok)
        self.assertIn("sensitive-looking column", " ".join(result.errors))
