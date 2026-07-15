import csv
import json
import zipfile
from io import BytesIO, StringIO

from django.db.models import Count, Q
from django.utils import timezone

from standings.models import PlayerStat, Standing
from tournament.models import Fixture, Result, Tournament
from tournament.player_stat_fields import (
    ADVANCED_PLAYER_STAT_FIELDS,
    DETAILED_INTEGER_PLAYER_STAT_FIELDS,
    PERCENTAGE_PLAYER_STAT_FIELDS,
    PLAYER_STAT_FIELD_LABELS,
)


PREDICTION_DATASET_PACKAGE_VERSION = "phase-4.2-v2"
PREDICTION_DATASET_PROJECT_LABEL = "Hosted by Tanvir"

PREDICTION_DETAILED_PLAYER_STAT_HEADERS = (
    ADVANCED_PLAYER_STAT_FIELDS
    + DETAILED_INTEGER_PLAYER_STAT_FIELDS
    + PERCENTAGE_PLAYER_STAT_FIELDS
)

PREDICTION_EVIDENCE_HEADERS = [
    "result_screenshot_url_or_path",
    "home_stats_screenshot_url_or_path",
    "away_stats_screenshot_url_or_path",
    "has_result_screenshot",
    "has_home_stats_screenshot",
    "has_away_stats_screenshot",
]

PREDICTION_AVAILABILITY_HEADERS = [
    "advanced_stats_available",
    "manual_stats_available",
    "screenshot_evidence_available",
]

PREDICTION_DETAILED_PLAYER_STAT_DESCRIPTIONS = {
    field_name: (
        "Official detailed PlayerStat value for "
        f"{PLAYER_STAT_FIELD_LABELS[field_name].lower()}."
    )
    for field_name in PREDICTION_DETAILED_PLAYER_STAT_HEADERS
}

PREDICTION_EVIDENCE_FIELD_DESCRIPTIONS = {
    "result_screenshot_url_or_path": (
        "Evidence reference exported from Result.screenshot; this may be a "
        "Cloudinary URL or stored media public path."
    ),
    "home_stats_screenshot_url_or_path": (
        "Evidence reference exported from Result.home_player_stats_screenshot; "
        "this maps to the home player-stat screenshot."
    ),
    "away_stats_screenshot_url_or_path": (
        "Evidence reference exported from Result.away_player_stats_screenshot; "
        "this maps to the away player-stat screenshot."
    ),
    "has_result_screenshot": "True when Result.screenshot is populated.",
    "has_home_stats_screenshot": (
        "True when Result.home_player_stats_screenshot is populated."
    ),
    "has_away_stats_screenshot": (
        "True when Result.away_player_stats_screenshot is populated."
    ),
}

PREDICTION_AVAILABILITY_FIELD_DESCRIPTIONS = {
    "advanced_stats_available": (
        "True when official detailed stat rows have relevant player-stat "
        "screenshot evidence; this flag does not inspect whether numeric "
        "advanced values are zero or non-zero."
    ),
    "manual_stats_available": (
        "True when approved manual official PlayerStat rows exist for the "
        "result or, in official_player_stats.csv, for the exported row."
    ),
    "screenshot_evidence_available": (
        "True when the relevant result or player-stat screenshot evidence "
        "reference exists."
    ),
}

APPROVED_RESULTS_HEADERS = [
    "tournament_id",
    "tournament_name",
    "tournament_status",
    "fixture_id",
    "fixture_round",
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
]

PLAYER_STATS_HEADERS = [
    "tournament_id",
    "tournament_name",
    "tournament_status",
    "fixture_id",
    "player_id",
    "player_name",
    "player_ign",
    "team_id",
    "team_name",
    "goals",
    "assists",
    "yellow_cards",
    "red_cards",
    "appearance_count",
    "fixture_date",
]

TEAM_STATS_HEADERS = [
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
]

HEAD_TO_HEAD_HEADERS = [
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
]

PREDICTION_APPROVED_RESULTS_HEADERS = [
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
    *PREDICTION_EVIDENCE_HEADERS,
    *PREDICTION_AVAILABILITY_HEADERS,
]

PREDICTION_FIXTURES_HEADERS = [
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
]

PREDICTION_TOURNAMENTS_HEADERS = [
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
]

PREDICTION_PLAYER_STATS_HEADERS = [
    "stat_id",
    "approved_result_id",
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
    *PREDICTION_DETAILED_PLAYER_STAT_HEADERS,
    "man_of_the_match",
    "appearance_count",
    *PREDICTION_EVIDENCE_HEADERS,
    *PREDICTION_AVAILABILITY_HEADERS,
]

PREDICTION_TEAM_STANDINGS_HEADERS = [
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
]

PREDICTION_HEAD_TO_HEAD_HEADERS = [
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
]

PREDICTION_DATASET_FILES = [
    ("approved_results.csv", PREDICTION_APPROVED_RESULTS_HEADERS),
    ("fixtures.csv", PREDICTION_FIXTURES_HEADERS),
    ("tournaments.csv", PREDICTION_TOURNAMENTS_HEADERS),
    ("official_player_stats.csv", PREDICTION_PLAYER_STATS_HEADERS),
    ("team_standings_history.csv", PREDICTION_TEAM_STANDINGS_HEADERS),
    ("head_to_head.csv", PREDICTION_HEAD_TO_HEAD_HEADERS),
]

PREDICTION_DATA_DICTIONARY = {
    "approved_results.csv": {
        "result_id": "Primary key of the approved Result row.",
        "tournament_id": "Primary key of the fixture's tournament.",
        "tournament_name": "Tournament name.",
        "tournament_status": "Tournament status; draft tournaments are excluded.",
        "fixture_id": "Primary key of the fixture the result belongs to.",
        "fixture_round": "Stored fixture round number.",
        "fixture_stage": "Stored fixture stage such as group, knockout, or final.",
        "fixture_group_label": "Optional stored group label for grouped fixtures.",
        "fixture_date": "Scheduled fixture date/time as ISO 8601 text when available.",
        "home_team_id": "Primary key of the fixture home team.",
        "home_team_name": "Fixture home team name.",
        "away_team_id": "Primary key of the fixture away team when available.",
        "away_team_name": "Fixture away team name when available.",
        "home_score": "Approved canonical fixture home-team score.",
        "away_score": "Approved canonical fixture away-team score.",
        "winner_team_id": "Winning team primary key, blank for draws.",
        "winner_team_name": "Winning team name, blank for draws.",
        "is_draw": "True when the approved score is level.",
        "approved_at": "Result review/approval timestamp as ISO 8601 text when available.",
        "created_at": "Result submission timestamp as ISO 8601 text.",
        **PREDICTION_EVIDENCE_FIELD_DESCRIPTIONS,
        **PREDICTION_AVAILABILITY_FIELD_DESCRIPTIONS,
    },
    "fixtures.csv": {
        "fixture_id": "Primary key of a fixture that has an approved result.",
        "tournament_id": "Primary key of the fixture's tournament.",
        "tournament_name": "Tournament name.",
        "tournament_status": "Tournament status; draft tournaments are excluded.",
        "tournament_type": "Tournament type stored on Tournament.",
        "tournament_format": "Tournament format stored on Tournament.",
        "fixture_round": "Stored fixture round number.",
        "fixture_stage": "Stored fixture stage such as group, knockout, or final.",
        "fixture_group_label": "Optional stored group label for grouped fixtures.",
        "fixture_date": "Scheduled fixture date/time as ISO 8601 text when available.",
        "submission_deadline": "Stored result submission deadline as ISO 8601 text when available.",
        "home_team_id": "Primary key of the fixture home team.",
        "home_team_name": "Fixture home team name.",
        "away_team_id": "Primary key of the fixture away team when available.",
        "away_team_name": "Fixture away team name when available.",
        "is_bye": "True when the fixture is a bye.",
        "approved_result_id": "Primary key of the approved Result row for this fixture.",
        "approved_result_reviewed_at": "Approved result review timestamp as ISO 8601 text when available.",
    },
    "tournaments.csv": {
        "tournament_id": "Primary key of a non-draft tournament represented in the package.",
        "tournament_name": "Tournament name.",
        "tournament_status": "Tournament status; draft tournaments are excluded.",
        "tournament_type": "Tournament type stored on Tournament.",
        "tournament_format": "Tournament format stored on Tournament.",
        "max_teams": "Configured maximum number of entrants.",
        "registration_deadline": "Registration deadline as ISO 8601 text when available.",
        "start_date": "Tournament start date as ISO 8601 text when available.",
        "end_date": "Tournament end date as ISO 8601 text when available.",
        "hybrid_qualifiers_per_group": "Configured hybrid qualifiers per group.",
        "tiebreaker_rules": "JSON text of stored tiebreaker rules.",
        "created_at": "Tournament creation timestamp as ISO 8601 text.",
        "updated_at": "Tournament update timestamp as ISO 8601 text.",
        "approved_result_count": "Count of approved result rows in this non-draft tournament.",
        "fixture_count_with_approved_results": "Count of fixtures in this tournament that have approved results.",
        "official_player_stat_row_count": "Count of official PlayerStat rows tied to approved results.",
        "standing_row_count": "Count of Standing rows for this tournament.",
    },
    "official_player_stats.csv": {
        "stat_id": "Primary key of the official PlayerStat row.",
        "approved_result_id": "Primary key of the approved Result row linked through this stat's fixture.",
        "tournament_id": "Primary key of the stat fixture's tournament.",
        "tournament_name": "Tournament name.",
        "tournament_status": "Tournament status; draft tournaments are excluded.",
        "fixture_id": "Primary key of the fixture the stat belongs to.",
        "fixture_date": "Scheduled fixture date/time as ISO 8601 text when available.",
        "player_id": "Primary key of the player.",
        "player_username": "Player username.",
        "player_ign": "Player in-game name when available.",
        "team_id": "Primary key of the team represented in this stat row.",
        "team_name": "Team name represented in this stat row.",
        "goals": "Official goals value.",
        "own_goals": "Official own-goals value.",
        "assists": "Official assists value.",
        "yellow_cards": "Official yellow-card value.",
        "red_cards": "Official red-card value.",
        **PREDICTION_DETAILED_PLAYER_STAT_DESCRIPTIONS,
        "man_of_the_match": "Stored official man-of-the-match flag.",
        "appearance_count": "Always 1 for each official PlayerStat row; zero-stat appearances can be missing.",
        **PREDICTION_EVIDENCE_FIELD_DESCRIPTIONS,
        **PREDICTION_AVAILABILITY_FIELD_DESCRIPTIONS,
    },
    "team_standings_history.csv": {
        "standing_id": "Primary key of the Standing row.",
        "tournament_id": "Primary key of the standing tournament.",
        "tournament_name": "Tournament name.",
        "tournament_status": "Tournament status; draft tournaments are excluded.",
        "team_id": "Primary key of the team.",
        "team_name": "Team name.",
        "played": "Standing matches played.",
        "wins": "Standing wins.",
        "draws": "Standing draws.",
        "losses": "Standing losses.",
        "goals_for": "Standing goals for.",
        "goals_against": "Standing goals against.",
        "goal_difference": "Standing goal difference.",
        "points": "Standing points.",
        "win_rate": "Wins divided by played, as a percentage rounded to one decimal place.",
        "last_updated": "Standing row update timestamp as ISO 8601 text.",
    },
    "head_to_head.csv": {
        "result_id": "Primary key of the approved Result row.",
        "tournament_id": "Primary key of the fixture tournament.",
        "tournament_name": "Tournament name.",
        "tournament_status": "Tournament status; draft tournaments are excluded.",
        "fixture_id": "Primary key of the fixture.",
        "fixture_date": "Scheduled fixture date/time as ISO 8601 text when available.",
        "team_a_id": "Primary key of fixture home team for this row.",
        "team_a_name": "Fixture home team name for this row.",
        "team_b_id": "Primary key of fixture away team for this row.",
        "team_b_name": "Fixture away team name for this row.",
        "team_a_goals": "Approved fixture home-team goals for this row.",
        "team_b_goals": "Approved fixture away-team goals for this row.",
        "winner_team_id": "Winning team primary key, blank for draws.",
        "winner_team_name": "Winning team name, blank for draws.",
        "is_draw": "True when the approved score is level.",
    },
}

PREDICTION_SOURCE_RULES = [
    "Only Result rows with status='approved' are exported as result truth.",
    "Draft tournaments are excluded from every CSV.",
    "Completed and archived tournaments remain included when their records are approved and non-draft.",
    "Official player stats come from standings.PlayerStat rows tied to fixtures with approved results.",
    "Submitted ResultPlayerStat rows are review-layer evidence and are not exported as official stats.",
    "Result screenshots and player-stat screenshots are exported as evidence pointers only, not as primary structured stat truth.",
    "OCR/Gemma extracted values must be treated as candidate interpretations and must not overwrite official stats without admin review.",
    "Sensitive account fields such as emails, credential hashes, staff flags, permissions, and internal account metadata are excluded.",
    "No championship count is inferred because Tournament has no canonical champion/winner field.",
]

PREDICTION_KNOWN_LIMITATIONS = [
    "Zero-stat player appearances are missing unless an official PlayerStat row exists.",
    "There is no canonical lineup, substitution, formation, or player-availability truth yet.",
    "There is no canonical Tournament champion/winner field, so championship counts are omitted.",
    "Team standings/history uses persisted Standing rows, which are not a full format-aware grouped standings model.",
    "The package does not include prediction snapshots, calibration records, or measured accuracy results.",
    "Unresolved fixtures are not exported as labeled training targets because they do not have approved results yet.",
    "Screenshots are treated as evidence for staff review, not as primary structured data.",
    "Zero values may be real zeros or default values; availability flags must be used to interpret missing advanced data.",
    "OCR/Gemma extraction is not part of the live web app and will run locally in a future/weekly processing step.",
]


def _iso(value):
    if value is None:
        return ""
    return value.isoformat()


def _media_url_or_path(value):
    if not value:
        return ""
    try:
        url = getattr(value, "url", "")
    except Exception:
        url = ""
    return str(url or value)


def _result_evidence_values(result):
    return {
        "result_screenshot_url_or_path": _media_url_or_path(result.screenshot),
        "home_stats_screenshot_url_or_path": _media_url_or_path(
            result.home_player_stats_screenshot
        ),
        "away_stats_screenshot_url_or_path": _media_url_or_path(
            result.away_player_stats_screenshot
        ),
        "has_result_screenshot": bool(result.screenshot),
        "has_home_stats_screenshot": bool(result.home_player_stats_screenshot),
        "has_away_stats_screenshot": bool(result.away_player_stats_screenshot),
    }


def _result_has_any_screenshot_evidence(result):
    return any(
        (
            bool(result.screenshot),
            bool(result.home_player_stats_screenshot),
            bool(result.away_player_stats_screenshot),
        )
    )


def _result_has_any_player_stats_screenshot(result):
    return any(
        (
            bool(result.home_player_stats_screenshot),
            bool(result.away_player_stats_screenshot),
        )
    )


def _team_stats_screenshot(result, team_id):
    fixture = result.fixture
    if team_id == fixture.home_team_id:
        return result.home_player_stats_screenshot
    if team_id == fixture.away_team_id:
        return result.away_player_stats_screenshot
    return None


def _winner_for_result(result):
    if result.home_score == result.away_score:
        return None
    if result.home_score > result.away_score:
        return result.fixture.home_team
    return result.fixture.away_team


def _win_rate(played, wins):
    if not played:
        return 0
    return round((wins / played) * 100, 1)


def approved_results_export_rows():
    results = (
        Result.objects
        .filter(status=Result.APPROVED)
        .exclude(fixture__tournament__status=Tournament.DRAFT)
        .select_related(
            "fixture",
            "fixture__tournament",
            "fixture__home_team",
            "fixture__away_team",
        )
        .order_by("fixture__tournament_id", "fixture_id", "pk")
    )

    rows = []
    for result in results:
        fixture = result.fixture
        tournament = fixture.tournament
        winner = _winner_for_result(result)
        rows.append({
            "tournament_id": tournament.pk,
            "tournament_name": tournament.name,
            "tournament_status": tournament.status,
            "fixture_id": fixture.pk,
            "fixture_round": fixture.round_number,
            "fixture_date": _iso(fixture.match_date),
            "home_team_id": fixture.home_team_id,
            "home_team_name": fixture.home_team.name,
            "away_team_id": fixture.away_team_id or "",
            "away_team_name": fixture.away_team.name if fixture.away_team_id else "",
            "home_score": result.home_score,
            "away_score": result.away_score,
            "winner_team_id": winner.pk if winner else "",
            "winner_team_name": winner.name if winner else "",
            "is_draw": result.home_score == result.away_score,
            "approved_at": _iso(result.reviewed_at),
            "created_at": _iso(result.submitted_at),
        })
    return rows


def player_stats_export_rows():
    stats = (
        PlayerStat.objects
        .filter(fixture__results__status=Result.APPROVED)
        .exclude(fixture__tournament__status=Tournament.DRAFT)
        .select_related("fixture", "fixture__tournament", "player", "team")
        .order_by("fixture__tournament_id", "fixture_id", "team_id", "player_id", "pk")
    )

    rows = []
    for stat in stats:
        fixture = stat.fixture
        tournament = fixture.tournament
        rows.append({
            "tournament_id": tournament.pk,
            "tournament_name": tournament.name,
            "tournament_status": tournament.status,
            "fixture_id": fixture.pk,
            "player_id": stat.player_id,
            "player_name": stat.player.username,
            "player_ign": stat.player.in_game_name,
            "team_id": stat.team_id,
            "team_name": stat.team.name,
            "goals": stat.goals,
            "assists": stat.assists,
            "yellow_cards": stat.yellow_cards,
            "red_cards": stat.red_cards,
            "appearance_count": 1,
            "fixture_date": _iso(fixture.match_date),
        })
    return rows


def team_stats_export_rows():
    standings = (
        Standing.objects
        .exclude(tournament__status=Tournament.DRAFT)
        .select_related("tournament", "team")
        .order_by("tournament_id", "team_id", "pk")
    )

    rows = []
    for standing in standings:
        rows.append({
            "tournament_id": standing.tournament_id,
            "tournament_name": standing.tournament.name,
            "tournament_status": standing.tournament.status,
            "team_id": standing.team_id,
            "team_name": standing.team.name,
            "played": standing.played,
            "wins": standing.wins,
            "draws": standing.draws,
            "losses": standing.losses,
            "goals_for": standing.goals_for,
            "goals_against": standing.goals_against,
            "goal_difference": standing.goal_difference,
            "points": standing.points,
            "win_rate": _win_rate(standing.played, standing.wins),
        })
    return rows


def head_to_head_export_rows():
    results = (
        Result.objects
        .filter(
            status=Result.APPROVED,
            fixture__is_bye=False,
            fixture__away_team__isnull=False,
        )
        .exclude(fixture__tournament__status=Tournament.DRAFT)
        .select_related(
            "fixture",
            "fixture__tournament",
            "fixture__home_team",
            "fixture__away_team",
        )
        .order_by("fixture__tournament_id", "fixture_id", "pk")
    )

    rows = []
    for result in results:
        fixture = result.fixture
        tournament = fixture.tournament
        winner = _winner_for_result(result)
        rows.append({
            "tournament_id": tournament.pk,
            "tournament_name": tournament.name,
            "tournament_status": tournament.status,
            "fixture_id": fixture.pk,
            "fixture_date": _iso(fixture.match_date),
            "team_a_id": fixture.home_team_id,
            "team_a_name": fixture.home_team.name,
            "team_b_id": fixture.away_team_id,
            "team_b_name": fixture.away_team.name,
            "team_a_goals": result.home_score,
            "team_b_goals": result.away_score,
            "winner_team_id": winner.pk if winner else "",
            "winner_team_name": winner.name if winner else "",
            "is_draw": result.home_score == result.away_score,
        })
    return rows


def _approved_result_queryset():
    return (
        Result.objects
        .filter(status=Result.APPROVED)
        .exclude(fixture__tournament__status=Tournament.DRAFT)
        .select_related(
            "fixture",
            "fixture__tournament",
            "fixture__home_team",
            "fixture__away_team",
        )
        .order_by("fixture__tournament_id", "fixture_id", "pk")
    )


def prediction_approved_results_export_rows():
    results = list(_approved_result_queryset())
    fixture_ids_with_official_stats = set(
        PlayerStat.objects
        .filter(fixture_id__in=[result.fixture_id for result in results])
        .values_list("fixture_id", flat=True)
    )

    rows = []
    for result in results:
        fixture = result.fixture
        tournament = fixture.tournament
        winner = _winner_for_result(result)
        manual_stats_available = fixture.pk in fixture_ids_with_official_stats
        rows.append({
            "result_id": result.pk,
            "tournament_id": tournament.pk,
            "tournament_name": tournament.name,
            "tournament_status": tournament.status,
            "fixture_id": fixture.pk,
            "fixture_round": fixture.round_number,
            "fixture_stage": fixture.stage,
            "fixture_group_label": fixture.group_label,
            "fixture_date": _iso(fixture.match_date),
            "home_team_id": fixture.home_team_id,
            "home_team_name": fixture.home_team.name,
            "away_team_id": fixture.away_team_id or "",
            "away_team_name": fixture.away_team.name if fixture.away_team_id else "",
            "home_score": result.home_score,
            "away_score": result.away_score,
            "winner_team_id": winner.pk if winner else "",
            "winner_team_name": winner.name if winner else "",
            "is_draw": result.home_score == result.away_score,
            "approved_at": _iso(result.reviewed_at),
            "created_at": _iso(result.submitted_at),
            **_result_evidence_values(result),
            "advanced_stats_available": (
                manual_stats_available
                and _result_has_any_player_stats_screenshot(result)
            ),
            "manual_stats_available": manual_stats_available,
            "screenshot_evidence_available": _result_has_any_screenshot_evidence(result),
        })
    return rows


def prediction_fixtures_export_rows():
    fixtures = (
        Fixture.objects
        .filter(results__status=Result.APPROVED)
        .exclude(tournament__status=Tournament.DRAFT)
        .select_related("tournament", "home_team", "away_team")
        .prefetch_related("results")
        .distinct()
        .order_by("tournament_id", "round_number", "match_date", "pk")
    )

    rows = []
    for fixture in fixtures:
        approved_result = (
            fixture.results
            .filter(status=Result.APPROVED)
            .order_by("-reviewed_at", "-submitted_at", "-pk")
            .first()
        )
        if approved_result is None:
            continue

        rows.append({
            "fixture_id": fixture.pk,
            "tournament_id": fixture.tournament_id,
            "tournament_name": fixture.tournament.name,
            "tournament_status": fixture.tournament.status,
            "tournament_type": fixture.tournament.tournament_type,
            "tournament_format": fixture.tournament.format,
            "fixture_round": fixture.round_number,
            "fixture_stage": fixture.stage,
            "fixture_group_label": fixture.group_label,
            "fixture_date": _iso(fixture.match_date),
            "submission_deadline": _iso(fixture.submission_deadline),
            "home_team_id": fixture.home_team_id,
            "home_team_name": fixture.home_team.name,
            "away_team_id": fixture.away_team_id or "",
            "away_team_name": fixture.away_team.name if fixture.away_team_id else "",
            "is_bye": fixture.is_bye,
            "approved_result_id": approved_result.pk,
            "approved_result_reviewed_at": _iso(approved_result.reviewed_at),
        })
    return rows


def _prediction_dataset_tournament_ids():
    result_ids = Result.objects.filter(
        status=Result.APPROVED,
    ).exclude(
        fixture__tournament__status=Tournament.DRAFT,
    ).values_list("fixture__tournament_id", flat=True)
    standing_ids = Standing.objects.exclude(
        tournament__status=Tournament.DRAFT,
    ).values_list("tournament_id", flat=True)
    stat_ids = PlayerStat.objects.filter(
        fixture__results__status=Result.APPROVED,
    ).exclude(
        fixture__tournament__status=Tournament.DRAFT,
    ).values_list("fixture__tournament_id", flat=True)
    return sorted({*result_ids, *standing_ids, *stat_ids})


def prediction_tournaments_export_rows():
    tournament_ids = _prediction_dataset_tournament_ids()
    tournaments = (
        Tournament.objects
        .filter(pk__in=tournament_ids)
        .annotate(
            approved_result_count=Count(
                "fixtures__results",
                filter=Q(fixtures__results__status=Result.APPROVED),
                distinct=True,
            ),
            fixture_count_with_approved_results=Count(
                "fixtures",
                filter=Q(fixtures__results__status=Result.APPROVED),
                distinct=True,
            ),
            official_player_stat_row_count=Count(
                "fixtures__player_stats",
                filter=Q(fixtures__results__status=Result.APPROVED),
                distinct=True,
            ),
            standing_row_count=Count("standings", distinct=True),
        )
        .order_by("pk")
    )

    rows = []
    for tournament in tournaments:
        rows.append({
            "tournament_id": tournament.pk,
            "tournament_name": tournament.name,
            "tournament_status": tournament.status,
            "tournament_type": tournament.tournament_type,
            "tournament_format": tournament.format,
            "max_teams": tournament.max_teams,
            "registration_deadline": _iso(tournament.registration_deadline),
            "start_date": _iso(tournament.start_date),
            "end_date": _iso(tournament.end_date),
            "hybrid_qualifiers_per_group": tournament.hybrid_qualifiers_per_group,
            "tiebreaker_rules": json.dumps(tournament.tiebreaker_rules, sort_keys=True),
            "created_at": _iso(tournament.created_at),
            "updated_at": _iso(tournament.updated_at),
            "approved_result_count": tournament.approved_result_count,
            "fixture_count_with_approved_results": tournament.fixture_count_with_approved_results,
            "official_player_stat_row_count": tournament.official_player_stat_row_count,
            "standing_row_count": tournament.standing_row_count,
        })
    return rows


def prediction_player_stats_export_rows():
    approved_result_by_fixture_id = {
        result.fixture_id: result
        for result in _approved_result_queryset()
    }
    stats = (
        PlayerStat.objects
        .filter(fixture_id__in=approved_result_by_fixture_id)
        .exclude(fixture__tournament__status=Tournament.DRAFT)
        .select_related("fixture", "fixture__tournament", "player", "team")
        .order_by("fixture__tournament_id", "fixture_id", "team_id", "player_id", "pk")
    )

    rows = []
    for stat in stats:
        fixture = stat.fixture
        tournament = fixture.tournament
        approved_result = approved_result_by_fixture_id[fixture.pk]
        team_stats_screenshot = _team_stats_screenshot(approved_result, stat.team_id)
        rows.append({
            "stat_id": stat.pk,
            "approved_result_id": approved_result.pk,
            "tournament_id": tournament.pk,
            "tournament_name": tournament.name,
            "tournament_status": tournament.status,
            "fixture_id": fixture.pk,
            "fixture_date": _iso(fixture.match_date),
            "player_id": stat.player_id,
            "player_username": stat.player.username,
            "player_ign": stat.player.in_game_name,
            "team_id": stat.team_id,
            "team_name": stat.team.name,
            "goals": stat.goals,
            "own_goals": stat.own_goals,
            "assists": stat.assists,
            "yellow_cards": stat.yellow_cards,
            "red_cards": stat.red_cards,
            **{
                field_name: getattr(stat, field_name)
                for field_name in PREDICTION_DETAILED_PLAYER_STAT_HEADERS
            },
            "man_of_the_match": stat.man_of_the_match,
            "appearance_count": 1,
            **_result_evidence_values(approved_result),
            "advanced_stats_available": bool(team_stats_screenshot),
            "manual_stats_available": True,
            "screenshot_evidence_available": (
                bool(approved_result.screenshot) or bool(team_stats_screenshot)
            ),
        })
    return rows


def prediction_team_standings_export_rows():
    standings = (
        Standing.objects
        .exclude(tournament__status=Tournament.DRAFT)
        .select_related("tournament", "team")
        .order_by("tournament_id", "team_id", "pk")
    )

    rows = []
    for standing in standings:
        rows.append({
            "standing_id": standing.pk,
            "tournament_id": standing.tournament_id,
            "tournament_name": standing.tournament.name,
            "tournament_status": standing.tournament.status,
            "team_id": standing.team_id,
            "team_name": standing.team.name,
            "played": standing.played,
            "wins": standing.wins,
            "draws": standing.draws,
            "losses": standing.losses,
            "goals_for": standing.goals_for,
            "goals_against": standing.goals_against,
            "goal_difference": standing.goal_difference,
            "points": standing.points,
            "win_rate": _win_rate(standing.played, standing.wins),
            "last_updated": _iso(standing.last_updated),
        })
    return rows


def prediction_head_to_head_export_rows():
    rows = []
    for result in _approved_result_queryset().filter(
        fixture__is_bye=False,
        fixture__away_team__isnull=False,
    ):
        fixture = result.fixture
        tournament = fixture.tournament
        winner = _winner_for_result(result)
        rows.append({
            "result_id": result.pk,
            "tournament_id": tournament.pk,
            "tournament_name": tournament.name,
            "tournament_status": tournament.status,
            "fixture_id": fixture.pk,
            "fixture_date": _iso(fixture.match_date),
            "team_a_id": fixture.home_team_id,
            "team_a_name": fixture.home_team.name,
            "team_b_id": fixture.away_team_id,
            "team_b_name": fixture.away_team.name,
            "team_a_goals": result.home_score,
            "team_b_goals": result.away_score,
            "winner_team_id": winner.pk if winner else "",
            "winner_team_name": winner.name if winner else "",
            "is_draw": result.home_score == result.away_score,
        })
    return rows


def prediction_dataset_export_rows_by_file():
    return {
        "approved_results.csv": prediction_approved_results_export_rows(),
        "fixtures.csv": prediction_fixtures_export_rows(),
        "tournaments.csv": prediction_tournaments_export_rows(),
        "official_player_stats.csv": prediction_player_stats_export_rows(),
        "team_standings_history.csv": prediction_team_standings_export_rows(),
        "head_to_head.csv": prediction_head_to_head_export_rows(),
    }


def _csv_text(headers, rows):
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            header: "" if row.get(header) is None else row.get(header)
            for header in headers
        })
    return output.getvalue()


def build_prediction_dataset_manifest(row_counts, generated_at=None):
    return {
        "generated_at": generated_at or timezone.now().isoformat(),
        "project_label": PREDICTION_DATASET_PROJECT_LABEL,
        "package_version": PREDICTION_DATASET_PACKAGE_VERSION,
        "schema_version": PREDICTION_DATASET_PACKAGE_VERSION,
        "row_counts": row_counts,
        "files": {
            filename: {
                "rows": row_counts[filename],
                "columns": headers,
            }
            for filename, headers in PREDICTION_DATASET_FILES
        },
        "source_rules": PREDICTION_SOURCE_RULES,
        "known_limitations": PREDICTION_KNOWN_LIMITATIONS,
    }


def build_prediction_dataset_dictionary():
    lines = [
        "# Hosted by Tanvir Prediction Dataset Data Dictionary",
        "",
        f"Package schema version: `{PREDICTION_DATASET_PACKAGE_VERSION}`",
        "",
        "Every CSV in the package uses a stable header order. Do not rename columns casually after Phase 4.2.",
        "",
    ]

    headers_by_filename = dict(PREDICTION_DATASET_FILES)
    for filename, headers in PREDICTION_DATASET_FILES:
        lines.extend([f"## {filename}", ""])
        descriptions = PREDICTION_DATA_DICTIONARY[filename]
        for header in headers:
            lines.append(f"- `{header}`: {descriptions[header]}")
        lines.append("")

    documented_columns = {
        filename: set(PREDICTION_DATA_DICTIONARY[filename])
        for filename in headers_by_filename
    }
    expected_columns = {
        filename: set(headers)
        for filename, headers in PREDICTION_DATASET_FILES
    }
    if documented_columns != expected_columns:
        raise ValueError("Prediction dataset data dictionary does not match headers.")

    return "\n".join(lines).rstrip() + "\n"


def build_prediction_dataset_package():
    rows_by_file = prediction_dataset_export_rows_by_file()
    row_counts = {
        filename: len(rows_by_file[filename])
        for filename, _headers in PREDICTION_DATASET_FILES
    }
    manifest = build_prediction_dataset_manifest(row_counts)
    files = {}

    for filename, headers in PREDICTION_DATASET_FILES:
        files[filename] = _csv_text(headers, rows_by_file[filename])

    files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    files["data_dictionary.md"] = build_prediction_dataset_dictionary()

    buffer = BytesIO()
    package_order = [
        filename for filename, _headers in PREDICTION_DATASET_FILES
    ] + ["manifest.json", "data_dictionary.md"]
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as package:
        for filename in package_order:
            package.writestr(filename, files[filename])

    return buffer.getvalue()
