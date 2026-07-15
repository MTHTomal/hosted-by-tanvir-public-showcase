#!/usr/bin/env python
"""Run local baseline predictions from a prediction dataset ZIP.

This script is intentionally local-only. It does not import Django, read
application settings, use a database, call external services, or require GPU
tooling. The output is a deterministic formula baseline, not AI or ML.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


FORMULA_VERSION = "phase-4.3-local-baseline-v1"
SCRIPT_NAME = "local_tools/run_prediction_baseline.py"

REQUIRED_CSV_FILES = [
    "approved_results.csv",
    "fixtures.csv",
    "tournaments.csv",
    "official_player_stats.csv",
    "team_standings_history.csv",
    "head_to_head.csv",
]
REQUIRED_FILES = [*REQUIRED_CSV_FILES, "manifest.json", "data_dictionary.md"]

BASELINE_PREDICTION_HEADERS = [
    "fixture_id",
    "tournament_id",
    "home_team",
    "away_team",
    "home_win_probability",
    "draw_probability",
    "away_win_probability",
    "expected_home_goals",
    "expected_away_goals",
    "predicted_score_label",
    "confidence_label",
    "explanation",
]

MINIMUM_COLUMNS_BY_FILE = {
    "approved_results.csv": {
        "fixture_id",
        "tournament_id",
        "home_team_id",
        "home_team_name",
        "away_team_id",
        "away_team_name",
        "home_score",
        "away_score",
    },
    "fixtures.csv": {
        "fixture_id",
        "tournament_id",
        "tournament_status",
        "tournament_type",
        "home_team_id",
        "home_team_name",
        "away_team_id",
        "away_team_name",
        "is_bye",
        "approved_result_id",
    },
    "official_player_stats.csv": {
        "fixture_id",
        "team_id",
        "goals",
        "assists",
    },
    "team_standings_history.csv": {
        "team_id",
        "played",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
        "points",
    },
    "head_to_head.csv": {
        "fixture_id",
        "team_a_id",
        "team_b_id",
        "team_a_goals",
        "team_b_goals",
        "is_draw",
    },
}

PREDICTABLE_TOURNAMENT_STATUSES = {"registration", "active"}
HISTORICAL_BACKTEST_STATUSES = {"registration", "active", "completed", "archived"}
TEAM_TOURNAMENT_TYPE = "team"
NEUTRAL_RATING = 0.5
RECENT_MATCH_LIMIT = 5
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


class BaselineError(Exception):
    """Raised when the package cannot be consumed safely."""


@dataclass(frozen=True)
class PackageData:
    source_path: Path
    manifest: dict
    rows_by_file: dict[str, list[dict[str, str]]]
    headers_by_file: dict[str, list[str]]
    warnings: list[str]


@dataclass(frozen=True)
class BaselineRunResult:
    output_dir: Path
    output_files: dict[str, Path]
    eligible_fixture_count: int
    prediction_count: int
    evaluated_historical_count: int
    warnings: list[str]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _clean_id(value: object) -> str:
    return str(value or "").strip()


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _safe_output(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return EMAIL_RE.sub("[redacted-email]", text)


def _parse_timestamp(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _result_sort_key(row: dict[str, str]) -> tuple[float, float, float, int]:
    return (
        _parse_timestamp(row.get("fixture_date")),
        _parse_timestamp(row.get("approved_at")),
        _parse_timestamp(row.get("created_at")),
        _safe_int(row.get("fixture_id")),
    )


def _read_text(package: zipfile.ZipFile, filename: str) -> str:
    return package.read(filename).decode("utf-8-sig")


def _read_csv(package: zipfile.ZipFile, filename: str) -> tuple[list[str], list[dict[str, str]]]:
    text = _read_text(package, filename)
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise BaselineError(f"{filename} is missing a header row.")
    return reader.fieldnames, list(reader)


def load_package(zip_path: str | Path) -> PackageData:
    source_path = Path(zip_path).expanduser()
    if not source_path.exists():
        raise BaselineError(f"Package does not exist: {source_path}")

    warnings: list[str] = []
    rows_by_file: dict[str, list[dict[str, str]]] = {}
    headers_by_file: dict[str, list[str]] = {}
    manifest: dict = {}

    try:
        with zipfile.ZipFile(source_path) as package:
            files = {name for name in package.namelist() if not name.endswith("/")}
            missing_files = [filename for filename in REQUIRED_FILES if filename not in files]
            if missing_files:
                raise BaselineError(
                    "Missing required package file(s): " + ", ".join(missing_files)
                )

            try:
                manifest = json.loads(_read_text(package, "manifest.json"))
            except json.JSONDecodeError as exc:
                raise BaselineError(f"manifest.json is not valid JSON: {exc}") from exc

            for filename in REQUIRED_CSV_FILES:
                headers, rows = _read_csv(package, filename)
                headers_by_file[filename] = headers
                rows_by_file[filename] = rows
                missing_columns = sorted(
                    MINIMUM_COLUMNS_BY_FILE.get(filename, set()) - set(headers)
                )
                if missing_columns:
                    raise BaselineError(
                        f"{filename} is missing required column(s): "
                        + ", ".join(missing_columns)
                    )
                if not rows:
                    warnings.append(f"{filename} has no data rows.")
    except zipfile.BadZipFile as exc:
        raise BaselineError(f"Not a valid ZIP package: {source_path}") from exc

    return PackageData(
        source_path=source_path,
        manifest=manifest,
        rows_by_file=rows_by_file,
        headers_by_file=headers_by_file,
        warnings=warnings,
    )


def _rating_from_totals(
    *, played: int, points: float, goals_for: float, goals_against: float
) -> float:
    if not played:
        return NEUTRAL_RATING
    points_score = _clamp((points / played) / 3, 0, 1)
    goal_difference_per_match = (goals_for - goals_against) / played
    goal_score = _clamp(0.5 + (goal_difference_per_match / 6), 0, 1)
    return _clamp((points_score * 0.7) + (goal_score * 0.3), 0, 1)


def _team_match_rows(
    team_id: str,
    approved_rows: Iterable[dict[str, str]],
    *,
    tournament_id: str | None = None,
    exclude_fixture_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    ordered_rows = sorted(approved_rows, key=_result_sort_key, reverse=True)
    for row in ordered_rows:
        fixture_id = _clean_id(row.get("fixture_id"))
        home_id = _clean_id(row.get("home_team_id"))
        away_id = _clean_id(row.get("away_team_id"))
        if exclude_fixture_id and fixture_id == exclude_fixture_id:
            continue
        if tournament_id and _clean_id(row.get("tournament_id")) != tournament_id:
            continue
        if not away_id or team_id not in {home_id, away_id}:
            continue

        home_score = _safe_float(row.get("home_score"), math.nan)
        away_score = _safe_float(row.get("away_score"), math.nan)
        if math.isnan(home_score) or math.isnan(away_score):
            continue

        if home_id == team_id:
            goals_for = home_score
            goals_against = away_score
        else:
            goals_for = away_score
            goals_against = home_score

        if goals_for > goals_against:
            points = 3
            outcome = "win"
        elif goals_for == goals_against:
            points = 1
            outcome = "draw"
        else:
            points = 0
            outcome = "loss"

        matches.append(
            {
                "fixture_id": fixture_id,
                "goals_for": goals_for,
                "goals_against": goals_against,
                "goal_difference": goals_for - goals_against,
                "points": points,
                "outcome": outcome,
            }
        )
        if limit and len(matches) >= limit:
            break
    return matches


def _summarize_match_rows(rows: Iterable[dict[str, object]]) -> dict[str, object]:
    rows = list(rows)
    played = len(rows)
    wins = sum(1 for row in rows if row["outcome"] == "win")
    draws = sum(1 for row in rows if row["outcome"] == "draw")
    losses = sum(1 for row in rows if row["outcome"] == "loss")
    goals_for = sum(float(row["goals_for"]) for row in rows)
    goals_against = sum(float(row["goals_against"]) for row in rows)
    points = sum(float(row["points"]) for row in rows)
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


def _summarize_standings(
    team_id: str,
    standing_rows: Iterable[dict[str, str]],
    *,
    fallback_rows: Iterable[dict[str, object]] | None = None,
) -> dict[str, object]:
    rows = [
        row
        for row in standing_rows
        if _clean_id(row.get("team_id")) == team_id
    ]
    if rows:
        played = sum(_safe_int(row.get("played")) for row in rows)
        wins = sum(_safe_int(row.get("wins")) for row in rows)
        draws = sum(_safe_int(row.get("draws")) for row in rows)
        losses = sum(_safe_int(row.get("losses")) for row in rows)
        goals_for = sum(_safe_float(row.get("goals_for")) for row in rows)
        goals_against = sum(_safe_float(row.get("goals_against")) for row in rows)
        points = sum(
            _safe_float(row.get("points"), (_safe_int(row.get("wins")) * 3) + _safe_int(row.get("draws")))
            for row in rows
        )
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
    if fallback_rows is not None:
        return _summarize_match_rows(fallback_rows)
    return _summarize_match_rows([])


def _global_approved_goal_average(approved_rows: Iterable[dict[str, str]]) -> float | None:
    result_count = 0
    total_goals = 0.0
    for row in approved_rows:
        if not _clean_id(row.get("away_team_id")):
            continue
        home_score = _safe_float(row.get("home_score"), math.nan)
        away_score = _safe_float(row.get("away_score"), math.nan)
        if math.isnan(home_score) or math.isnan(away_score):
            continue
        total_goals += home_score + away_score
        result_count += 1
    if not result_count:
        return None
    return total_goals / (result_count * 2)


def _goal_rate(
    current_summary: dict[str, object],
    historical_summary: dict[str, object],
    key: str,
    global_average: float,
) -> float:
    weighted_rates = []
    if current_summary["played"] and current_summary.get(key) is not None:
        weighted_rates.append((float(current_summary[key]), 0.6))
    if historical_summary["played"] and historical_summary.get(key) is not None:
        weighted_rates.append((float(historical_summary[key]), 0.4))
    if not weighted_rates:
        return global_average
    weighted_total = sum(value * weight for value, weight in weighted_rates)
    weight_total = sum(weight for _value, weight in weighted_rates)
    return weighted_total / weight_total if weight_total else global_average


def _official_player_threat(
    team_id: str,
    player_stat_rows: Iterable[dict[str, str]],
) -> dict[str, object]:
    goals = 0.0
    assists = 0.0
    scoring_rows = 0
    scoring_fixtures: set[str] = set()

    for row in player_stat_rows:
        if _clean_id(row.get("team_id")) != team_id:
            continue
        row_goals = _safe_float(row.get("goals"))
        row_assists = _safe_float(row.get("assists"))
        if row_goals <= 0 and row_assists <= 0:
            continue
        goals += row_goals
        assists += row_assists
        scoring_rows += 1
        scoring_fixtures.add(_clean_id(row.get("fixture_id")))

    if not scoring_rows or not scoring_fixtures:
        return {
            "rating": NEUTRAL_RATING,
            "goals": 0,
            "assists": 0,
            "scoring_rows": 0,
            "scoring_fixtures": 0,
            "has_data": False,
        }

    threat_per_scoring_fixture = (goals + (assists * 0.6)) / len(scoring_fixtures)
    return {
        "rating": _clamp(threat_per_scoring_fixture / 3, 0, 1),
        "goals": goals,
        "assists": assists,
        "scoring_rows": scoring_rows,
        "scoring_fixtures": len(scoring_fixtures),
        "has_data": True,
    }


def _head_to_head_factor(
    home_team_id: str,
    away_team_id: str,
    head_to_head_rows: Iterable[dict[str, str]],
    *,
    exclude_fixture_id: str | None = None,
) -> tuple[dict[str, object], float, float]:
    stats = {
        "meetings": 0,
        "home_wins": 0,
        "away_wins": 0,
        "draws": 0,
        "home_goals": 0.0,
        "away_goals": 0.0,
    }
    for row in head_to_head_rows:
        fixture_id = _clean_id(row.get("fixture_id"))
        if exclude_fixture_id and fixture_id == exclude_fixture_id:
            continue
        team_a_id = _clean_id(row.get("team_a_id"))
        team_b_id = _clean_id(row.get("team_b_id"))
        if {team_a_id, team_b_id} != {home_team_id, away_team_id}:
            continue
        team_a_goals = _safe_float(row.get("team_a_goals"), math.nan)
        team_b_goals = _safe_float(row.get("team_b_goals"), math.nan)
        if math.isnan(team_a_goals) or math.isnan(team_b_goals):
            continue

        if team_a_id == home_team_id:
            home_goals = team_a_goals
            away_goals = team_b_goals
        else:
            home_goals = team_b_goals
            away_goals = team_a_goals

        stats["meetings"] += 1
        stats["home_goals"] += home_goals
        stats["away_goals"] += away_goals
        if home_goals > away_goals:
            stats["home_wins"] += 1
        elif away_goals > home_goals:
            stats["away_wins"] += 1
        else:
            stats["draws"] += 1

    meetings = int(stats["meetings"])
    if not meetings:
        return stats, NEUTRAL_RATING, NEUTRAL_RATING

    home_points = (int(stats["home_wins"]) * 3) + int(stats["draws"])
    away_points = (int(stats["away_wins"]) * 3) + int(stats["draws"])
    home_points_score = home_points / (meetings * 3)
    away_points_score = away_points / (meetings * 3)
    goal_difference_per_meeting = (
        float(stats["home_goals"]) - float(stats["away_goals"])
    ) / meetings
    home_goal_score = _clamp(0.5 + (goal_difference_per_meeting / 6), 0, 1)
    away_goal_score = _clamp(0.5 - (goal_difference_per_meeting / 6), 0, 1)
    home_rating = _clamp((home_points_score * 0.7) + (home_goal_score * 0.3), 0, 1)
    away_rating = _clamp((away_points_score * 0.7) + (away_goal_score * 0.3), 0, 1)
    return stats, home_rating, away_rating


def _factor(
    label: str,
    weight: float,
    home_rating: float,
    away_rating: float,
    data_points: int,
    summary: str,
) -> dict[str, object]:
    return {
        "label": label,
        "weight": weight,
        "home_rating": round(home_rating, 3),
        "away_rating": round(away_rating, 3),
        "advantage": round(home_rating - away_rating, 3),
        "data_points": data_points,
        "summary": summary,
    }


def _probabilities_from_edge(edge: float, confidence: float) -> tuple[float, float, float, float]:
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


def _score_label(
    home_goals: float,
    away_goals: float,
    home_probability: float,
    draw_probability: float,
    away_probability: float,
) -> str:
    home_score = max(0, round(home_goals))
    away_score = max(0, round(away_goals))

    if (
        home_probability > away_probability
        and home_probability > draw_probability
        and home_score <= away_score
    ):
        home_score = away_score + 1
    elif (
        away_probability > home_probability
        and away_probability > draw_probability
        and away_score <= home_score
    ):
        away_score = home_score + 1
    elif draw_probability >= home_probability and draw_probability >= away_probability:
        shared_score = round((home_goals + away_goals) / 2)
        home_score = shared_score
        away_score = shared_score

    return f"{home_score}-{away_score}"


def _confidence_label(confidence: float, data_points: int) -> str:
    if confidence >= 0.75 and data_points >= 9:
        return "high"
    if confidence >= 0.4 and data_points >= 5:
        return "medium"
    return "low"


def _is_unresolved_eligible_fixture(
    fixture_row: dict[str, str],
    approved_fixture_ids: set[str],
) -> bool:
    fixture_id = _clean_id(fixture_row.get("fixture_id"))
    if not fixture_id or fixture_id in approved_fixture_ids:
        return False
    if _truthy(fixture_row.get("is_bye")):
        return False
    if not _clean_id(fixture_row.get("home_team_id")):
        return False
    if not _clean_id(fixture_row.get("away_team_id")):
        return False
    if _clean_id(fixture_row.get("approved_result_id")):
        return False
    if str(fixture_row.get("tournament_type", "")).strip().lower() != TEAM_TOURNAMENT_TYPE:
        return False
    if (
        str(fixture_row.get("tournament_status", "")).strip().lower()
        not in PREDICTABLE_TOURNAMENT_STATUSES
    ):
        return False
    return True


def _is_historical_backtest_fixture(fixture_row: dict[str, str]) -> bool:
    if _truthy(fixture_row.get("is_bye")):
        return False
    if not _clean_id(fixture_row.get("home_team_id")):
        return False
    if not _clean_id(fixture_row.get("away_team_id")):
        return False
    if str(fixture_row.get("tournament_type", "")).strip().lower() != TEAM_TOURNAMENT_TYPE:
        return False
    if (
        str(fixture_row.get("tournament_status", "")).strip().lower()
        not in HISTORICAL_BACKTEST_STATUSES
    ):
        return False
    return True


def _predict_fixture_from_rows(
    fixture_row: dict[str, str],
    *,
    approved_rows: list[dict[str, str]],
    standing_rows: list[dict[str, str]],
    player_stat_rows: list[dict[str, str]],
    head_to_head_rows: list[dict[str, str]],
) -> tuple[dict[str, str] | None, str | None, dict[str, object] | None]:
    fixture_id = _clean_id(fixture_row.get("fixture_id"))
    tournament_id = _clean_id(fixture_row.get("tournament_id"))
    home_team_id = _clean_id(fixture_row.get("home_team_id"))
    away_team_id = _clean_id(fixture_row.get("away_team_id"))
    home_team_name = fixture_row.get("home_team_name") or f"Team {home_team_id}"
    away_team_name = fixture_row.get("away_team_name") or f"Team {away_team_id}"

    global_goal_average = _global_approved_goal_average(approved_rows)
    if global_goal_average is None:
        return None, "Prediction needs at least one approved historical result.", None

    home_current = _summarize_match_rows(
        _team_match_rows(
            home_team_id,
            approved_rows,
            tournament_id=tournament_id,
            exclude_fixture_id=fixture_id,
            limit=RECENT_MATCH_LIMIT,
        )
    )
    away_current = _summarize_match_rows(
        _team_match_rows(
            away_team_id,
            approved_rows,
            tournament_id=tournament_id,
            exclude_fixture_id=fixture_id,
            limit=RECENT_MATCH_LIMIT,
        )
    )
    home_history_rows = _team_match_rows(
        home_team_id,
        approved_rows,
        exclude_fixture_id=fixture_id,
    )
    away_history_rows = _team_match_rows(
        away_team_id,
        approved_rows,
        exclude_fixture_id=fixture_id,
    )
    if not home_history_rows and not away_history_rows:
        return (
            None,
            "Prediction needs approved history for at least one fixture team.",
            None,
        )

    home_history_match_summary = _summarize_match_rows(home_history_rows)
    away_history_match_summary = _summarize_match_rows(away_history_rows)
    home_history = _summarize_standings(
        home_team_id,
        standing_rows,
        fallback_rows=home_history_rows,
    )
    away_history = _summarize_standings(
        away_team_id,
        standing_rows,
        fallback_rows=away_history_rows,
    )
    h2h_stats, home_h2h_rating, away_h2h_rating = _head_to_head_factor(
        home_team_id,
        away_team_id,
        head_to_head_rows,
        exclude_fixture_id=fixture_id,
    )
    home_threat = _official_player_threat(home_team_id, player_stat_rows)
    away_threat = _official_player_threat(away_team_id, player_stat_rows)

    current_summary = (
        f"Current form: {home_team_name} "
        f"{float(home_current['points_per_match']):.1f} PPM over {home_current['played']} match(es), "
        f"{away_team_name} {float(away_current['points_per_match']):.1f} PPM over "
        f"{away_current['played']} match(es)."
        if home_current["played"] and away_current["played"]
        else "Current form is thin in this tournament, so missing current-form data stays neutral."
    )
    history_summary = (
        f"Historical strength: {home_team_name} "
        f"{float(home_history['points_per_match']):.1f} PPM over {home_history['played']} match(es), "
        f"{away_team_name} {float(away_history['points_per_match']):.1f} PPM over "
        f"{away_history['played']} match(es)."
        if home_history["played"] and away_history["played"]
        else "Historical strength is thin for one side, so the missing side stays near neutral."
    )
    h2h_summary = (
        f"Head-to-head: {h2h_stats['meetings']} approved meeting(s), "
        f"{home_team_name} {h2h_stats['home_wins']} win(s), "
        f"{away_team_name} {h2h_stats['away_wins']} win(s), "
        f"{h2h_stats['draws']} draw(s)."
        if h2h_stats["meetings"]
        else "Head-to-head has no approved meetings yet, so it stays neutral."
    )
    player_summary = (
        f"Official player threat: {home_team_name} {home_threat['goals']:.0f} goal(s), "
        f"{home_threat['assists']:.0f} assist(s); {away_team_name} "
        f"{away_threat['goals']:.0f} goal(s), {away_threat['assists']:.0f} assist(s)."
        if home_threat["has_data"] or away_threat["has_data"]
        else "No official scoring or assist PlayerStat rows are available; zero-stat appearances are not assumed."
    )

    factors = {
        "current_form": _factor(
            "Current Form",
            0.30,
            float(home_current["rating"]),
            float(away_current["rating"]),
            int(home_current["played"]) + int(away_current["played"]),
            current_summary,
        ),
        "historical_strength": _factor(
            "Historical Strength",
            0.35,
            float(home_history["rating"]),
            float(away_history["rating"]),
            int(home_history["played"]) + int(away_history["played"]),
            history_summary,
        ),
        "head_to_head": _factor(
            "Head To Head",
            0.20,
            home_h2h_rating,
            away_h2h_rating,
            int(h2h_stats["meetings"]),
            h2h_summary,
        ),
        "player_threat": _factor(
            "Player Threat",
            0.15,
            float(home_threat["rating"]),
            float(away_threat["rating"]),
            int(home_threat["scoring_rows"]) + int(away_threat["scoring_rows"]),
            player_summary,
        ),
    }

    raw_edge = sum(
        float(factor["advantage"]) * float(factor["weight"])
        for factor in factors.values()
    )
    data_points = (
        len(home_history_rows)
        + len(away_history_rows)
        + int(h2h_stats["meetings"])
        + int(home_threat["scoring_rows"])
        + int(away_threat["scoring_rows"])
    )
    confidence = _clamp(data_points / 12, 0, 1)
    if len(home_history_rows) + len(away_history_rows) < 4:
        raw_edge *= 0.75

    home_probability, draw_probability, away_probability, moderated_edge = (
        _probabilities_from_edge(raw_edge, confidence)
    )

    home_attack = _goal_rate(
        home_current,
        home_history_match_summary,
        "goals_for_per_match",
        global_goal_average,
    )
    away_defense = _goal_rate(
        away_current,
        away_history_match_summary,
        "goals_against_per_match",
        global_goal_average,
    )
    away_attack = _goal_rate(
        away_current,
        away_history_match_summary,
        "goals_for_per_match",
        global_goal_average,
    )
    home_defense = _goal_rate(
        home_current,
        home_history_match_summary,
        "goals_against_per_match",
        global_goal_average,
    )
    expected_home_goals = _clamp(
        ((home_attack * 0.62) + (away_defense * 0.38)) + (moderated_edge * 0.35),
        0.2,
        5.0,
    )
    expected_away_goals = _clamp(
        ((away_attack * 0.62) + (home_defense * 0.38)) - (moderated_edge * 0.35),
        0.2,
        5.0,
    )

    explanation_lines = [
        "Deterministic local formula over package CSVs only.",
        current_summary,
        history_summary,
        h2h_summary,
        player_summary,
    ]
    if len(home_history_rows) + len(away_history_rows) < 4:
        explanation_lines.append("Data is thin, so probabilities are kept conservative.")

    prediction_row = {
        "fixture_id": _safe_output(fixture_id),
        "tournament_id": _safe_output(tournament_id),
        "home_team": _safe_output(home_team_name),
        "away_team": _safe_output(away_team_name),
        "home_win_probability": f"{home_probability:.1f}",
        "draw_probability": f"{draw_probability:.1f}",
        "away_win_probability": f"{away_probability:.1f}",
        "expected_home_goals": f"{expected_home_goals:.2f}",
        "expected_away_goals": f"{expected_away_goals:.2f}",
        "predicted_score_label": _score_label(
            expected_home_goals,
            expected_away_goals,
            home_probability,
            draw_probability,
            away_probability,
        ),
        "confidence_label": _confidence_label(confidence, data_points),
        "explanation": _safe_output(" ".join(explanation_lines)),
    }
    details = {
        "probabilities": {
            "home": home_probability,
            "draw": draw_probability,
            "away": away_probability,
        },
        "confidence": confidence,
        "data_points": data_points,
        "factors": factors,
    }
    return prediction_row, None, details


def generate_predictions(
    package_data: PackageData,
) -> tuple[list[dict[str, str]], int, list[str]]:
    rows = package_data.rows_by_file
    eligible_fixtures = eligible_prediction_fixture_rows(package_data)

    warnings: list[str] = []
    predictions: list[dict[str, str]] = []
    for fixture_row in eligible_fixtures:
        prediction, unavailable_reason, _details = predict_fixture_from_package_rows(
            fixture_row,
            rows,
        )
        if prediction is None:
            warnings.append(
                f"Fixture {_clean_id(fixture_row.get('fixture_id'))} skipped: "
                f"{unavailable_reason}"
            )
            continue
        predictions.append(prediction)

    if not eligible_fixtures:
        warnings.append(
            "No unresolved eligible fixtures were found in fixtures.csv. "
            "Current Phase 4.2 packages can be valid and still produce an empty "
            "prediction file because they export approved-result fixture context."
        )
    elif not predictions:
        warnings.append(
            "Unresolved eligible fixtures were present, but none had enough "
            "approved package history for the baseline formula."
        )

    return predictions, len(eligible_fixtures), warnings


def eligible_prediction_fixture_rows(
    package_data: PackageData,
) -> list[dict[str, str]]:
    rows = package_data.rows_by_file
    approved_fixture_ids = {
        _clean_id(row.get("fixture_id"))
        for row in rows["approved_results.csv"]
        if _clean_id(row.get("fixture_id"))
    }
    return [
        row
        for row in rows["fixtures.csv"]
        if _is_unresolved_eligible_fixture(row, approved_fixture_ids)
    ]


def predict_fixture_from_package_rows(
    fixture_row: dict[str, str],
    rows_by_file: Mapping[str, list[dict[str, str]]],
) -> tuple[dict[str, str] | None, str | None, dict[str, object] | None]:
    return _predict_fixture_from_rows(
        fixture_row,
        approved_rows=rows_by_file["approved_results.csv"],
        standing_rows=rows_by_file["team_standings_history.csv"],
        player_stat_rows=rows_by_file["official_player_stats.csv"],
        head_to_head_rows=rows_by_file["head_to_head.csv"],
    )


def _actual_outcome_from_result(row: dict[str, str]) -> str | None:
    home_score = _safe_float(row.get("home_score"), math.nan)
    away_score = _safe_float(row.get("away_score"), math.nan)
    if math.isnan(home_score) or math.isnan(away_score):
        return None
    if home_score > away_score:
        return "home"
    if away_score > home_score:
        return "away"
    return "draw"


def _predicted_outcome_from_row(row: dict[str, str]) -> str:
    probabilities = {
        "home": _safe_float(row.get("home_win_probability")),
        "draw": _safe_float(row.get("draw_probability")),
        "away": _safe_float(row.get("away_win_probability")),
    }
    return max(probabilities, key=probabilities.get)


def evaluate_historical_backtest(package_data: PackageData) -> dict[str, object]:
    rows = package_data.rows_by_file
    fixture_rows_by_id = {
        _clean_id(row.get("fixture_id")): row
        for row in rows["fixtures.csv"]
        if _clean_id(row.get("fixture_id"))
    }
    ordered_results = sorted(rows["approved_results.csv"], key=_result_sort_key)
    correct_outcomes = 0
    evaluated = 0
    brier_scores: list[float] = []

    for index, target_result in enumerate(ordered_results):
        fixture_id = _clean_id(target_result.get("fixture_id"))
        fixture_row = fixture_rows_by_id.get(fixture_id)
        actual_outcome = _actual_outcome_from_result(target_result)
        if fixture_row is None or actual_outcome is None:
            continue
        if not _is_historical_backtest_fixture(fixture_row):
            continue

        history_rows = ordered_results[:index]
        history_fixture_ids = {
            _clean_id(row.get("fixture_id"))
            for row in history_rows
            if _clean_id(row.get("fixture_id"))
        }
        if not history_rows:
            continue

        prediction, _reason, _details = _predict_fixture_from_rows(
            fixture_row,
            approved_rows=history_rows,
            standing_rows=[],
            player_stat_rows=[
                row
                for row in rows["official_player_stats.csv"]
                if _clean_id(row.get("fixture_id")) in history_fixture_ids
            ],
            head_to_head_rows=[
                row
                for row in rows["head_to_head.csv"]
                if _clean_id(row.get("fixture_id")) in history_fixture_ids
            ],
        )
        if prediction is None:
            continue

        predicted_outcome = _predicted_outcome_from_row(prediction)
        correct_outcomes += int(predicted_outcome == actual_outcome)
        evaluated += 1

        probability_vector = {
            "home": _safe_float(prediction["home_win_probability"]) / 100,
            "draw": _safe_float(prediction["draw_probability"]) / 100,
            "away": _safe_float(prediction["away_win_probability"]) / 100,
        }
        brier_scores.append(
            sum(
                (
                    probability_vector[outcome]
                    - (1.0 if actual_outcome == outcome else 0.0)
                )
                ** 2
                for outcome in ("home", "draw", "away")
            )
        )

    return {
        "evaluated_historical_count": evaluated,
        "correct_outcome_count": correct_outcomes,
        "outcome_accuracy": round(correct_outcomes / evaluated, 3) if evaluated else None,
        "average_brier_score": (
            round(sum(brier_scores) / len(brier_scores), 4) if brier_scores else None
        ),
        "method": (
            "Chronological leave-past-in backtest over approved fixtures; each "
            "target only sees earlier approved result, head-to-head, and player-stat rows."
        ),
    }


def _write_predictions_csv(path: Path, predictions: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=BASELINE_PREDICTION_HEADERS,
            lineterminator="\n",
        )
        writer.writeheader()
        for row in predictions:
            writer.writerow({
                header: _safe_output(row.get(header, ""))
                for header in BASELINE_PREDICTION_HEADERS
            })


def _write_json(path: Path, data: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def run_baseline(
    zip_path: str | Path,
    output_dir: str | Path | None = None,
) -> BaselineRunResult:
    package_data = load_package(zip_path)
    resolved_input_path = package_data.source_path.resolve()
    destination = (
        Path(output_dir).expanduser()
        if output_dir is not None
        else package_data.source_path.with_suffix("").with_name(
            f"{package_data.source_path.stem}_baseline_output"
        )
    )
    destination.mkdir(parents=True, exist_ok=True)

    generated_at = _utc_now_iso()
    predictions, eligible_fixture_count, prediction_warnings = generate_predictions(
        package_data
    )
    backtest = evaluate_historical_backtest(package_data)
    warnings = [*package_data.warnings, *prediction_warnings]
    limitations = [
        "Local-only deterministic formula baseline; this is not AI or ML.",
        "No PyTorch, CUDA/GPU, OCR, Gemma/local LLM, Claude, Google Sheets, paid API, or external network call is used.",
        "The baseline uses only CSV rows inside the downloaded package and does not read Django settings or the database.",
        "Current Phase 4.2 packages may contain no unresolved fixture rows, so baseline_predictions.csv can be intentionally empty.",
        "Zero-stat appearances, lineups, formations, substitutions, player availability, venue effects, and championship history are not modeled.",
        "Backtest metrics are a simple local baseline check and should not be described as measured model accuracy.",
    ]

    output_files = {
        "baseline_predictions.csv": destination / "baseline_predictions.csv",
        "evaluation_summary.json": destination / "evaluation_summary.json",
        "run_manifest.json": destination / "run_manifest.json",
    }

    evaluation_summary = {
        "generated_at": generated_at,
        "source_package": str(resolved_input_path),
        "eligible_fixture_count": eligible_fixture_count,
        "prediction_count": len(predictions),
        "evaluated_historical_count": backtest["evaluated_historical_count"],
        "backtest": backtest,
        "formula_version": FORMULA_VERSION,
        "limitations": limitations,
        "warnings": warnings,
    }
    run_manifest = {
        "script_name": SCRIPT_NAME,
        "input_package_path": str(resolved_input_path),
        "output_files": {
            filename: str(path.resolve())
            for filename, path in output_files.items()
        },
        "formula_version": FORMULA_VERSION,
        "local_only": True,
        "render_required": False,
        "generated_at": generated_at,
        "package_manifest": {
            "package_version": package_data.manifest.get("package_version"),
            "schema_version": package_data.manifest.get("schema_version"),
            "row_counts": package_data.manifest.get("row_counts", {}),
        },
    }

    _write_predictions_csv(output_files["baseline_predictions.csv"], predictions)
    _write_json(output_files["evaluation_summary.json"], evaluation_summary)
    _write_json(output_files["run_manifest.json"], run_manifest)

    return BaselineRunResult(
        output_dir=destination,
        output_files=output_files,
        eligible_fixture_count=eligible_fixture_count,
        prediction_count=len(predictions),
        evaluated_historical_count=int(backtest["evaluated_historical_count"]),
        warnings=warnings,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a local deterministic baseline over a Hosted by Tanvir "
            "prediction dataset ZIP."
        )
    )
    parser.add_argument("zip_path", help="Path to prediction-dataset ZIP package")
    parser.add_argument(
        "-o",
        "--output-dir",
        help=(
            "Directory for baseline_predictions.csv, evaluation_summary.json, "
            "and run_manifest.json. Defaults beside the ZIP."
        ),
    )
    args = parser.parse_args(argv)

    try:
        result = run_baseline(args.zip_path, args.output_dir)
    except BaselineError as exc:
        print(f"Baseline run failed: {exc}", file=sys.stderr)
        return 1

    print("Prediction baseline run complete")
    print(f"Output directory: {result.output_dir}")
    print(f"Eligible unresolved fixtures: {result.eligible_fixture_count}")
    print(f"Predictions written: {result.prediction_count}")
    print(f"Historical fixtures evaluated: {result.evaluated_historical_count}")
    if result.warnings:
        print("")
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
