from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Player, Team
from standings.models import PlayerStat
from tournament.models import Fixture, Tournament


def make_player(username, is_staff=False):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_staff=is_staff,
    )


def make_tournament(name, status=Tournament.ACTIVE):
    return Tournament.objects.create(
        name=name,
        format=Tournament.ROUND_ROBIN,
        status=status,
        max_teams=4,
        tournament_type=Tournament.TEAM,
    )


def make_fixture(tournament, home_team, away_team, round_number):
    return Fixture.objects.create(
        tournament=tournament,
        home_team=home_team,
        away_team=away_team,
        round_number=round_number,
        stage=Fixture.GROUP,
    )


class TopStatsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament("Stats League")
        self.home = Team.objects.create(name="Stats Home", is_approved=True)
        self.away = Team.objects.create(name="Stats Away", is_approved=True)
        self.player_one = make_player("assistone")
        self.player_two = make_player("assisttwo")

        fixture_one = make_fixture(self.tournament, self.home, self.away, round_number=1)
        fixture_two = make_fixture(self.tournament, self.home, self.away, round_number=2)

        PlayerStat.objects.create(
            player=self.player_one,
            fixture=fixture_one,
            team=self.home,
            goals=1,
            assists=1,
        )
        PlayerStat.objects.create(
            player=self.player_two,
            fixture=fixture_one,
            team=self.away,
            goals=2,
            assists=3,
        )
        PlayerStat.objects.create(
            player=self.player_one,
            fixture=fixture_two,
            team=self.home,
            goals=1,
            assists=1,
        )

    def test_top_assists_page_lists_players_by_total_assists(self):
        response = self.client.get(reverse("standings:top_assists", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        rows = list(response.context["top_assists"])
        self.assertEqual(rows[0]["player__username"], self.player_two.username)
        self.assertEqual(rows[0]["total_assists"], 3)
        self.assertEqual(rows[1]["player__username"], self.player_one.username)
        self.assertEqual(rows[1]["total_assists"], 2)

    def test_top_stats_pages_use_scroll_wrapper_markup(self):
        scorers_response = self.client.get(reverse("standings:top_scorers", args=[self.tournament.pk]))
        assists_response = self.client.get(reverse("standings:top_assists", args=[self.tournament.pk]))

        self.assertContains(scorers_response, "table-shell")
        self.assertContains(assists_response, "table-shell")

    def test_top_stats_pages_protect_long_tournament_name_in_mobile_breadcrumb_and_header(self):
        long_name_tournament = make_tournament(
            "Extremely Long Tournament Name For Mobile Breadcrumb Stress Validation League"
        )

        scorers_response = self.client.get(reverse("standings:top_scorers", args=[long_name_tournament.pk]))
        assists_response = self.client.get(reverse("standings:top_assists", args=[long_name_tournament.pk]))

        self.assertContains(scorers_response, "breadcrumb flex-wrap")
        self.assertContains(assists_response, "breadcrumb flex-wrap")
        self.assertContains(scorers_response, "min-w-0 max-w-[10rem] truncate sm:max-w-none")
        self.assertContains(assists_response, "min-w-0 max-w-[10rem] truncate sm:max-w-none")
        self.assertContains(scorers_response, "section-label mb-1 max-w-[10rem] truncate sm:max-w-none")
        self.assertContains(assists_response, "section-label mb-1 max-w-[10rem] truncate sm:max-w-none")

    def test_anonymous_user_cannot_access_draft_top_assists(self):
        draft_tournament = make_tournament("Draft Assists", status=Tournament.DRAFT)

        response = self.client.get(reverse("standings:top_assists", args=[draft_tournament.pk]))

        self.assertEqual(response.status_code, 404)

    def test_staff_user_can_access_draft_top_assists(self):
        draft_tournament = make_tournament("Staff Draft Assists", status=Tournament.DRAFT)
        staff = make_player("assiststaff", is_staff=True)
        self.client.login(username=staff.username, password="testpass123")

        response = self.client.get(reverse("standings:top_assists", args=[draft_tournament.pk]))

        self.assertEqual(response.status_code, 200)
