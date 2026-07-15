# tournament/fixtures.py

BYE = "BYE"


def generate_round_robin(teams):
    teams_list = list(teams)
    if len(teams_list) % 2 == 1:
        teams_list.append(BYE)
    n = len(teams_list)
    schedule = []
    for round_num in range(n - 1):
        for i in range(n // 2):
            home = teams_list[i]
            away = teams_list[n - 1 - i]
            schedule.append({
                "home": home,
                "away": away,
                "round_number": round_num + 1,
                "is_bye": home == BYE or away == BYE,
            })
        last = teams_list[-1]
        teams_list[2:] = teams_list[1:-1]
        teams_list[1] = last
    return schedule


def generate_knockout(teams):
    teams_list = list(teams)
    n = len(teams_list)
    power = 1
    while power < n:
        power *= 2
    teams_list += [BYE] * (power - n)
    matchups = []
    total = len(teams_list)
    for i in range(total // 2):
        home = teams_list[i]
        away = teams_list[total - 1 - i]
        if home == BYE and away == BYE:
            continue
        matchups.append({
            "home": home,
            "away": away,
            "round_number": 1,
            "is_bye": home == BYE or away == BYE,
        })
    return matchups


def generate_fixtures_for_tournament(tournament):
    """
    Fetches APPROVED registered teams only, calls the right generator,
    bulk-creates Fixture rows. Idempotent for non-hybrid tournaments.

    For hybrid tournaments this function is progressive:
    - first call generates grouped round-robin fixtures
    - later calls generate the next knockout round once the current stage is complete

    Returns: (created_count, error_message_or_None)
    """
    from tournament.models import Fixture, TournamentRegistration, Tournament
    from tournament.hybrid import HybridProgressionError, create_next_hybrid_knockout_round

    tournament.fixture_generation_notice = ""
    tournament.fixture_generation_action = ""

    if tournament.is_archived:
        return 0, tournament.archive_lock_reason()

    if tournament.tournament_type != Tournament.TEAM:
        return 0, "Fixtures can only be generated for team tournaments."

    if tournament.format == Tournament.HYBRID:
        existing_fixtures = Fixture.objects.filter(tournament=tournament)
        existing_group_stage = existing_fixtures.filter(stage=Fixture.GROUP, group_label__gt="")
        existing_elimination = existing_fixtures.filter(stage__in=[Fixture.KNOCKOUT, Fixture.FINAL])

        if existing_elimination.exists() or existing_group_stage.exists():
            try:
                count, round_number, stage = create_next_hybrid_knockout_round(tournament)
            except HybridProgressionError as error:
                return 0, str(error)

            if stage == Fixture.FINAL:
                tournament.fixture_generation_action = "Final"
            elif existing_elimination.exists():
                tournament.fixture_generation_action = f"Knockout round {round_number}"
            else:
                tournament.fixture_generation_action = f"Knockout round {round_number}"
            return count, None

        assigned_groups_exist = TournamentRegistration.objects.filter(
            tournament=tournament,
            is_active=True,
            team__is_approved=True,
            group_label__gt="",
        ).exists()
        if not assigned_groups_exist:
            return 0, (
                "Hybrid tournaments require group assignments before the first fixture generation. "
                "Assign every active team entrant to a group first."
            )

    elif Fixture.objects.filter(tournament=tournament).exists():
        return 0, "Fixtures already exist for this tournament."

    # Only approved teams get fixtures
    registrations = TournamentRegistration.objects.filter(
        tournament=tournament,
        is_active=True,
        team__is_approved=True,
    ).select_related("team").order_by("group_label", "seed", "registered_at")

    grouped_registrations = [reg for reg in registrations if reg.group_label]
    if grouped_registrations:
        ungrouped_registrations = [reg for reg in registrations if not reg.group_label]
        if ungrouped_registrations:
            return 0, (
                "Active team entrants are only partially grouped. "
                "Assign every active team entrant to a group before generating group-stage fixtures."
            )

        fixtures_to_create = []
        skipped_groups = []
        group_labels = sorted({reg.group_label for reg in grouped_registrations})
        for group_label in group_labels:
            group_teams = [reg.team for reg in grouped_registrations if reg.group_label == group_label]
            if len(group_teams) < 2:
                skipped_groups.append(group_label)
                continue

            raw = generate_round_robin(group_teams)
            for match in raw:
                home = match["home"]
                away = match["away"]
                is_bye = match["is_bye"]
                if is_bye:
                    home_team = away if home == BYE else home
                    away_team = None
                else:
                    home_team = home
                    away_team = away
                fixtures_to_create.append(Fixture(
                    tournament=tournament,
                    home_team=home_team,
                    away_team=away_team,
                    round_number=match["round_number"],
                    stage=Fixture.GROUP,
                    group_label=group_label,
                    is_bye=is_bye,
                ))

        if not fixtures_to_create:
            return 0, (
                "No group-stage fixtures were generated. "
                "Each assigned group needs at least 2 active teams."
            )

        created = Fixture.objects.bulk_create(fixtures_to_create)
        tournament.fixture_generation_action = "Group stage"
        if skipped_groups:
            skipped_list = ", ".join(f"Group {label}" for label in skipped_groups)
            tournament.fixture_generation_notice = (
                f"Skipped {skipped_list} because each group needs at least 2 active teams."
            )
        return len(created), None

    teams = [reg.team for reg in registrations]

    if len(teams) < 2:
        return 0, (
            "Need at least 2 approved registered teams to generate fixtures. "
            "Make sure teams are approved in admin before generating."
        )

    if tournament.format == Tournament.ROUND_ROBIN:
        raw = generate_round_robin(teams)
        stage = Fixture.GROUP
    elif tournament.format == Tournament.KNOCKOUT:
        raw = generate_knockout(teams)
        stage = Fixture.KNOCKOUT
    else:
        raw = generate_round_robin(teams)
        stage = Fixture.GROUP

    fixtures_to_create = []
    for match in raw:
        home = match["home"]
        away = match["away"]
        is_bye = match["is_bye"]
        if is_bye:
            home_team = away if home == BYE else home
            away_team = None
        else:
            home_team = home
            away_team = away
        fixtures_to_create.append(Fixture(
            tournament=tournament,
            home_team=home_team,
            away_team=away_team,
            round_number=match["round_number"],
            stage=stage,
            group_label="",
            is_bye=is_bye,
        ))

    created = Fixture.objects.bulk_create(fixtures_to_create)
    tournament.fixture_generation_action = "Fixtures"
    return len(created), None
