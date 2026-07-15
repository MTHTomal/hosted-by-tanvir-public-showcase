from dataclasses import dataclass

from tournament.models import Fixture, Result, Tournament, TournamentRegistration


@dataclass
class HybridStandingRow:
    team: object
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0

    @property
    def goal_difference(self):
        return self.goals_for - self.goals_against


class HybridProgressionError(Exception):
    pass


def _qualifiers_per_group(tournament):
    qualifiers_per_group = tournament.hybrid_qualifiers_per_group
    if qualifiers_per_group not in [
        Tournament.HYBRID_QUALIFIERS_TOP_2,
        Tournament.HYBRID_QUALIFIERS_TOP_4,
    ]:
        raise HybridProgressionError(
            "Hybrid knockout generation supports only top 2 or top 4 qualifiers per group."
        )
    return qualifiers_per_group


def _non_bye_group_fixtures(tournament):
    return list(
        Fixture.objects.filter(
            tournament=tournament,
            stage=Fixture.GROUP,
            group_label__gt="",
            is_bye=False,
        )
        .select_related("home_team", "away_team")
        .order_by("group_label", "round_number", "pk")
    )


def _approved_result(fixture):
    return (
        fixture.results.filter(status=Result.APPROVED)
        .order_by("-reviewed_at", "-submitted_at", "-pk")
        .first()
    )


def _group_labels_for_hybrid(tournament):
    return list(
        TournamentRegistration.objects.filter(
            tournament=tournament,
            is_active=True,
            team__is_approved=True,
            group_label__gt="",
        )
        .order_by("group_label")
        .values_list("group_label", flat=True)
        .distinct()
    )


def build_group_tables_for_tournament(tournament):
    fixtures = _non_bye_group_fixtures(tournament)
    if not fixtures:
        raise HybridProgressionError(
            "Generate the hybrid group-stage fixtures before creating the knockout bracket."
        )

    tables = {}
    for fixture in fixtures:
        section = tables.setdefault(fixture.group_label, {})
        section.setdefault(fixture.home_team_id, HybridStandingRow(team=fixture.home_team))
        section.setdefault(fixture.away_team_id, HybridStandingRow(team=fixture.away_team))

        result = _approved_result(fixture)
        if result is None:
            continue

        home = section[fixture.home_team_id]
        away = section[fixture.away_team_id]
        home.played += 1
        away.played += 1
        home.goals_for += result.home_score
        home.goals_against += result.away_score
        away.goals_for += result.away_score
        away.goals_against += result.home_score

        if result.home_score > result.away_score:
            home.wins += 1
            away.losses += 1
            home.points += 3
        elif result.home_score < result.away_score:
            away.wins += 1
            home.losses += 1
            away.points += 3
        else:
            home.draws += 1
            away.draws += 1
            home.points += 1
            away.points += 1

    return {
        label: sorted(
            rows.values(),
            key=lambda row: (
                -row.points,
                -row.goal_difference,
                -row.goals_for,
                row.team.name,
            ),
        )
        for label, rows in sorted(tables.items())
    }


def _validate_group_stage_ready_for_knockout(tournament):
    if tournament.tournament_type != Tournament.TEAM:
        raise HybridProgressionError("Hybrid progression is only available for team tournaments.")
    if tournament.format != Tournament.HYBRID:
        raise HybridProgressionError("This tournament is not using the hybrid format.")

    qualifiers_per_group = _qualifiers_per_group(tournament)

    group_labels = _group_labels_for_hybrid(tournament)
    if not group_labels:
        raise HybridProgressionError(
            "Assign active team entrants to groups before generating hybrid fixtures."
        )

    fixtures = _non_bye_group_fixtures(tournament)
    if not fixtures:
        raise HybridProgressionError(
            "Generate the hybrid group-stage fixtures before creating the knockout bracket."
        )

    incomplete = [fixture for fixture in fixtures if _approved_result(fixture) is None]
    if incomplete:
        raise HybridProgressionError(
            "Every group-stage fixture must have an approved result before creating the knockout bracket."
        )

    tables = build_group_tables_for_tournament(tournament)
    for group_label in group_labels:
        rows = tables.get(group_label, [])
        if len(rows) < qualifiers_per_group:
            raise HybridProgressionError(
                f"Group {group_label} must have at least {qualifiers_per_group} teams to produce qualifiers."
            )

    if len(group_labels) % 2 != 0:
        raise HybridProgressionError(
            "Hybrid knockout generation currently requires an even number of groups."
        )

    return tables, group_labels, qualifiers_per_group


def _initial_pairs_for_group_pair(left_rows, right_rows, qualifiers_per_group):
    if qualifiers_per_group == Tournament.HYBRID_QUALIFIERS_TOP_2:
        return [
            (left_rows[0].team, right_rows[1].team),
            (right_rows[0].team, left_rows[1].team),
        ]

    if qualifiers_per_group == Tournament.HYBRID_QUALIFIERS_TOP_4:
        return [
            (left_rows[0].team, right_rows[3].team),
            (left_rows[1].team, right_rows[2].team),
            (right_rows[0].team, left_rows[3].team),
            (right_rows[1].team, left_rows[2].team),
        ]

    raise HybridProgressionError(
        "Hybrid knockout generation supports only top 2 or top 4 qualifiers per group."
    )


def _next_round_number(tournament):
    latest = (
        Fixture.objects.filter(tournament=tournament)
        .order_by("-round_number")
        .values_list("round_number", flat=True)
        .first()
    )
    return (latest or 0) + 1


def _stage_for_pair_count(pair_count):
    return Fixture.FINAL if pair_count == 1 else Fixture.KNOCKOUT


def _create_fixture_rows(tournament, ordered_pairs):
    round_number = _next_round_number(tournament)
    stage = _stage_for_pair_count(len(ordered_pairs))
    fixtures = [
        Fixture(
            tournament=tournament,
            home_team=home,
            away_team=away,
            round_number=round_number,
            stage=stage,
            group_label="",
        )
        for home, away in ordered_pairs
    ]
    created = Fixture.objects.bulk_create(fixtures)
    return len(created), round_number, stage


def create_initial_hybrid_knockout_round(tournament):
    existing_elimination = Fixture.objects.filter(
        tournament=tournament,
        stage__in=[Fixture.KNOCKOUT, Fixture.FINAL],
    )
    if existing_elimination.exists():
        raise HybridProgressionError("The hybrid knockout bracket has already been created.")

    tables, group_labels, qualifiers_per_group = _validate_group_stage_ready_for_knockout(tournament)

    ordered_pairs = []
    for index in range(0, len(group_labels), 2):
        left_label = group_labels[index]
        right_label = group_labels[index + 1]
        left_rows = tables[left_label]
        right_rows = tables[right_label]
        ordered_pairs.extend(
            _initial_pairs_for_group_pair(
                left_rows=left_rows,
                right_rows=right_rows,
                qualifiers_per_group=qualifiers_per_group,
            )
        )

    expected_fixture_count = (len(group_labels) * qualifiers_per_group) // 2
    if len(ordered_pairs) != expected_fixture_count:
        raise HybridProgressionError(
            "Hybrid knockout bracket construction failed due to an invalid qualifier configuration."
        )

    advancing_team_ids = [team.pk for pair in ordered_pairs for team in pair]
    if len(set(advancing_team_ids)) != len(advancing_team_ids):
        raise HybridProgressionError(
            "Hybrid knockout bracket construction failed because a team was seeded more than once."
        )

    return _create_fixture_rows(tournament, ordered_pairs)


def _latest_elimination_round_fixtures(tournament):
    fixtures = list(
        Fixture.objects.filter(
            tournament=tournament,
            stage__in=[Fixture.KNOCKOUT, Fixture.FINAL],
        )
        .select_related("home_team", "away_team")
        .order_by("round_number", "pk")
    )
    if not fixtures:
        return []

    latest_round = max(fixture.round_number for fixture in fixtures)
    return [fixture for fixture in fixtures if fixture.round_number == latest_round]


def _winner_for_fixture(fixture):
    result = _approved_result(fixture)
    if result is None:
        raise HybridProgressionError("Every fixture in the current knockout round needs an approved result.")
    if result.home_score == result.away_score:
        raise HybridProgressionError(
            "Knockout fixtures cannot end in a draw. Edit the approved result before generating the next round."
        )
    return fixture.home_team if result.home_score > result.away_score else fixture.away_team


def create_next_hybrid_knockout_round(tournament):
    latest_round_fixtures = _latest_elimination_round_fixtures(tournament)
    if not latest_round_fixtures:
        return create_initial_hybrid_knockout_round(tournament)

    if len(latest_round_fixtures) == 1 and latest_round_fixtures[0].stage == Fixture.FINAL:
        if _approved_result(latest_round_fixtures[0]) is None:
            raise HybridProgressionError("The final still needs an approved result.")
        raise HybridProgressionError("This hybrid tournament is already complete.")

    winners = [_winner_for_fixture(fixture) for fixture in latest_round_fixtures]
    if len(winners) < 2:
        raise HybridProgressionError("Not enough winners are available to create another knockout round.")
    if len(winners) % 2 != 0:
        raise HybridProgressionError("The next knockout round requires an even number of advancing teams.")

    ordered_pairs = []
    for index in range(0, len(winners), 2):
        ordered_pairs.append((winners[index], winners[index + 1]))

    return _create_fixture_rows(tournament, ordered_pairs)
