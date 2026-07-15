from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Player, Team, TeamMembership
from tournament.models import Fixture, Result, Tournament


def make_player(username, *, is_staff=False):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_staff=is_staff,
    )


def make_team(name):
    return Team.objects.create(name=name, is_approved=True)


def make_tournament(name="Phase 2 Cup"):
    return Tournament.objects.create(
        name=name,
        format=Tournament.ROUND_ROBIN,
        status=Tournament.ACTIVE,
        max_teams=4,
        tournament_type=Tournament.TEAM,
    )


def make_fixture(tournament, home, away):
    return Fixture.objects.create(
        tournament=tournament,
        home_team=home,
        away_team=away,
        round_number=1,
        stage=Fixture.GROUP,
    )


class ResultEvidenceFormTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament("Phase 2 Form Cup")
        self.home = make_team("Phase2 Home")
        self.away = make_team("Phase2 Away")
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.player = make_player("phase2_form_player")
        TeamMembership.objects.create(
            player=self.player,
            team=self.home,
            role=TeamMembership.PLAYER,
        )

    def test_result_submit_form_exposes_all_three_optional_screenshot_fields(self):
        self.client.login(username="phase2_form_player", password="testpass123")
        response = self.client.get(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="screenshot"')
        self.assertContains(response, 'name="home_player_stats_screenshot"')
        self.assertContains(response, 'name="away_player_stats_screenshot"')
        self.assertContains(response, "Match screenshot (optional)")

    def test_submit_without_any_screenshots_keeps_all_evidence_fields_empty(self):
        self.client.login(username="phase2_form_player", password="testpass123")
        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            {"home_score": 3, "away_score": 1},
        )

        submitted = Result.objects.get(fixture=self.fixture)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(bool(submitted.screenshot))
        self.assertFalse(bool(submitted.home_player_stats_screenshot))
        self.assertFalse(bool(submitted.away_player_stats_screenshot))


class FixtureDetailEvidenceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament("Phase 2 Fixture Cup")
        self.home = make_team("Fixture Evidence Home")
        self.away = make_team("Fixture Evidence Away")
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.staff = make_player("phase2_fixture_staff", is_staff=True)

    def test_fixture_detail_shows_legacy_match_screenshot_only(self):
        Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=self.staff,
            screenshot="demo-validation/legacy-match",
        )
        self.client.login(username="phase2_fixture_staff", password="testpass123")

        response = self.client.get(reverse("tournament:fixture_detail", args=[self.fixture.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Evidence")
        self.assertContains(response, "Match screenshot")
        self.assertContains(response, "demo-validation/legacy-match")
        self.assertNotContains(response, "Home team player-stat screenshot")
        self.assertNotContains(response, "Away team player-stat screenshot")

    def test_fixture_detail_shows_all_three_evidence_labels_when_present(self):
        Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=self.staff,
            screenshot="demo-validation/match-proof",
            home_player_stats_screenshot="demo-validation/home-proof",
            away_player_stats_screenshot="demo-validation/away-proof",
        )
        self.client.login(username="phase2_fixture_staff", password="testpass123")

        response = self.client.get(reverse("tournament:fixture_detail", args=[self.fixture.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Match screenshot")
        self.assertContains(response, "Home team player-stat screenshot")
        self.assertContains(response, "Away team player-stat screenshot")
        self.assertContains(response, "demo-validation/match-proof")
        self.assertContains(response, "demo-validation/home-proof")
        self.assertContains(response, "demo-validation/away-proof")

    def test_fixture_detail_hides_evidence_links_for_non_staff_users(self):
        submitter = make_player("phase2_fixture_submitter")
        TeamMembership.objects.create(
            player=submitter,
            team=self.home,
            role=TeamMembership.PLAYER,
        )
        Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=self.staff,
            screenshot="demo-validation/match-proof-hidden",
            home_player_stats_screenshot="demo-validation/home-proof-hidden",
            away_player_stats_screenshot="demo-validation/away-proof-hidden",
        )
        self.client.login(username="phase2_fixture_submitter", password="testpass123")

        response = self.client.get(reverse("tournament:fixture_detail", args=[self.fixture.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Uploaded screenshots for this result submission.")
        self.assertNotContains(response, "Match screenshot")
        self.assertNotContains(response, "demo-validation/match-proof-hidden")


class ResultQueueEvidenceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament("Phase 2 Queue Cup")
        self.home = make_team("Queue Evidence Home")
        self.away = make_team("Queue Evidence Away")
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.staff = make_player("phase2_queue_staff", is_staff=True)
        self.submitter = make_player("phase2_queue_submitter")
        TeamMembership.objects.create(
            player=self.submitter,
            team=self.home,
            role=TeamMembership.PLAYER,
        )

    def test_staff_queue_shows_labeled_evidence_links_for_pending_result(self):
        Result.objects.create(
            fixture=self.fixture,
            home_score=4,
            away_score=2,
            status=Result.PENDING,
            submitted_by=self.submitter,
            submitting_team=self.home,
            screenshot="demo-validation/queue-match",
            home_player_stats_screenshot="demo-validation/queue-home",
            away_player_stats_screenshot="demo-validation/queue-away",
        )
        self.client.login(username="phase2_queue_staff", password="testpass123")

        response = self.client.get(reverse("tournament:admin_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Evidence")
        self.assertContains(response, "Match screenshot")
        self.assertContains(response, "Home team player-stat screenshot")
        self.assertContains(response, "Away team player-stat screenshot")
        self.assertContains(response, "demo-validation/queue-match")
        self.assertContains(response, "demo-validation/queue-home")
        self.assertContains(response, "demo-validation/queue-away")
