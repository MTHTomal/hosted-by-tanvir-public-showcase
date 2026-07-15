from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Player, Team, TeamMembership
from standings.models import PlayerStat, Standing
from standings.services import (
    get_head_to_head_stats,
    get_player_career_stats,
    get_player_team_history,
    get_team_historical_stats,
)
from tournament.models import Fixture, Result, ResultPlayerStat, Tournament


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


def make_fixture(tournament, home, away, round_number=1, match_date=None):
    return Fixture.objects.create(
        tournament=tournament,
        home_team=home,
        away_team=away,
        round_number=round_number,
        stage=Fixture.GROUP,
        match_date=match_date,
    )


def make_result(fixture, home_score=1, away_score=0, status=Result.APPROVED):
    return Result.objects.create(
        fixture=fixture,
        home_score=home_score,
        away_score=away_score,
        status=status,
    )


class PlayerCareerStatsServiceTests(TestCase):
    def setUp(self):
        self.player = make_player("careerplayer")
        self.team = make_team("Career Team")
        self.opponent = make_team("Career Opponent")
        self.tournament = make_tournament("Career Cup")

    def test_player_career_totals_use_playerstat_rows_with_approved_results_only(self):
        approved_fixture = make_fixture(self.tournament, self.team, self.opponent)
        make_result(approved_fixture, home_score=2, away_score=0, status=Result.APPROVED)
        PlayerStat.objects.create(
            player=self.player,
            fixture=approved_fixture,
            team=self.team,
            goals=2,
            assists=1,
            yellow_cards=1,
        )

        pending_fixture = make_fixture(
            self.tournament,
            self.team,
            self.opponent,
            round_number=2,
        )
        make_result(pending_fixture, home_score=9, away_score=0, status=Result.PENDING)
        PlayerStat.objects.create(
            player=self.player,
            fixture=pending_fixture,
            team=self.team,
            goals=9,
            assists=9,
            yellow_cards=9,
            red_cards=9,
        )

        draft_tournament = make_tournament("Draft Career Cup", status=Tournament.DRAFT)
        draft_fixture = make_fixture(draft_tournament, self.team, self.opponent)
        make_result(draft_fixture, home_score=4, away_score=0, status=Result.APPROVED)
        PlayerStat.objects.create(
            player=self.player,
            fixture=draft_fixture,
            team=self.team,
            goals=4,
            assists=4,
        )

        stats = get_player_career_stats(self.player)

        self.assertEqual(stats["total_goals"], 2)
        self.assertEqual(stats["total_assists"], 1)
        self.assertEqual(stats["total_yellow_cards"], 1)
        self.assertEqual(stats["total_red_cards"], 0)
        self.assertEqual(stats["appearances"], 1)
        self.assertEqual(stats["tournaments_played"], 1)

    def test_rejected_disputed_and_unapproved_submitted_stats_do_not_appear(self):
        rejected_fixture = make_fixture(self.tournament, self.team, self.opponent)
        rejected = make_result(rejected_fixture, home_score=3, away_score=1, status=Result.REJECTED)
        disputed_fixture = make_fixture(
            self.tournament,
            self.team,
            self.opponent,
            round_number=2,
        )
        disputed = make_result(disputed_fixture, home_score=2, away_score=2, status=Result.DISPUTED)
        pending_fixture = make_fixture(
            self.tournament,
            self.team,
            self.opponent,
            round_number=3,
        )
        pending = make_result(pending_fixture, home_score=1, away_score=1, status=Result.PENDING)

        for result in [rejected, disputed, pending]:
            ResultPlayerStat.objects.create(
                result=result,
                player=self.player,
                team=self.team,
                goals=5,
                assists=5,
                yellow_cards=1,
                red_cards=1,
            )

        stats = get_player_career_stats(self.player)

        self.assertEqual(stats["total_goals"], 0)
        self.assertEqual(stats["total_assists"], 0)
        self.assertEqual(stats["appearances"], 0)
        self.assertEqual(stats["tournaments_played"], 0)

    def test_appearances_count_playerstat_rows_and_tournaments_are_distinct(self):
        second_tournament = make_tournament("Second Career Cup")
        fixtures = [
            make_fixture(self.tournament, self.team, self.opponent, round_number=1),
            make_fixture(self.tournament, self.team, self.opponent, round_number=2),
            make_fixture(second_tournament, self.team, self.opponent, round_number=1),
        ]
        for fixture in fixtures:
            make_result(fixture, home_score=1, away_score=0, status=Result.APPROVED)
            PlayerStat.objects.create(
                player=self.player,
                fixture=fixture,
                team=self.team,
            )

        stats = get_player_career_stats(self.player)

        self.assertEqual(stats["appearances"], 3)
        self.assertEqual(stats["tournaments_played"], 2)

    def test_teams_played_for_uses_teammembership_history(self):
        old_team = make_team("Old Career Team")
        pending_team = make_team("Pending Career Team", is_approved=False)
        TeamMembership.objects.create(
            player=self.player,
            team=old_team,
            is_active=False,
            left_at=timezone.now(),
        )
        TeamMembership.objects.create(player=self.player, team=self.team)
        TeamMembership.objects.create(
            player=self.player,
            team=pending_team,
            is_active=False,
            left_at=timezone.now(),
        )

        history = get_player_team_history(self.player)
        stats = get_player_career_stats(self.player)

        self.assertEqual(history["teams_played_for"], 2)
        self.assertEqual(stats["teams_played_for"], 2)
        self.assertEqual(history["current_team"], self.team)
        self.assertEqual(
            {team.name for team in history["teams"]},
            {"Career Team", "Old Career Team"},
        )


class TeamHistoricalStatsServiceTests(TestCase):
    def test_team_historical_totals_aggregate_public_standings(self):
        team = make_team("History Team")
        active = make_tournament("Active History Cup", status=Tournament.ACTIVE)
        completed = make_tournament("Completed History Cup", status=Tournament.COMPLETED)
        archived = make_tournament("Archived History Cup", status=Tournament.ARCHIVED)
        draft = make_tournament("Draft History Cup", status=Tournament.DRAFT)

        Standing.objects.create(
            tournament=active,
            team=team,
            played=2,
            wins=1,
            draws=1,
            losses=0,
            goals_for=5,
            goals_against=3,
            goal_difference=2,
            points=4,
        )
        Standing.objects.create(
            tournament=completed,
            team=team,
            played=3,
            wins=2,
            draws=0,
            losses=1,
            goals_for=6,
            goals_against=4,
            goal_difference=2,
            points=6,
        )
        Standing.objects.create(
            tournament=archived,
            team=team,
            played=1,
            wins=0,
            draws=1,
            losses=0,
            goals_for=1,
            goals_against=1,
            goal_difference=0,
            points=1,
        )
        Standing.objects.create(
            tournament=draft,
            team=team,
            played=9,
            wins=9,
            draws=0,
            losses=0,
            goals_for=99,
            goals_against=0,
            goal_difference=99,
            points=27,
        )

        stats = get_team_historical_stats(team)

        self.assertEqual(stats["total_played"], 6)
        self.assertEqual(stats["total_wins"], 3)
        self.assertEqual(stats["total_draws"], 2)
        self.assertEqual(stats["total_losses"], 1)
        self.assertEqual(stats["total_goals_for"], 12)
        self.assertEqual(stats["total_goals_against"], 8)
        self.assertEqual(stats["total_goal_difference"], 4)
        self.assertEqual(stats["total_points"], 11)
        self.assertEqual(stats["win_rate"], 50.0)
        self.assertEqual(stats["tournaments_entered"], 3)
        self.assertEqual(len(stats["tournament_history"]), 3)
        self.assertIsNone(stats["championships"])

    def test_team_win_rate_handles_zero_games(self):
        team = make_team("Zero History Team")

        stats = get_team_historical_stats(team)

        self.assertEqual(stats["total_played"], 0)
        self.assertEqual(stats["win_rate"], 0)
        self.assertEqual(stats["tournaments_entered"], 0)


class HeadToHeadStatsServiceTests(TestCase):
    def test_head_to_head_uses_approved_non_draft_results_only(self):
        team_a = make_team("H2H Alpha")
        team_b = make_team("H2H Beta")
        tournament = make_tournament("H2H Cup")
        draft = make_tournament("Draft H2H Cup", status=Tournament.DRAFT)
        now = timezone.now()

        fixture_one = make_fixture(
            tournament,
            team_a,
            team_b,
            round_number=1,
            match_date=now - timedelta(days=3),
        )
        make_result(fixture_one, home_score=2, away_score=0, status=Result.APPROVED)

        fixture_two = make_fixture(
            tournament,
            team_b,
            team_a,
            round_number=2,
            match_date=now - timedelta(days=2),
        )
        make_result(fixture_two, home_score=1, away_score=1, status=Result.APPROVED)

        pending_fixture = make_fixture(
            tournament,
            team_a,
            team_b,
            round_number=3,
            match_date=now - timedelta(days=1),
        )
        make_result(pending_fixture, home_score=0, away_score=5, status=Result.PENDING)

        draft_fixture = make_fixture(draft, team_a, team_b, round_number=1)
        make_result(draft_fixture, home_score=7, away_score=0, status=Result.APPROVED)

        stats = get_head_to_head_stats(team_a, team_b)

        self.assertEqual(stats["meetings"], 2)
        self.assertEqual(stats["team_a_wins"], 1)
        self.assertEqual(stats["team_b_wins"], 0)
        self.assertEqual(stats["draws"], 1)
        self.assertEqual(stats["team_a_goals"], 3)
        self.assertEqual(stats["team_b_goals"], 1)
        self.assertEqual(len(stats["past_matches"]), 2)
        self.assertEqual(stats["past_matches"][0]["fixture"], fixture_two)
        self.assertEqual(stats["past_matches"][0]["score"], "1-1")
