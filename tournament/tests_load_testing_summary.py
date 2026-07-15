import csv
import json
import shutil
import uuid
from pathlib import Path
from unittest import TestCase

from local_tools.load_testing.summarize_jmeter_results import (
    SUMMARY_CSV_HEADERS,
    LoadTestSummaryError,
    summarize_jmeter_results,
)


class JMeterSummaryHelperTests(TestCase):
    def make_workspace(self):
        base_path = Path.cwd() / f"phase45_load_test_{uuid.uuid4().hex}"
        base_path.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(base_path, ignore_errors=True))
        return base_path

    def write_jmeter_csv(self, base_path, rows):
        csv_path = base_path / "raw.jtl"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "timeStamp",
                    "elapsed",
                    "label",
                    "responseCode",
                    "responseMessage",
                    "success",
                    "URL",
                    "failureMessage",
                ],
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        return csv_path

    def test_summary_outputs_are_created_with_percentiles_and_throughput(self):
        base_path = self.make_workspace()
        csv_path = self.write_jmeter_csv(
            base_path,
            [
                {
                    "timeStamp": "1000",
                    "elapsed": "100",
                    "label": "GET homepage",
                    "responseCode": "200",
                    "responseMessage": "OK",
                    "success": "true",
                    "URL": "http://127.0.0.1:8000/",
                    "failureMessage": "",
                },
                {
                    "timeStamp": "1100",
                    "elapsed": "200",
                    "label": "GET homepage",
                    "responseCode": "200",
                    "responseMessage": "OK",
                    "success": "true",
                    "URL": "http://127.0.0.1:8000/",
                    "failureMessage": "",
                },
                {
                    "timeStamp": "1400",
                    "elapsed": "500",
                    "label": "GET fixture detail",
                    "responseCode": "500",
                    "responseMessage": "Server Error",
                    "success": "false",
                    "URL": "http://127.0.0.1:8000/fixture/1/?token=secret",
                    "failureMessage": "sensitive failure details",
                },
            ],
        )

        result = summarize_jmeter_results(csv_path, output_dir=base_path / "summary")

        self.assertTrue(result.output_files["jmeter_summary.csv"].exists())
        self.assertTrue(result.output_files["jmeter_summary.json"].exists())
        self.assertTrue(result.output_files["jmeter_summary.md"].exists())

        summary = json.loads(result.output_files["jmeter_summary.json"].read_text())
        self.assertEqual(summary["overall"]["request_count"], 3)
        self.assertEqual(summary["overall"]["failure_count"], 1)
        self.assertEqual(summary["overall"]["error_rate_percent"], 33.333)
        self.assertEqual(summary["overall"]["p95_elapsed_ms"], 500.0)
        self.assertIsNotNone(summary["overall"]["throughput_requests_per_second"])
        self.assertEqual(summary["response_codes"]["200"], 2)
        self.assertEqual(summary["response_codes"]["500"], 1)
        self.assertIn("GET homepage", summary["by_label"])

    def test_summary_csv_uses_stable_headers(self):
        base_path = self.make_workspace()
        csv_path = self.write_jmeter_csv(
            base_path,
            [
                {
                    "timeStamp": "1000",
                    "elapsed": "100",
                    "label": "GET tournament list",
                    "responseCode": "200",
                    "responseMessage": "OK",
                    "success": "true",
                    "URL": "http://127.0.0.1:8000/tournaments/",
                    "failureMessage": "",
                },
            ],
        )

        result = summarize_jmeter_results(csv_path, output_dir=base_path / "summary")

        with result.output_files["jmeter_summary.csv"].open(newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, SUMMARY_CSV_HEADERS)
            rows = list(reader)

        self.assertEqual(rows[0]["label"], "__overall__")
        self.assertEqual(rows[1]["label"], "GET tournament list")

    def test_generated_summaries_omit_raw_urls_and_failure_messages(self):
        base_path = self.make_workspace()
        csv_path = self.write_jmeter_csv(
            base_path,
            [
                {
                    "timeStamp": "1000",
                    "elapsed": "100",
                    "label": "GET fixture detail",
                    "responseCode": "500",
                    "responseMessage": "Server Error",
                    "success": "false",
                    "URL": "https://example.com/fixture/1/?session=secret-token",
                    "failureMessage": "secret failure body",
                },
            ],
        )

        result = summarize_jmeter_results(csv_path, output_dir=base_path / "summary")
        combined_output = "\n".join(
            path.read_text()
            for path in result.output_files.values()
        )

        self.assertNotIn("secret-token", combined_output)
        self.assertNotIn("secret failure body", combined_output)
        self.assertNotIn("https://example.com", combined_output)

    def test_missing_elapsed_column_fails_clearly(self):
        base_path = self.make_workspace()
        csv_path = base_path / "bad.jtl"
        csv_path.write_text("label,success\nGET homepage,true\n", encoding="utf-8")

        with self.assertRaises(LoadTestSummaryError) as context:
            summarize_jmeter_results(csv_path, output_dir=base_path / "summary")

        self.assertIn("elapsed", str(context.exception))
