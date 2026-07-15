import csv
import json
import tempfile
import zipfile
from io import BytesIO, StringIO
from pathlib import Path
from unittest import TestCase

from local_tools.run_prediction_baseline import run_baseline


CSV_HEADERS = {
    "approved_results.csv": [
        "result_id",
        "tournament_id",
        "tournament_name",
        "tournament_status",
        "fixture_id",
        "fixture_round",
        "fixture_stage",
        "fixture_group_label",
        "fixture_date",
        "home_team_id",
        "home_team_name",
        "away_team_id",
        "away_team_name",
        "home_score",
        "away_score",
        "winner_team_id",
        "winner_team_name",
        "is_draw",
        "approved_at",
        "created_at",
    ],
    "fixtures.csv": [
        "fixture_id",
        "tournament_id",
        "tournament_name",
        "tournament_status",
        "tournament_type",
        "tournament_format",
        "fixture_round",
        "fixture_stage",
        "fixture_group_label",
        "fixture_date",
        "submission_deadline",
        "home_team_id",
        "home_team_name",
        "away_team_id",
        "away_team_name",
        "is_bye",
        "approved_result_id",
        "approved_result_reviewed_at",
    ],
    "tournaments.csv": [
        "tournament_id",
        "tournament_name",
        "tournament_status",
        "tournament_type",
        "tournament_format",
        "max_teams",
        "registration_deadline",
        "start_date",
        "end_date",
        "hybrid_qualifiers_per_group",
        "tiebreaker_rules",
        "created_at",
        "updated_at",
        "approved_result_count",
        "fixture_count_with_approved_results",
        "official_player_stat_row_count",
        "standing_row_count",
    ],
    "official_player_stats.csv": [
        "stat_id",
        "tournament_id",
        "tournament_name",
        "tournament_status",
        "fixture_id",
        "fixture_date",
        "player_id",
        "player_username",
        "player_ign",
        "team_id",
        "team_name",
        "goals",
        "own_goals",
        "assists",
        "yellow_cards",
        "red_cards",
        "man_of_the_match",
        "appearance_count",
    ],
    "team_standings_history.csv": [
        "standing_id",
        "tournament_id",
        "tournament_name",
        "tournament_status",
        "team_id",
        "team_name",
        "played",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
        "goal_difference",
        "points",
        "win_rate",
        "last_updated",
    ],
    "head_to_head.csv": [
        "result_id",
        "tournament_id",
        "tournament_name",
        "tournament_status",
        "fixture_id",
        "fixture_date",
        "team_a_id",
        "team_a_name",
        "team_b_id",
        "team_b_name",
        "team_a_goals",
        "team_b_goals",
        "winner_team_id",
        "winner_team_name",
        "is_draw",
    ],
}


def csv_text(filename, rows):
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=CSV_HEADERS[filename],
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({
            header: row.get(header, "")
            for header in CSV_HEADERS[filename]
        })
    return output.getvalue()


def build_package_bytes(*, include_unresolved=True, sensitive_player_username=False):
    approved_rows = [
        {
            "result_id": "101",
            "tournament_id": "1",
            "tournament_name": "Baseline Cup",
            "tournament_status": "active",
            "fixture_id": "1",
            "fixture_round": "1",
            "fixture_stage": "group",
            "fixture_date": "2026-05-01T12:00:00+00:00",
            "home_team_id": "10",
            "home_team_name": "Falcons",
            "away_team_id": "30",
            "away_team_name": "Comets",
            "home_score": "3",
            "away_score": "1",
            "winner_team_id": "10",
            "winner_team_name": "Falcons",
            "is_draw": "False",
            "approved_at": "2026-05-01T15:00:00+00:00",
            "created_at": "2026-05-01T14:00:00+00:00",
        },
        {
            "result_id": "102",
            "tournament_id": "1",
            "tournament_name": "Baseline Cup",
            "tournament_status": "active",
            "fixture_id": "2",
            "fixture_round": "2",
            "fixture_stage": "group",
            "fixture_date": "2026-05-02T12:00:00+00:00",
            "home_team_id": "20",
            "home_team_name": "Rovers",
            "away_team_id": "30",
            "away_team_name": "Comets",
            "home_score": "2",
            "away_score": "0",
            "winner_team_id": "20",
            "winner_team_name": "Rovers",
            "is_draw": "False",
            "approved_at": "2026-05-02T15:00:00+00:00",
            "created_at": "2026-05-02T14:00:00+00:00",
        },
    ]
    fixture_rows = [
        {
            "fixture_id": "1",
            "tournament_id": "1",
            "tournament_name": "Baseline Cup",
            "tournament_status": "active",
            "tournament_type": "team",
            "tournament_format": "round_robin",
            "fixture_round": "1",
            "fixture_stage": "group",
            "fixture_date": "2026-05-01T12:00:00+00:00",
            "home_team_id": "10",
            "home_team_name": "Falcons",
            "away_team_id": "30",
            "away_team_name": "Comets",
            "is_bye": "False",
            "approved_result_id": "101",
            "approved_result_reviewed_at": "2026-05-01T15:00:00+00:00",
        },
        {
            "fixture_id": "2",
            "tournament_id": "1",
            "tournament_name": "Baseline Cup",
            "tournament_status": "active",
            "tournament_type": "team",
            "tournament_format": "round_robin",
            "fixture_round": "2",
            "fixture_stage": "group",
            "fixture_date": "2026-05-02T12:00:00+00:00",
            "home_team_id": "20",
            "home_team_name": "Rovers",
            "away_team_id": "30",
            "away_team_name": "Comets",
            "is_bye": "False",
            "approved_result_id": "102",
            "approved_result_reviewed_at": "2026-05-02T15:00:00+00:00",
        },
    ]
    if include_unresolved:
        fixture_rows.append(
            {
                "fixture_id": "3",
                "tournament_id": "1",
                "tournament_name": "Baseline Cup",
                "tournament_status": "active",
                "tournament_type": "team",
                "tournament_format": "round_robin",
                "fixture_round": "3",
                "fixture_stage": "group",
                "fixture_date": "2026-05-03T12:00:00+00:00",
                "home_team_id": "10",
                "home_team_name": "Falcons",
                "away_team_id": "20",
                "away_team_name": "Rovers",
                "is_bye": "False",
                "approved_result_id": "",
            }
        )

    player_username = "secret@example.com" if sensitive_player_username else "striker"
    rows_by_file = {
        "approved_results.csv": approved_rows,
        "fixtures.csv": fixture_rows,
        "tournaments.csv": [
            {
                "tournament_id": "1",
                "tournament_name": "Baseline Cup",
                "tournament_status": "active",
                "tournament_type": "team",
                "tournament_format": "round_robin",
                "max_teams": "4",
                "tiebreaker_rules": "[]",
                "approved_result_count": "2",
                "fixture_count_with_approved_results": "2",
                "official_player_stat_row_count": "2",
                "standing_row_count": "3",
            }
        ],
        "official_player_stats.csv": [
            {
                "stat_id": "201",
                "tournament_id": "1",
                "tournament_name": "Baseline Cup",
                "tournament_status": "active",
                "fixture_id": "1",
                "fixture_date": "2026-05-01T12:00:00+00:00",
                "player_id": "1001",
                "player_username": player_username,
                "player_ign": "NoLeak",
                "team_id": "10",
                "team_name": "Falcons",
                "goals": "2",
                "own_goals": "0",
                "assists": "1",
                "appearance_count": "1",
            },
            {
                "stat_id": "202",
                "tournament_id": "1",
                "tournament_name": "Baseline Cup",
                "tournament_status": "active",
                "fixture_id": "2",
                "fixture_date": "2026-05-02T12:00:00+00:00",
                "player_id": "1002",
                "player_username": "rover",
                "player_ign": "Rover",
                "team_id": "20",
                "team_name": "Rovers",
                "goals": "1",
                "own_goals": "0",
                "assists": "2",
                "appearance_count": "1",
            },
        ],
        "team_standings_history.csv": [
            {
                "standing_id": "301",
                "tournament_id": "1",
                "tournament_name": "Baseline Cup",
                "tournament_status": "active",
                "team_id": "10",
                "team_name": "Falcons",
                "played": "1",
                "wins": "1",
                "draws": "0",
                "losses": "0",
                "goals_for": "3",
                "goals_against": "1",
                "goal_difference": "2",
                "points": "3",
                "win_rate": "100.0",
            },
            {
                "standing_id": "302",
                "tournament_id": "1",
                "tournament_name": "Baseline Cup",
                "tournament_status": "active",
                "team_id": "20",
                "team_name": "Rovers",
                "played": "1",
                "wins": "1",
                "draws": "0",
                "losses": "0",
                "goals_for": "2",
                "goals_against": "0",
                "goal_difference": "2",
                "points": "3",
                "win_rate": "100.0",
            },
            {
                "standing_id": "303",
                "tournament_id": "1",
                "tournament_name": "Baseline Cup",
                "tournament_status": "active",
                "team_id": "30",
                "team_name": "Comets",
                "played": "2",
                "wins": "0",
                "draws": "0",
                "losses": "2",
                "goals_for": "1",
                "goals_against": "5",
                "goal_difference": "-4",
                "points": "0",
                "win_rate": "0.0",
            },
        ],
        "head_to_head.csv": [
            {
                "result_id": "101",
                "tournament_id": "1",
                "tournament_name": "Baseline Cup",
                "tournament_status": "active",
                "fixture_id": "1",
                "fixture_date": "2026-05-01T12:00:00+00:00",
                "team_a_id": "10",
                "team_a_name": "Falcons",
                "team_b_id": "30",
                "team_b_name": "Comets",
                "team_a_goals": "3",
                "team_b_goals": "1",
                "winner_team_id": "10",
                "winner_team_name": "Falcons",
                "is_draw": "False",
            }
        ],
    }

    files = {
        filename: csv_text(filename, rows)
        for filename, rows in rows_by_file.items()
    }
    manifest = {
        "package_version": "phase-4.2-v1",
        "schema_version": "phase-4.2-v1",
        "row_counts": {
            filename: len(rows)
            for filename, rows in rows_by_file.items()
        },
        "files": {
            filename: {
                "rows": len(rows),
                "columns": CSV_HEADERS[filename],
            }
            for filename, rows in rows_by_file.items()
        },
        "known_limitations": ["Zero-stat appearances can be missing."],
        "source_rules": ["Only approved results are exported as result truth."],
    }
    files["manifest.json"] = json.dumps(manifest)
    files["data_dictionary.md"] = "# Data Dictionary\n"

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for filename, content in files.items():
            package.writestr(filename, content)
    return buffer.getvalue()


class LocalPredictionBaselineConsumerTests(TestCase):
    def run_package(self, *, include_unresolved=True, sensitive_player_username=False):
        tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(tmp.cleanup)
        base_path = Path(tmp.name)
        package_path = base_path / "prediction_package.zip"
        output_dir = base_path / "baseline_output"
        package_path.write_bytes(
            build_package_bytes(
                include_unresolved=include_unresolved,
                sensitive_player_username=sensitive_player_username,
            )
        )

        result = run_baseline(package_path, output_dir)
        return result

    def test_script_reads_valid_package_and_creates_required_outputs(self):
        result = self.run_package()

        self.assertTrue(result.output_files["baseline_predictions.csv"].exists())
        self.assertTrue(result.output_files["evaluation_summary.json"].exists())
        self.assertTrue(result.output_files["run_manifest.json"].exists())

        with result.output_files["baseline_predictions.csv"].open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fixture_id"], "3")
        self.assertEqual(rows[0]["home_team"], "Falcons")
        self.assertEqual(rows[0]["away_team"], "Rovers")

        summary = json.loads(result.output_files["evaluation_summary.json"].read_text())
        self.assertEqual(summary["eligible_fixture_count"], 1)
        self.assertEqual(summary["prediction_count"], 1)
        self.assertIn("evaluated_historical_count", summary)

    def test_probabilities_are_bounded_and_sum_approximately_to_100(self):
        result = self.run_package()

        with result.output_files["baseline_predictions.csv"].open(newline="") as handle:
            row = next(csv.DictReader(handle))

        probabilities = [
            float(row["home_win_probability"]),
            float(row["draw_probability"]),
            float(row["away_win_probability"]),
        ]
        for probability in probabilities:
            self.assertGreaterEqual(probability, 0)
            self.assertLessEqual(probability, 100)
        self.assertAlmostEqual(sum(probabilities), 100.0, places=1)

    def test_empty_unresolved_fixture_case_writes_clear_empty_output(self):
        result = self.run_package(include_unresolved=False)

        with result.output_files["baseline_predictions.csv"].open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows, [])

        summary = json.loads(result.output_files["evaluation_summary.json"].read_text())
        self.assertEqual(summary["eligible_fixture_count"], 0)
        self.assertEqual(summary["prediction_count"], 0)
        self.assertTrue(
            any("No unresolved eligible fixtures" in warning for warning in summary["warnings"])
        )

    def test_sensitive_player_fields_do_not_appear_in_outputs(self):
        result = self.run_package(sensitive_player_username=True)

        combined_output = "\n".join(
            path.read_text()
            for path in result.output_files.values()
        )
        self.assertNotIn("secret@example.com", combined_output)
        self.assertNotIn("player_username", combined_output)
        self.assertNotIn("email", combined_output.lower())
