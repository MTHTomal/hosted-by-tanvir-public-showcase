from django.test import TestCase
from django.urls import reverse

from accounts.models import Player, Team
from standings.models import PlayerStat
from tournament.models import Fixture, Result, ResultPlayerStat, Tournament
from tournament.services import get_fixture_prediction


def make_player(username, *, is_staff=False):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_staff=is_staff,
    )


def make_team(name):
    return Team.objects.create(name=name, is_approved=True)


def make_tournament(name="Prediction Cup", *, status=Tournament.ACTIVE):
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
    )


def make_result(fixture, *, home_score=1, away_score=0, status=Result.APPROVED):
    return Result.objects.create(
        fixture=fixture,
        home_score=home_score,
        away_score=away_score,
        status=status,
    )


class BasicFixturePredictionServiceTests(TestCase):
    def setUp(self):
        self.tournament = make_tournament()
        self.home = make_team("Prediction Home")
        self.away = make_team("Prediction Away")
        self.other = make_team("Prediction Other")
        self.fixture = make_fixture(
            self.tournament,
            self.home,
            self.away,
            round_number=9,
        )

    def add_basic_history(self):
        home_history = make_fixture(
            self.tournament,
            self.home,
            self.other,
            round_number=1,
        )
        make_result(home_history, home_score=3, away_score=1)
        away_history = make_fixture(
            self.tournament,
            self.away,
            self.other,
            round_number=2,
        )
        make_result(away_history, home_score=1, away_score=2)
        return home_history, away_history

    def test_prediction_unavailable_for_bye_fixtures(self):
        bye_fixture = make_fixture(
            self.tournament,
            self.home,
            None,
            round_number=10,
        )

        prediction = get_fixture_prediction(bye_fixture)

        self.assertFalse(prediction["is_available"])
        self.assertIn("bye", prediction["unavailable_reason"].lower())

    def test_prediction_unavailable_when_one_or_both_teams_are_missing(self):
        fixtures = [
            Fixture(
                tournament=self.tournament,
                home_team=self.home,
                round_number=1,
                stage=Fixture.GROUP,
                is_bye=False,
            ),
            Fixture(
                tournament=self.tournament,
                away_team=self.away,
                round_number=1,
                stage=Fixture.GROUP,
                is_bye=False,
            ),
        ]

        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                prediction = get_fixture_prediction(fixture)
                self.assertFalse(prediction["is_available"])
                self.assertIn("both teams", prediction["unavailable_reason"].lower())

    def test_prediction_unavailable_for_fixtures_with_approved_result(self):
        make_result(self.fixture, home_score=2, away_score=1)

        prediction = get_fixture_prediction(self.fixture)

        self.assertFalse(prediction["is_available"])
        self.assertIn("official result", prediction["unavailable_reason"].lower())

    def test_prediction_unavailable_for_completed_or_archived_fixture_targets(self):
        for status in [Tournament.COMPLETED, Tournament.ARCHIVED]:
            with self.subTest(status=status):
                tournament = make_tournament(f"Closed Prediction Cup {status}", status=status)
                fixture = make_fixture(tournament, self.home, self.away, round_number=1)

                prediction = get_fixture_prediction(fixture)

                self.assertFalse(prediction["is_available"])
                self.assertIn("upcoming or active", prediction["unavailable_reason"].lower())

    def test_prediction_unavailable_and_hidden_for_draft_tournament_leakage_case(self):
        draft_tournament = make_tournament("Draft Prediction Cup", status=Tournament.DRAFT)
        draft_fixture = make_fixture(
            draft_tournament,
            self.home,
            self.away,
            round_number=1,
        )

        prediction = get_fixture_prediction(draft_fixture)

        self.assertFalse(prediction["is_available"])
        self.assertIn("draft", prediction["unavailable_reason"].lower())

        public_response = self.client.get(
            reverse("tournament:fixture_detail", args=[draft_fixture.pk])
        )
        self.assertEqual(public_response.status_code, 404)

        staff = make_player("predictiondraftstaff", is_staff=True)
        self.client.login(username=staff.username, password="testpass123")
        staff_response = self.client.get(
            reverse("tournament:fixture_detail", args=[draft_fixture.pk])
        )
        self.assertEqual(staff_response.status_code, 200)
        self.assertNotContains(staff_response, "Transparent formula-based prediction")

    def test_prediction_works_for_unresolved_fixture_with_approved_history(self):
        self.add_basic_history()

        prediction = get_fixture_prediction(self.fixture)

        self.assertTrue(prediction["is_available"])
        self.assertEqual(prediction["home_team"], self.home)
        self.assertEqual(prediction["away_team"], self.away)
        self.assertIsNotNone(prediction["predicted_score_label"])
        self.assertGreater(len(prediction["explanation_lines"]), 1)

    def test_probabilities_are_bounded_and_roughly_sum_to_100(self):
        self.add_basic_history()

        prediction = get_fixture_prediction(self.fixture)

        probabilities = [
            prediction["home_win_probability"],
            prediction["draw_probability"],
            prediction["away_win_probability"],
        ]
        for probability in probabilities:
            self.assertGreaterEqual(probability, 0)
            self.assertLessEqual(probability, 100)
        self.assertAlmostEqual(sum(probabilities), 100, places=1)

    def test_player_threat_uses_only_official_playerstats_from_approved_results(self):
        home_history, _ = self.add_basic_history()
        home_scorer = make_player("predictionhomethreat")
        pending_player = make_player("predictionpendingthreat")
        PlayerStat.objects.create(
            player=home_scorer,
            fixture=home_history,
            team=self.home,
            goals=3,
            assists=1,
        )

        pending_fixture = make_fixture(
            self.tournament,
            self.away,
            self.other,
            round_number=3,
        )
        pending_result = make_result(
            pending_fixture,
            home_score=9,
            away_score=0,
            status=Result.PENDING,
        )
        ResultPlayerStat.objects.create(
            result=pending_result,
            player=pending_player,
            team=self.away,
            goals=9,
            assists=9,
        )
        PlayerStat.objects.create(
            player=pending_player,
            fixture=pending_fixture,
            team=self.away,
            goals=9,
            assists=9,
        )

        prediction = get_fixture_prediction(self.fixture)
        player_threat = prediction["factors"]["player_threat"]

        self.assertTrue(prediction["is_available"])
        self.assertEqual(player_threat["home_official_goals"], 3)
        self.assertEqual(player_threat["away_official_goals"], 0)
        self.assertEqual(player_threat["away_scoring_rows"], 0)
        self.assertGreater(
            player_threat["home_rating"],
            player_threat["away_rating"],
        )

    def test_player_threat_does_not_assume_zero_stat_appearances(self):
        _, away_history = self.add_basic_history()
        zero_stat_player = make_player("predictionzerostat")
        PlayerStat.objects.create(
            player=zero_stat_player,
            fixture=away_history,
            team=self.away,
            goals=0,
            assists=0,
        )

        prediction = get_fixture_prediction(self.fixture)
        player_threat = prediction["factors"]["player_threat"]

        self.assertTrue(prediction["is_available"])
        self.assertEqual(player_threat["data_points"], 0)
        self.assertEqual(player_threat["away_scoring_rows"], 0)
        self.assertEqual(player_threat["away_rating"], 0.5)
        self.assertIn(
            "zero-stat appearances are not assumed",
            player_threat["summary"],
        )

    def test_head_to_head_affects_explanation_when_data_exists(self):
        self.add_basic_history()
        previous_meeting = make_fixture(
            self.tournament,
            self.away,
            self.home,
            round_number=4,
        )
        make_result(previous_meeting, home_score=1, away_score=2)

        prediction = get_fixture_prediction(self.fixture)
        head_to_head = prediction["factors"]["head_to_head"]

        self.assertTrue(prediction["is_available"])
        self.assertEqual(head_to_head["data_points"], 1)
        self.assertIn("Head-to-head: 1 approved meeting", head_to_head["summary"])
        self.assertTrue(
            any("Head-to-head: 1 approved meeting" in line for line in prediction["explanation_lines"])
        )

    def test_fixture_detail_renders_prediction_panel_for_eligible_fixture(self):
        self.add_basic_history()

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Transparent formula-based prediction")
        self.assertContains(response, "Estimate from approved match history")
        self.assertContains(response, "Not a guarantee")
