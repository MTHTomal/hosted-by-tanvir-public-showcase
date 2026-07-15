import csv
import json
import zipfile
from io import BytesIO
from io import StringIO

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Player, Team
from standings.models import PlayerStat, Standing
from tournament.models import Fixture, Result, ResultPlayerStat, Tournament
from tournament.player_stat_fields import (
    ADVANCED_PLAYER_STAT_FIELDS,
    DETAILED_INTEGER_PLAYER_STAT_FIELDS,
    PERCENTAGE_PLAYER_STAT_FIELDS,
)


DETAILED_PLAYER_STAT_FIELDS = (
    ADVANCED_PLAYER_STAT_FIELDS
    + DETAILED_INTEGER_PLAYER_STAT_FIELDS
    + PERCENTAGE_PLAYER_STAT_FIELDS
)

EVIDENCE_COLUMNS = [
    "result_screenshot_url_or_path",
    "home_stats_screenshot_url_or_path",
    "away_stats_screenshot_url_or_path",
    "has_result_screenshot",
    "has_home_stats_screenshot",
    "has_away_stats_screenshot",
]

AVAILABILITY_COLUMNS = [
    "advanced_stats_available",
    "manual_stats_available",
    "screenshot_evidence_available",
]


def make_player(username, *, is_staff=False):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_staff=is_staff,
    )


def make_team(name):
    return Team.objects.create(name=name, is_approved=True)


def make_tournament(name, *, status=Tournament.ACTIVE):
    return Tournament.objects.create(
        name=name,
        format=Tournament.ROUND_ROBIN,
        status=status,
        max_teams=4,
        tournament_type=Tournament.TEAM,
    )


def make_fixture(tournament, home, away, *, round_number=1):
    return Fixture.objects.create(
        tournament=tournament,
        home_team=home,
        away_team=away,
        round_number=round_number,
        stage=Fixture.GROUP,
        match_date=timezone.make_aware(timezone.datetime(2026, 5, round_number, 12, 0, 0)),
    )


def csv_rows(response):
    return list(csv.DictReader(StringIO(response.content.decode())))


def package_files(response):
    with zipfile.ZipFile(BytesIO(response.content)) as package:
        return {
            filename: package.read(filename).decode()
            for filename in package.namelist()
        }


def package_csv_rows(files, filename):
    return list(csv.DictReader(StringIO(files[filename])))


class StaffCSVExportPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = make_player("exportstaff", is_staff=True)
        self.player = make_player("exportplayer")
        self.urls = [
            reverse("tournament:staff_export_dashboard"),
            reverse("tournament:staff_export_results_csv"),
            reverse("tournament:staff_export_player_stats_csv"),
            reverse("tournament:staff_export_team_stats_csv"),
            reverse("tournament:staff_export_head_to_head_csv"),
            reverse("tournament:staff_export_prediction_dataset_zip"),
        ]

    def test_staff_export_dashboard_loads(self):
        self.client.login(username=self.staff.username, password="testpass123")

        response = self.client.get(reverse("tournament:staff_export_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tournament/staff_exports.html")
        self.assertContains(response, "Google Sheets API integration is deferred")
        self.assertContains(response, reverse("tournament:staff_export_results_csv"))
        self.assertContains(response, reverse("tournament:staff_export_prediction_dataset_zip"))

    def test_anonymous_users_cannot_access_export_dashboard_or_downloads(self):
        for url in self.urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response.url)

    def test_normal_players_cannot_access_export_dashboard_or_downloads(self):
        self.client.login(username=self.player.username, password="testpass123")

        for url in self.urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)


class StaffCSVExportDownloadTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = make_player("downloadstaff", is_staff=True)
        self.player = make_player("downloadplayer")
        self.stat_player = make_player("statplayer")
        self.stat_player.in_game_name = "StatIGN"
        self.stat_player.save(update_fields=["in_game_name"])

        self.tournament = make_tournament("Export Cup", status=Tournament.ACTIVE)
        self.draft_tournament = make_tournament("Draft Export Cup", status=Tournament.DRAFT)
        self.completed_tournament = make_tournament("Completed Export Cup", status=Tournament.COMPLETED)
        self.archived_tournament = make_tournament("Archived Export Cup", status=Tournament.ARCHIVED)

        self.home = make_team("Export Home")
        self.away = make_team("Export Away")
        self.extra = make_team("Export Extra")

        self.approved_fixture = make_fixture(self.tournament, self.home, self.away, round_number=1)
        self.approved_result = Result.objects.create(
            fixture=self.approved_fixture,
            submitted_by=self.player,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            reviewed_at=timezone.now(),
            screenshot="exports/match-proof",
            home_player_stats_screenshot="exports/home-stat-proof",
            away_player_stats_screenshot="exports/away-stat-proof",
        )
        self.player_stat = PlayerStat.objects.create(
            player=self.stat_player,
            fixture=self.approved_fixture,
            team=self.home,
            goals=2,
            assists=1,
            yellow_cards=1,
            red_cards=0,
            total_points=88,
            offensive_positioning=82,
            shooting=76,
            dueling=64,
            defensive_positioning=51,
            passing=79,
            dribbling=73,
            shots=5,
            shots_on_target=3,
            key_passes=2,
            passes=44,
            successful_passes=39,
            instrumental_passes=4,
            dribbles=8,
            successful_dribbles=6,
            instrumental_dribbles=2,
            receiving=11,
            good_receives=9,
            overlaps=1,
            runs_out_wide=2,
            forward_runs=7,
            offensive_receives=10,
            intercepts=3,
            tackles=4,
            impactful_steals=1,
            frontal_presses=5,
            presses_from_behind=2,
            good_positioning_pct="77.25",
            double_marks=1,
            passes_obstructed=2,
            players_marked=3,
        )
        self.zero_stat_player = make_player("zerostatplayer")
        self.zero_player_stat = PlayerStat.objects.create(
            player=self.zero_stat_player,
            fixture=self.approved_fixture,
            team=self.away,
        )

        self.pending_fixture = make_fixture(self.tournament, self.home, self.extra, round_number=2)
        self.pending_result = Result.objects.create(
            fixture=self.pending_fixture,
            submitted_by=self.player,
            home_score=2,
            away_score=2,
            status=Result.PENDING,
        )
        ResultPlayerStat.objects.create(
            result=self.pending_result,
            player=self.player,
            team=self.home,
            goals=9,
            assists=9,
        )
        PlayerStat.objects.create(
            player=self.player,
            fixture=self.pending_fixture,
            team=self.home,
            goals=8,
            assists=8,
        )

        self.rejected_fixture = make_fixture(self.tournament, self.away, self.extra, round_number=3)
        Result.objects.create(
            fixture=self.rejected_fixture,
            submitted_by=self.player,
            home_score=4,
            away_score=0,
            status=Result.REJECTED,
        )
        self.disputed_fixture = make_fixture(self.completed_tournament, self.home, self.extra, round_number=4)
        Result.objects.create(
            fixture=self.disputed_fixture,
            submitted_by=self.player,
            home_score=1,
            away_score=0,
            status=Result.DISPUTED,
        )

        draft_fixture = make_fixture(self.draft_tournament, self.home, self.away, round_number=5)
        Result.objects.create(
            fixture=draft_fixture,
            submitted_by=self.player,
            home_score=5,
            away_score=0,
            status=Result.APPROVED,
            reviewed_at=timezone.now(),
        )
        PlayerStat.objects.create(
            player=self.player,
            fixture=draft_fixture,
            team=self.home,
            goals=5,
        )
        Standing.objects.create(
            tournament=self.draft_tournament,
            team=self.extra,
            played=1,
            wins=1,
            goals_for=5,
            points=3,
            goal_difference=5,
        )

        archived_fixture = make_fixture(self.archived_tournament, self.home, self.extra, round_number=6)
        Result.objects.create(
            fixture=archived_fixture,
            submitted_by=self.player,
            home_score=0,
            away_score=0,
            status=Result.APPROVED,
            reviewed_at=timezone.now(),
        )

    def get_as_staff(self, url_name):
        self.client.login(username=self.staff.username, password="testpass123")
        return self.client.get(reverse(url_name))

    def assert_csv_download(self, response, filename):
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(filename, response["Content-Disposition"])

    def test_staff_can_download_each_csv(self):
        downloads = [
            ("tournament:staff_export_results_csv", "approved-results.csv"),
            ("tournament:staff_export_player_stats_csv", "player-stats.csv"),
            ("tournament:staff_export_team_stats_csv", "team-stats.csv"),
            ("tournament:staff_export_head_to_head_csv", "head-to-head.csv"),
        ]

        for url_name, filename in downloads:
            with self.subTest(url_name=url_name):
                response = self.get_as_staff(url_name)
                self.assert_csv_download(response, filename)

    def test_staff_can_download_prediction_dataset_zip(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("hosted-by-tanvir-prediction-dataset.zip", response["Content-Disposition"])

    def test_approved_results_export_includes_approved_results_only(self):
        response = self.get_as_staff("tournament:staff_export_results_csv")
        rows = csv_rows(response)

        fixture_ids = {row["fixture_id"] for row in rows}
        self.assertIn(str(self.approved_fixture.pk), fixture_ids)
        self.assertIn(str(self.approved_result.home_score), {row["home_score"] for row in rows})
        self.assertNotIn(str(self.pending_fixture.pk), fixture_ids)
        self.assertNotIn(str(self.rejected_fixture.pk), fixture_ids)
        self.assertNotIn(str(self.disputed_fixture.pk), fixture_ids)
        self.assertNotIn(self.draft_tournament.name, response.content.decode())
        self.assertIn(self.archived_tournament.name, response.content.decode())

    def test_player_stats_export_uses_playerstat_and_excludes_sensitive_fields(self):
        response = self.get_as_staff("tournament:staff_export_player_stats_csv")
        content = response.content.decode()
        rows = csv_rows(response)

        fixture_ids = {row["fixture_id"] for row in rows}
        self.assertIn(str(self.approved_fixture.pk), fixture_ids)
        self.assertNotIn(str(self.pending_fixture.pk), fixture_ids)
        self.assertNotIn(self.draft_tournament.name, content)
        self.assertNotIn("9", [row["goals"] for row in rows])
        self.assertNotIn(self.stat_player.email, content)
        self.assertNotIn("password", content.lower())
        self.assertNotIn("is_staff", content)
        official_row = next(row for row in rows if row["player_id"] == str(self.stat_player.pk))
        self.assertEqual(official_row["player_name"], self.stat_player.username)
        self.assertEqual(official_row["player_ign"], "StatIGN")
        self.assertEqual(official_row["appearance_count"], "1")

    def test_team_stats_export_includes_standing_data_and_win_rate(self):
        Standing.objects.update_or_create(
            tournament=self.completed_tournament,
            team=self.home,
            defaults={
                "played": 4,
                "wins": 3,
                "draws": 1,
                "losses": 0,
                "goals_for": 10,
                "goals_against": 3,
                "goal_difference": 7,
                "points": 10,
            },
        )

        response = self.get_as_staff("tournament:staff_export_team_stats_csv")
        rows = csv_rows(response)
        row = next(
            row
            for row in rows
            if row["tournament_id"] == str(self.completed_tournament.pk)
            and row["team_id"] == str(self.home.pk)
        )

        self.assertEqual(row["played"], "4")
        self.assertEqual(row["wins"], "3")
        self.assertEqual(row["win_rate"], "75.0")
        self.assertNotIn(self.draft_tournament.name, response.content.decode())

    def test_head_to_head_export_includes_approved_and_excludes_unapproved_rows(self):
        response = self.get_as_staff("tournament:staff_export_head_to_head_csv")
        rows = csv_rows(response)
        fixture_ids = {row["fixture_id"] for row in rows}
        approved_row = next(row for row in rows if row["fixture_id"] == str(self.approved_fixture.pk))

        self.assertIn(str(self.approved_fixture.pk), fixture_ids)
        self.assertNotIn(str(self.pending_fixture.pk), fixture_ids)
        self.assertNotIn(self.draft_tournament.name, response.content.decode())
        self.assertEqual(approved_row["team_a_id"], str(self.home.pk))
        self.assertEqual(approved_row["team_a_goals"], "3")
        self.assertEqual(approved_row["team_b_goals"], "1")
        self.assertEqual(approved_row["winner_team_id"], str(self.home.pk))
        self.assertEqual(approved_row["is_draw"], "False")

    def test_prediction_dataset_package_contains_expected_files(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        files = package_files(response)

        self.assertEqual(
            set(files),
            {
                "approved_results.csv",
                "fixtures.csv",
                "tournaments.csv",
                "official_player_stats.csv",
                "team_standings_history.csv",
                "head_to_head.csv",
                "manifest.json",
                "data_dictionary.md",
            },
        )

    def test_prediction_dataset_package_filters_to_approved_non_draft_data(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        files = package_files(response)
        approved_rows = package_csv_rows(files, "approved_results.csv")
        fixture_rows = package_csv_rows(files, "fixtures.csv")
        player_stat_rows = package_csv_rows(files, "official_player_stats.csv")
        head_to_head_rows = package_csv_rows(files, "head_to_head.csv")

        approved_fixture_ids = {row["fixture_id"] for row in approved_rows}
        packaged_fixture_ids = {row["fixture_id"] for row in fixture_rows}
        player_stat_fixture_ids = {row["fixture_id"] for row in player_stat_rows}
        h2h_fixture_ids = {row["fixture_id"] for row in head_to_head_rows}

        self.assertIn(str(self.approved_fixture.pk), approved_fixture_ids)
        self.assertIn(str(self.approved_fixture.pk), packaged_fixture_ids)
        self.assertIn(str(self.approved_fixture.pk), player_stat_fixture_ids)
        self.assertIn(str(self.approved_fixture.pk), h2h_fixture_ids)
        self.assertNotIn(str(self.pending_fixture.pk), approved_fixture_ids)
        self.assertNotIn(str(self.pending_fixture.pk), packaged_fixture_ids)
        self.assertNotIn(str(self.pending_fixture.pk), player_stat_fixture_ids)
        self.assertNotIn(str(self.pending_fixture.pk), h2h_fixture_ids)
        self.assertNotIn(str(self.rejected_fixture.pk), approved_fixture_ids)
        self.assertNotIn(str(self.disputed_fixture.pk), approved_fixture_ids)
        self.assertNotIn(self.draft_tournament.name, "\n".join(files.values()))

    def test_prediction_dataset_package_excludes_sensitive_fields(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        files = package_files(response)
        content = "\n".join(files.values())

        self.assertNotIn(self.staff.email, content)
        self.assertNotIn(self.player.email, content)
        self.assertNotIn(self.stat_player.email, content)
        self.assertNotIn("password", content.lower())
        self.assertNotIn("is_staff", content)
        self.assertNotIn("auth metadata", content.lower())

    def test_prediction_dataset_official_player_stats_include_detailed_fields(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        files = package_files(response)
        header = files["official_player_stats.csv"].splitlines()[0].split(",")
        rows = package_csv_rows(files, "official_player_stats.csv")
        row = next(row for row in rows if row["player_id"] == str(self.stat_player.pk))

        self.assertIn("approved_result_id", header)
        for field_name in DETAILED_PLAYER_STAT_FIELDS:
            with self.subTest(field_name=field_name):
                self.assertIn(field_name, header)

        self.assertEqual(row["approved_result_id"], str(self.approved_result.pk))
        self.assertEqual(row["total_points"], "88")
        self.assertEqual(row["shots"], "5")
        self.assertEqual(row["shots_on_target"], "3")
        self.assertEqual(row["passes"], "44")
        self.assertEqual(row["successful_passes"], "39")
        self.assertEqual(row["dribbles"], "8")
        self.assertEqual(row["successful_dribbles"], "6")
        self.assertEqual(row["good_positioning_pct"], "77.25")

    def test_prediction_dataset_exports_screenshot_references_and_flags(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        files = package_files(response)
        approved_rows = package_csv_rows(files, "approved_results.csv")
        player_stat_rows = package_csv_rows(files, "official_player_stats.csv")
        approved_row = next(
            row for row in approved_rows if row["fixture_id"] == str(self.approved_fixture.pk)
        )
        player_row = next(
            row for row in player_stat_rows if row["player_id"] == str(self.stat_player.pk)
        )

        for row in [approved_row, player_row]:
            for column in EVIDENCE_COLUMNS + AVAILABILITY_COLUMNS:
                with self.subTest(column=column):
                    self.assertIn(column, row)

        self.assertIn("exports/match-proof", approved_row["result_screenshot_url_or_path"])
        self.assertIn("exports/home-stat-proof", approved_row["home_stats_screenshot_url_or_path"])
        self.assertIn("exports/away-stat-proof", approved_row["away_stats_screenshot_url_or_path"])
        self.assertEqual(approved_row["has_result_screenshot"], "True")
        self.assertEqual(approved_row["has_home_stats_screenshot"], "True")
        self.assertEqual(approved_row["has_away_stats_screenshot"], "True")
        self.assertEqual(approved_row["manual_stats_available"], "True")
        self.assertEqual(approved_row["advanced_stats_available"], "True")
        self.assertEqual(approved_row["screenshot_evidence_available"], "True")
        self.assertIn("exports/home-stat-proof", player_row["home_stats_screenshot_url_or_path"])
        self.assertEqual(player_row["manual_stats_available"], "True")
        self.assertEqual(player_row["advanced_stats_available"], "True")
        self.assertEqual(player_row["screenshot_evidence_available"], "True")

    def test_prediction_dataset_preserves_zero_stats_without_marking_them_missing(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        files = package_files(response)
        rows = package_csv_rows(files, "official_player_stats.csv")
        zero_row = next(
            row for row in rows if row["player_id"] == str(self.zero_stat_player.pk)
        )

        integer_fields = [
            field_name
            for field_name in DETAILED_PLAYER_STAT_FIELDS
            if field_name != "good_positioning_pct"
        ]
        for field_name in integer_fields:
            with self.subTest(field_name=field_name):
                self.assertEqual(zero_row[field_name], "0")
        self.assertEqual(zero_row["good_positioning_pct"], "0.00")
        self.assertEqual(zero_row["manual_stats_available"], "True")
        self.assertEqual(zero_row["advanced_stats_available"], "True")
        self.assertEqual(zero_row["screenshot_evidence_available"], "True")

    def test_prediction_dataset_manifest_row_counts_match_csvs(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        files = package_files(response)
        manifest = json.loads(files["manifest.json"])

        for filename, count in manifest["row_counts"].items():
            with self.subTest(filename=filename):
                self.assertEqual(count, len(package_csv_rows(files, filename)))
                self.assertEqual(manifest["files"][filename]["rows"], count)

    def test_prediction_dataset_data_dictionary_documents_every_csv_column(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        files = package_files(response)
        manifest = json.loads(files["manifest.json"])
        dictionary = files["data_dictionary.md"]

        self.assertIn("approved_results.csv", dictionary)
        self.assertIn("official_player_stats.csv", dictionary)
        for filename, details in manifest["files"].items():
            for column in details["columns"]:
                with self.subTest(filename=filename, column=column):
                    self.assertIn(f"`{column}`", dictionary)

    def test_prediction_dataset_manifest_and_dictionary_document_local_processing_fields(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        files = package_files(response)
        manifest = json.loads(files["manifest.json"])
        dictionary = files["data_dictionary.md"]
        official_columns = manifest["files"]["official_player_stats.csv"]["columns"]
        approved_columns = manifest["files"]["approved_results.csv"]["columns"]
        limitations = " ".join(manifest["known_limitations"])

        for field_name in DETAILED_PLAYER_STAT_FIELDS:
            with self.subTest(field_name=field_name):
                self.assertIn(field_name, official_columns)
                self.assertIn(f"`{field_name}`", dictionary)
        for column in EVIDENCE_COLUMNS + AVAILABILITY_COLUMNS:
            with self.subTest(column=column):
                self.assertIn(column, official_columns)
                self.assertIn(column, approved_columns)
                self.assertIn(f"`{column}`", dictionary)

        self.assertIn("Result.home_player_stats_screenshot", dictionary)
        self.assertIn("Result.away_player_stats_screenshot", dictionary)
        self.assertIn(
            "Zero values may be real zeros or default values; availability flags must be used to interpret missing advanced data.",
            limitations,
        )
        self.assertIn(
            "OCR/Gemma extraction is not part of the live web app and will run locally in a future/weekly processing step.",
            limitations,
        )

    def test_prediction_dataset_manifest_documents_known_gaps_and_source_rules(self):
        response = self.get_as_staff("tournament:staff_export_prediction_dataset_zip")
        manifest = json.loads(package_files(response)["manifest.json"])
        known_limitations = " ".join(manifest["known_limitations"]).lower()
        source_rules = " ".join(manifest["source_rules"]).lower()

        self.assertIn("zero-stat", known_limitations)
        self.assertIn("lineup", known_limitations)
        self.assertIn("champion", known_limitations)
        self.assertIn("approved", source_rules)
        self.assertIn("draft", source_rules)
        self.assertIn("screenshots", source_rules)


class EmptyCSVExportTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = make_player("emptystaff", is_staff=True)
        self.client.login(username=self.staff.username, password="testpass123")

    def test_empty_exports_still_return_header_row(self):
        urls = [
            reverse("tournament:staff_export_results_csv"),
            reverse("tournament:staff_export_player_stats_csv"),
            reverse("tournament:staff_export_team_stats_csv"),
            reverse("tournament:staff_export_head_to_head_csv"),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                content = response.content.decode().strip()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(content.splitlines()), 1)
                self.assertIn(",", content)
