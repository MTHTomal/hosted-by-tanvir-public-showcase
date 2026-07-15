from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Player, Team, TeamMembership
from standings.models import PlayerStat, Standing
from tournament.models import Fixture, Result, Tournament


def make_player(username, is_active=True):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_active=is_active,
    )


def make_team(name, captain=None, is_approved=True):
    return Team.objects.create(
        name=name,
        captain=captain,
        is_approved=is_approved,
    )


def make_tournament(name, status=Tournament.ACTIVE):
    return Tournament.objects.create(
        name=name,
        format=Tournament.ROUND_ROBIN,
        status=status,
        max_teams=4,
        tournament_type=Tournament.TEAM,
    )


def make_fixture(tournament, home, away, round_number=1):
    return Fixture.objects.create(
        tournament=tournament,
        home_team=home,
        away_team=away,
        round_number=round_number,
        stage=Fixture.GROUP,
    )


class PublicProfileHistoryTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_player_profile_shows_career_stats_section(self):
        player = make_player("profilecareer")
        team = make_team("Profile Career Team")
        opponent = make_team("Profile Career Opponent")
        tournament = make_tournament("Profile Active Cup")
        fixture = make_fixture(tournament, team, opponent)
        Result.objects.create(
            fixture=fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
        )
        PlayerStat.objects.create(
            player=player,
            fixture=fixture,
            team=team,
            goals=3,
            assists=2,
            yellow_cards=1,
            red_cards=1,
        )
        TeamMembership.objects.create(player=player, team=team)

        response = self.client.get(reverse("accounts:profile", args=[player.username]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Career Stats")
        self.assertContains(response, "Appearances")
        self.assertContains(response, "Current Tournament")
        self.assertContains(response, "Profile Active Cup")
        self.assertContains(response, "Profile Career Team")
        self.assertEqual(response.context["career_stats"]["total_goals"], 3)
        self.assertEqual(response.context["career_stats"]["appearances"], 1)

    def test_team_profile_shows_historical_stats_section(self):
        team = make_team("Profile History Team")
        tournament = make_tournament("Profile Completed Cup", status=Tournament.COMPLETED)
        Standing.objects.create(
            tournament=tournament,
            team=team,
            played=4,
            wins=2,
            draws=1,
            losses=1,
            goals_for=7,
            goals_against=5,
            goal_difference=2,
            points=7,
        )

        response = self.client.get(reverse("accounts:team_detail", args=[team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historical Stats")
        self.assertContains(response, "W-D-L")
        self.assertContains(response, "Goals Against")
        self.assertContains(response, "Profile Completed Cup")
        self.assertEqual(response.context["historical_stats"]["total_played"], 4)
        self.assertEqual(response.context["historical_stats"]["total_draws"], 1)
        self.assertEqual(response.context["historical_stats"]["total_losses"], 1)

    def test_profile_pages_do_not_crash_when_no_stats_exist(self):
        player = make_player("emptystatsplayer")
        team = make_team("Empty Stats Team")

        player_response = self.client.get(reverse("accounts:profile", args=[player.username]))
        team_response = self.client.get(reverse("accounts:team_detail", args=[team.pk]))

        self.assertEqual(player_response.status_code, 200)
        self.assertContains(player_response, "Career Stats")
        self.assertContains(player_response, "No recorded stats yet")
        self.assertContains(player_response, "No active tournament stats yet")
        self.assertEqual(player_response.context["career_stats"]["appearances"], 0)

        self.assertEqual(team_response.status_code, 200)
        self.assertContains(team_response, "Historical Stats")
        self.assertContains(team_response, "No tournament history yet")
        self.assertEqual(team_response.context["historical_stats"]["total_played"], 0)

    def test_inactive_player_profile_visibility_follows_existing_behavior(self):
        inactive_player = make_player("inactiveprofile", is_active=False)

        response = self.client.get(reverse("accounts:profile", args=[inactive_player.username]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Career Stats")
