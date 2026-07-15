from django.db.models import Count, Prefetch, Q, Sum

from standings.services import (
    PUBLIC_TOURNAMENT_STATUSES,
    get_head_to_head_stats,
    get_team_historical_stats,
    official_player_stat_rows,
)
from tournament.models import Fixture, Result, Tournament


NEUTRAL_RATING = 0.5
RECENT_MATCH_LIMIT = 5
PREDICTABLE_FIXTURE_STATUSES = (
    Tournament.REGISTRATION,
    Tournament.ACTIVE,
)


def _clamp(value, lower, upper):
    return max(lower, min(upper, value))


def _approved_results_queryset():
    return (
        Result.objects
        .filter(status=Result.APPROVED)
        .order_by("-reviewed_at", "-submitted_at", "-pk")
    )


def _approved_team_match_rows(team, *, tournament=None, exclude_fixture_id=None, limit=None):
    if team is None:
        return []

    fixtures = (
        Fixture.objects
        .filter(
            Q(home_team=team) | Q(away_team=team),
            is_bye=False,
            away_team__isnull=False,
            tournament__tournament_type=Tournament.TEAM,
            tournament__status__in=PUBLIC_TOURNAMENT_STATUSES,
        )
        .select_related("tournament", "home_team", "away_team")
        .prefetch_related(
            Prefetch(
                "results",
                queryset=_approved_results_queryset(),
                to_attr="_prediction_approved_results",
            )
        )
        .order_by("-match_date", "-pk")
    )
    if tournament is not None:
        fixtures = fixtures.filter(tournament=tournament)
    if exclude_fixture_id:
        fixtures = fixtures.exclude(pk=exclude_fixture_id)

    rows = []
    for fixture in fixtures:
        result = fixture._prediction_approved_results[0] if fixture._prediction_approved_results else None
        if result is None:
            continue

        if fixture.home_team_id == team.pk:
            goals_for = result.home_score
            goals_against = result.away_score
        else:
            goals_for = result.away_score
            goals_against = result.home_score

        if goals_for > goals_against:
            points = 3
            outcome = "win"
        elif goals_for == goals_against:
            points = 1
            outcome = "draw"
        else:
            points = 0
            outcome = "loss"

        rows.append({
            "fixture": fixture,
            "result": result,
            "goals_for": goals_for,
            "goals_against": goals_against,
            "goal_difference": goals_for - goals_against,
            "points": points,
            "outcome": outcome,
        })
        if limit and len(rows) >= limit:
            break

    return rows


def _rating_from_totals(*, played, points, goals_for, goals_against):
    if not played:
        return NEUTRAL_RATING

    points_score = _clamp((points / played) / 3, 0, 1)
    goal_difference_per_match = (goals_for - goals_against) / played
    goal_score = _clamp(0.5 + (goal_difference_per_match / 6), 0, 1)
    return _clamp((points_score * 0.7) + (goal_score * 0.3), 0, 1)


def _summarize_match_rows(rows):
    played = len(rows)
    wins = sum(1 for row in rows if row["outcome"] == "win")
    draws = sum(1 for row in rows if row["outcome"] == "draw")
    losses = sum(1 for row in rows if row["outcome"] == "loss")
    goals_for = sum(row["goals_for"] for row in rows)
    goals_against = sum(row["goals_against"] for row in rows)
    points = sum(row["points"] for row in rows)
    rating = _rating_from_totals(
        played=played,
        points=points,
        goals_for=goals_for,
        goals_against=goals_against,
    )

    return {
        "played": played,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "points": points,
        "rating": rating,
        "points_per_match": (points / played) if played else None,
        "goals_for_per_match": (goals_for / played) if played else None,
        "goals_against_per_match": (goals_against / played) if played else None,
    }


def _summarize_historical_strength(team):
    stats = get_team_historical_stats(team)
    played = stats["total_played"]
    rating = _rating_from_totals(
        played=played,
        points=stats["total_points"],
        goals_for=stats["total_goals_for"],
        goals_against=stats["total_goals_against"],
    )
    return {
        "played": played,
        "wins": stats["total_wins"],
        "draws": stats["total_draws"],
        "losses": stats["total_losses"],
        "goals_for": stats["total_goals_for"],
        "goals_against": stats["total_goals_against"],
        "points": stats["total_points"],
        "rating": rating,
        "points_per_match": (stats["total_points"] / played) if played else None,
        "goals_for_per_match": (stats["total_goals_for"] / played) if played else None,
        "goals_against_per_match": (stats["total_goals_against"] / played) if played else None,
    }


def _global_approved_goal_average():
    totals = (
        Result.objects
        .filter(
            status=Result.APPROVED,
            fixture__is_bye=False,
            fixture__away_team__isnull=False,
            fixture__tournament__tournament_type=Tournament.TEAM,
            fixture__tournament__status__in=PUBLIC_TOURNAMENT_STATUSES,
        )
        .aggregate(
            result_count=Count("pk"),
            home_goals=Sum("home_score"),
            away_goals=Sum("away_score"),
        )
    )
    result_count = totals["result_count"] or 0
    if not result_count:
        return None
    total_goals = (totals["home_goals"] or 0) + (totals["away_goals"] or 0)
    return total_goals / (result_count * 2)


def _goal_rate(current_summary, historical_summary, key, global_average):
    weighted_rates = []
    if current_summary["played"]:
        weighted_rates.append((current_summary[key], 0.6))
    if historical_summary["played"]:
        weighted_rates.append((historical_summary[key], 0.4))

    if not weighted_rates:
        return global_average

    weighted_total = sum(value * weight for value, weight in weighted_rates if value is not None)
    weight_total = sum(weight for value, weight in weighted_rates if value is not None)
    if not weight_total:
        return global_average
    return weighted_total / weight_total


def _official_player_threat(team):
    scoring_rows = official_player_stat_rows().filter(
        team=team,
        fixture__tournament__tournament_type=Tournament.TEAM,
    ).filter(
        Q(goals__gt=0) | Q(assists__gt=0)
    )
    totals = scoring_rows.aggregate(
        goals=Sum("goals"),
        assists=Sum("assists"),
        scoring_rows=Count("pk"),
        scoring_fixtures=Count("fixture", distinct=True),
    )
    goals = totals["goals"] or 0
    assists = totals["assists"] or 0
    row_count = totals["scoring_rows"] or 0
    fixture_count = totals["scoring_fixtures"] or 0

    if not row_count or not fixture_count:
        return {
            "rating": NEUTRAL_RATING,
            "goals": 0,
            "assists": 0,
            "scoring_rows": 0,
            "scoring_fixtures": 0,
            "has_data": False,
        }

    threat_per_scoring_fixture = (goals + (assists * 0.6)) / fixture_count
    rating = _clamp(threat_per_scoring_fixture / 3, 0, 1)
    return {
        "rating": rating,
        "goals": goals,
        "assists": assists,
        "scoring_rows": row_count,
        "scoring_fixtures": fixture_count,
        "has_data": True,
    }


def _head_to_head_factor(home_team, away_team):
    stats = get_head_to_head_stats(home_team, away_team)
    meetings = stats["meetings"]
    if not meetings:
        return stats, NEUTRAL_RATING, NEUTRAL_RATING

    home_points = (stats["team_a_wins"] * 3) + stats["draws"]
    away_points = (stats["team_b_wins"] * 3) + stats["draws"]
    home_points_score = home_points / (meetings * 3)
    away_points_score = away_points / (meetings * 3)
    goal_difference_per_meeting = (stats["team_a_goals"] - stats["team_b_goals"]) / meetings
    home_goal_score = _clamp(0.5 + (goal_difference_per_meeting / 6), 0, 1)
    away_goal_score = _clamp(0.5 - (goal_difference_per_meeting / 6), 0, 1)
    return (
        stats,
        _clamp((home_points_score * 0.7) + (home_goal_score * 0.3), 0, 1),
        _clamp((away_points_score * 0.7) + (away_goal_score * 0.3), 0, 1),
    )


def _factor(label, weight, home_rating, away_rating, data_points, summary):
    return {
        "label": label,
        "weight": weight,
        "home_rating": round(home_rating, 3),
        "away_rating": round(away_rating, 3),
        "advantage": round(home_rating - away_rating, 3),
        "data_points": data_points,
        "summary": summary,
    }


def _probabilities_from_edge(edge, confidence):
    moderated_edge = _clamp(edge * (0.55 + (confidence * 0.45)), -0.65, 0.65)
    draw_probability = _clamp(28 - (abs(moderated_edge) * 18), 18, 34)
    non_draw_pool = 100 - draw_probability
    home_probability = non_draw_pool * (0.5 + (moderated_edge * 0.5))
    away_probability = non_draw_pool - home_probability

    home_probability = _clamp(home_probability, 5, 90)
    away_probability = _clamp(away_probability, 5, 90)
    draw_probability = _clamp(draw_probability, 5, 45)

    total = home_probability + away_probability + draw_probability
    home_probability = round((home_probability / total) * 100, 1)
    draw_probability = round((draw_probability / total) * 100, 1)
    away_probability = round(100 - home_probability - draw_probability, 1)
    return home_probability, draw_probability, away_probability, moderated_edge


def _score_label(home_goals, away_goals, home_probability, draw_probability, away_probability):
    home_score = max(0, round(home_goals))
    away_score = max(0, round(away_goals))

    if home_probability > away_probability and home_probability > draw_probability and home_score <= away_score:
        home_score = away_score + 1
    elif away_probability > home_probability and away_probability > draw_probability and away_score <= home_score:
        away_score = home_score + 1
    elif draw_probability >= home_probability and draw_probability >= away_probability:
        shared_score = round((home_goals + away_goals) / 2)
        home_score = shared_score
        away_score = shared_score

    return f"{home_score}-{away_score}"


def _unavailable(fixture, reason):
    return {
        "is_available": False,
        "unavailable_reason": reason,
        "home_team": getattr(fixture, "home_team", None),
        "away_team": getattr(fixture, "away_team", None),
        "home_win_probability": None,
        "away_win_probability": None,
        "draw_probability": None,
        "expected_home_goals": None,
        "expected_away_goals": None,
        "predicted_score_label": None,
        "explanation_lines": [],
        "factors": {},
    }


def get_fixture_prediction(fixture):
    """
    Return a deterministic, CPU-only fixture prediction dictionary.

    The helper deliberately uses approved, non-draft data only. Missing factor
    data stays neutral, and the service returns unavailable when there is no
    approved history for either team.
    """
    if fixture is None:
        return _unavailable(fixture, "Prediction needs a fixture.")
    if getattr(fixture, "is_bye", False):
        return _unavailable(fixture, "Prediction is not available for bye fixtures.")
    if not getattr(fixture, "home_team_id", None) or not getattr(fixture, "away_team_id", None):
        return _unavailable(fixture, "Prediction needs both teams.")
    if fixture.tournament.status == Tournament.DRAFT:
        return _unavailable(fixture, "Prediction is not available for draft tournaments.")
    if fixture.tournament.status not in PREDICTABLE_FIXTURE_STATUSES:
        return _unavailable(fixture, "Prediction is only available for upcoming or active fixtures.")
    if fixture.tournament.tournament_type != Tournament.TEAM:
        return _unavailable(fixture, "Prediction is only available for team tournaments.")
    if fixture.results.filter(status=Result.APPROVED).exists():
        return _unavailable(fixture, "Prediction is not available once an official result is approved.")

    home_team = fixture.home_team
    away_team = fixture.away_team
    global_goal_average = _global_approved_goal_average()
    if global_goal_average is None:
        return _unavailable(
            fixture,
            "Prediction needs at least one approved non-draft historical result.",
        )

    home_current = _summarize_match_rows(
        _approved_team_match_rows(
            home_team,
            tournament=fixture.tournament,
            exclude_fixture_id=fixture.pk,
            limit=RECENT_MATCH_LIMIT,
        )
    )
    away_current = _summarize_match_rows(
        _approved_team_match_rows(
            away_team,
            tournament=fixture.tournament,
            exclude_fixture_id=fixture.pk,
            limit=RECENT_MATCH_LIMIT,
        )
    )
    home_history_rows = _approved_team_match_rows(home_team, exclude_fixture_id=fixture.pk)
    away_history_rows = _approved_team_match_rows(away_team, exclude_fixture_id=fixture.pk)
    home_history = _summarize_historical_strength(home_team)
    away_history = _summarize_historical_strength(away_team)

    if not home_history_rows and not away_history_rows:
        return _unavailable(
            fixture,
            "Prediction needs approved non-draft history for at least one fixture team.",
        )

    h2h_stats, home_h2h_rating, away_h2h_rating = _head_to_head_factor(home_team, away_team)
    home_threat = _official_player_threat(home_team)
    away_threat = _official_player_threat(away_team)

    current_summary = (
        f"Current form: {home_team.name} "
        f"{home_current['points_per_match']:.1f} PPM over {home_current['played']} match(es), "
        f"{away_team.name} {away_current['points_per_match']:.1f} PPM over {away_current['played']} match(es)."
        if home_current["played"] and away_current["played"]
        else "Current form is thin in this tournament, so missing current-form data stays neutral."
    )
    history_summary = (
        f"Historical strength: {home_team.name} "
        f"{home_history['points_per_match']:.1f} PPM over {home_history['played']} match(es), "
        f"{away_team.name} {away_history['points_per_match']:.1f} PPM over {away_history['played']} match(es)."
        if home_history["played"] and away_history["played"]
        else "Historical strength is thin for one side, so the missing side stays near neutral."
    )
    h2h_summary = (
        f"Head-to-head: {h2h_stats['meetings']} approved meeting(s), "
        f"{home_team.name} {h2h_stats['team_a_wins']} win(s), "
        f"{away_team.name} {h2h_stats['team_b_wins']} win(s), "
        f"{h2h_stats['draws']} draw(s)."
        if h2h_stats["meetings"]
        else "Head-to-head has no approved meetings yet, so it stays neutral."
    )
    player_summary = (
        f"Player threat uses official scoring rows only: {home_team.name} "
        f"{home_threat['goals']} goal(s), {home_threat['assists']} assist(s); "
        f"{away_team.name} {away_threat['goals']} goal(s), {away_threat['assists']} assist(s)."
        if home_threat["has_data"] or away_threat["has_data"]
        else "No official scoring or assist PlayerStat rows are available; zero-stat appearances are not assumed."
    )

    factors = {
        "current_form": _factor(
            "Current Form",
            0.30,
            home_current["rating"],
            away_current["rating"],
            home_current["played"] + away_current["played"],
            current_summary,
        ),
        "historical_strength": _factor(
            "Historical Strength",
            0.35,
            home_history["rating"],
            away_history["rating"],
            home_history["played"] + away_history["played"],
            history_summary,
        ),
        "head_to_head": _factor(
            "Head To Head",
            0.20,
            home_h2h_rating,
            away_h2h_rating,
            h2h_stats["meetings"],
            h2h_summary,
        ),
        "player_threat": _factor(
            "Player Threat",
            0.15,
            home_threat["rating"],
            away_threat["rating"],
            home_threat["scoring_rows"] + away_threat["scoring_rows"],
            player_summary,
        ),
    }
    factors["player_threat"].update({
        "home_official_goals": home_threat["goals"],
        "away_official_goals": away_threat["goals"],
        "home_official_assists": home_threat["assists"],
        "away_official_assists": away_threat["assists"],
        "home_scoring_rows": home_threat["scoring_rows"],
        "away_scoring_rows": away_threat["scoring_rows"],
    })

    raw_edge = sum(
        factor["advantage"] * factor["weight"]
        for factor in factors.values()
    )
    data_points = (
        len(home_history_rows)
        + len(away_history_rows)
        + h2h_stats["meetings"]
        + home_threat["scoring_rows"]
        + away_threat["scoring_rows"]
    )
    confidence = _clamp(data_points / 12, 0, 1)
    if len(home_history_rows) + len(away_history_rows) < 4:
        raw_edge *= 0.75
    home_probability, draw_probability, away_probability, moderated_edge = _probabilities_from_edge(
        raw_edge,
        confidence,
    )

    home_attack = _goal_rate(
        home_current,
        _summarize_match_rows(home_history_rows),
        "goals_for_per_match",
        global_goal_average,
    )
    away_defense = _goal_rate(
        away_current,
        _summarize_match_rows(away_history_rows),
        "goals_against_per_match",
        global_goal_average,
    )
    away_attack = _goal_rate(
        away_current,
        _summarize_match_rows(away_history_rows),
        "goals_for_per_match",
        global_goal_average,
    )
    home_defense = _goal_rate(
        home_current,
        _summarize_match_rows(home_history_rows),
        "goals_against_per_match",
        global_goal_average,
    )
    expected_home_goals = _clamp(((home_attack * 0.62) + (away_defense * 0.38)) + (moderated_edge * 0.35), 0.2, 5.0)
    expected_away_goals = _clamp(((away_attack * 0.62) + (home_defense * 0.38)) - (moderated_edge * 0.35), 0.2, 5.0)

    explanation_lines = [
        "Transparent formula using approved non-draft results only.",
        current_summary,
        history_summary,
        h2h_summary,
        player_summary,
    ]
    if len(home_history_rows) + len(away_history_rows) < 4:
        explanation_lines.append("Data is thin, so probabilities are kept conservative.")

    return {
        "is_available": True,
        "unavailable_reason": None,
        "home_team": home_team,
        "away_team": away_team,
        "home_win_probability": home_probability,
        "away_win_probability": away_probability,
        "draw_probability": draw_probability,
        "expected_home_goals": round(expected_home_goals, 2),
        "expected_away_goals": round(expected_away_goals, 2),
        "predicted_score_label": _score_label(
            expected_home_goals,
            expected_away_goals,
            home_probability,
            draw_probability,
            away_probability,
        ),
        "explanation_lines": explanation_lines,
        "factors": factors,
    }
