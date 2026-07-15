from django.db.models import Prefetch, Q, Sum

from accounts.models import TeamMembership
from standings.models import PlayerStat, Standing
from tournament.models import Fixture, Result, Tournament, TournamentRegistration


PUBLIC_TOURNAMENT_STATUSES = (
    Tournament.REGISTRATION,
    Tournament.ACTIVE,
    Tournament.COMPLETED,
    Tournament.ARCHIVED,
)


def _player_stat_has_field(field_name):
    return any(field.name == field_name for field in PlayerStat._meta.get_fields())


def official_player_stat_rows(player=None, tournament=None):
    """
    Return public official player-stat rows.

    PlayerStat is the approved/published layer, but this query also requires an
    approved result on the fixture and a non-draft tournament so ad hoc rows or
    draft-tournament data do not leak into public career/history totals.
    """
    rows = PlayerStat.objects.filter(
        fixture__results__status=Result.APPROVED,
        fixture__tournament__status__in=PUBLIC_TOURNAMENT_STATUSES,
    )
    if player is not None:
        rows = rows.filter(player=player)
    if tournament is not None:
        rows = rows.filter(fixture__tournament=tournament)
    return rows


def _aggregate_player_rows(rows):
    aggregates = {
        "total_goals": Sum("goals"),
        "total_yellow_cards": Sum("yellow_cards"),
        "total_red_cards": Sum("red_cards"),
    }
    has_assists = _player_stat_has_field("assists")
    if has_assists:
        aggregates["total_assists"] = Sum("assists")

    totals = rows.aggregate(**aggregates)
    data = {
        "total_goals": totals["total_goals"] or 0,
        "total_yellow_cards": totals["total_yellow_cards"] or 0,
        "total_red_cards": totals["total_red_cards"] or 0,
        "appearances": rows.count(),
        "has_assists": has_assists,
    }
    data["total_assists"] = (totals.get("total_assists") or 0) if has_assists else None
    return data


def get_player_team_history(player, *, public=True):
    """
    Return the player's team-membership history.

    Public history is intentionally limited to approved teams. Appearances still
    come from PlayerStat rows; TeamMembership only answers which teams the player
    has represented in roster history.
    """
    memberships = TeamMembership.objects.filter(player=player).select_related("team")
    if public:
        memberships = memberships.filter(team__is_approved=True)
    memberships = memberships.order_by("-is_active", "-joined_at", "team__name")

    history = list(memberships)
    current_membership = next((item for item in history if item.is_active), None)
    teams_by_id = {}
    for membership in history:
        teams_by_id.setdefault(membership.team_id, membership.team)

    return {
        "memberships": history,
        "teams": list(teams_by_id.values()),
        "teams_played_for": len(teams_by_id),
        "current_membership": current_membership,
        "current_team": current_membership.team if current_membership else None,
    }


def get_player_current_tournament_stats(player, tournament=None):
    """
    Return approved stats for a player's current tournament.

    If no tournament is supplied, the current tournament is the latest active,
    non-draft tournament where the player has an official PlayerStat row. This
    misses active tournaments where the player has not yet produced an official
    PlayerStat row, including zero-stat appearances that were never recorded.
    """
    rows = official_player_stat_rows(player=player)

    if tournament is not None:
        if tournament.status not in PUBLIC_TOURNAMENT_STATUSES:
            return None
    else:
        tournament_id = (
            rows.filter(fixture__tournament__status=Tournament.ACTIVE)
            .order_by(
                "-fixture__tournament__start_date",
                "-fixture__tournament__pk",
            )
            .values_list("fixture__tournament_id", flat=True)
            .first()
        )
        if tournament_id is None:
            return None
        tournament = Tournament.objects.get(pk=tournament_id)

    tournament_rows = rows.filter(fixture__tournament=tournament)
    stats = _aggregate_player_rows(tournament_rows)
    stats.update({
        "tournament": tournament,
        "has_stats": tournament_rows.exists(),
    })
    return stats


def get_player_career_stats(player):
    rows = official_player_stat_rows(player=player)
    team_history = get_player_team_history(player)
    career_stats = _aggregate_player_rows(rows)
    career_stats.update({
        "tournaments_played": (
            rows.order_by()
            .values("fixture__tournament_id")
            .distinct()
            .count()
        ),
        "teams_played_for": team_history["teams_played_for"],
        "teams_played_for_list": team_history["teams"],
        "team_history": team_history["memberships"],
        "current_membership": team_history["current_membership"],
        "current_team": team_history["current_team"],
        "current_tournament_stats": get_player_current_tournament_stats(player),
    })
    return career_stats


def _standing_win_rate(played, wins):
    if not played:
        return 0
    return round((wins / played) * 100, 1)


def get_team_tournament_history(team):
    standings = (
        Standing.objects
        .filter(
            team=team,
            tournament__status__in=PUBLIC_TOURNAMENT_STATUSES,
        )
        .select_related("tournament")
        .order_by(
            "-tournament__start_date",
            "-tournament__end_date",
            "tournament__name",
        )
    )

    return [
        {
            "standing": standing,
            "tournament": standing.tournament,
            "played": standing.played,
            "wins": standing.wins,
            "draws": standing.draws,
            "losses": standing.losses,
            "goals_for": standing.goals_for,
            "goals_against": standing.goals_against,
            "goal_difference": standing.goal_difference,
            "points": standing.points,
            "win_rate": _standing_win_rate(standing.played, standing.wins),
        }
        for standing in standings
    ]


def _team_entered_tournament_ids(team):
    registration_ids = set(
        TournamentRegistration.objects.filter(
            team=team,
            is_active=True,
            tournament__status__in=PUBLIC_TOURNAMENT_STATUSES,
        ).values_list("tournament_id", flat=True)
    )
    standing_ids = set(
        Standing.objects.filter(
            team=team,
            tournament__status__in=PUBLIC_TOURNAMENT_STATUSES,
        ).values_list("tournament_id", flat=True)
    )
    fixture_ids = set(
        Fixture.objects.filter(
            Q(home_team=team) | Q(away_team=team),
            is_bye=False,
            tournament__status__in=PUBLIC_TOURNAMENT_STATUSES,
        ).values_list("tournament_id", flat=True)
    )
    return registration_ids | standing_ids | fixture_ids


def get_team_historical_stats(team):
    standings = Standing.objects.filter(
        team=team,
        tournament__status__in=PUBLIC_TOURNAMENT_STATUSES,
    )
    totals = standings.aggregate(
        total_played=Sum("played"),
        total_wins=Sum("wins"),
        total_draws=Sum("draws"),
        total_losses=Sum("losses"),
        total_goals_for=Sum("goals_for"),
        total_goals_against=Sum("goals_against"),
        total_goal_difference=Sum("goal_difference"),
        total_points=Sum("points"),
    )
    data = {key: value or 0 for key, value in totals.items()}
    data.update({
        "tournaments_entered": len(_team_entered_tournament_ids(team)),
        "win_rate": _standing_win_rate(
            data["total_played"],
            data["total_wins"],
        ),
        "tournament_history": get_team_tournament_history(team),
        "championships": None,
    })
    return data


def get_head_to_head_stats(team_a, team_b):
    stats = {
        "team_a": team_a,
        "team_b": team_b,
        "meetings": 0,
        "team_a_wins": 0,
        "team_b_wins": 0,
        "draws": 0,
        "team_a_goals": 0,
        "team_b_goals": 0,
        "past_matches": [],
    }
    if team_a.pk == team_b.pk:
        return stats

    approved_results = Result.objects.filter(status=Result.APPROVED).order_by(
        "-reviewed_at",
        "-submitted_at",
        "-pk",
    )
    fixtures = (
        Fixture.objects
        .filter(
            (
                Q(home_team=team_a, away_team=team_b)
                | Q(home_team=team_b, away_team=team_a)
            ),
            is_bye=False,
            tournament__status__in=PUBLIC_TOURNAMENT_STATUSES,
        )
        .select_related("tournament", "home_team", "away_team")
        .prefetch_related(
            Prefetch("results", queryset=approved_results, to_attr="approved_results")
        )
        .order_by("-match_date", "-pk")
    )

    for fixture in fixtures:
        result = fixture.approved_results[0] if fixture.approved_results else None
        if result is None:
            continue

        if fixture.home_team_id == team_a.pk:
            team_a_goals = result.home_score
            team_b_goals = result.away_score
        else:
            team_a_goals = result.away_score
            team_b_goals = result.home_score

        stats["meetings"] += 1
        stats["team_a_goals"] += team_a_goals
        stats["team_b_goals"] += team_b_goals
        if team_a_goals > team_b_goals:
            stats["team_a_wins"] += 1
        elif team_b_goals > team_a_goals:
            stats["team_b_wins"] += 1
        else:
            stats["draws"] += 1

        stats["past_matches"].append({
            "tournament": fixture.tournament,
            "fixture": fixture,
            "home_score": result.home_score,
            "away_score": result.away_score,
            "team_a_goals": team_a_goals,
            "team_b_goals": team_b_goals,
            "score": f"{team_a_goals}-{team_b_goals}",
            "fixture_score": f"{result.home_score}-{result.away_score}",
            "date": fixture.match_date,
            "status": result.status,
        })

    return stats
