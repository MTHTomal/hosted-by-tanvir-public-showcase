from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Player, Team, TeamMembership
from tournament.fixtures import generate_fixtures_for_tournament
from tournament.models import Fixture, Result, Tournament, TournamentRegistration
from tournament.validation_demo import (
    DEMO_TEAM_NAMES,
    DEMO_STAFF,
    DEMO_TEAM_SPECS,
    DEMO_TOURNAMENTS,
    DEMO_RESULT_SCREENSHOT_PREFIX,
    demo_entity_note,
    demo_deadline,
    demo_end_date,
    demo_start_date,
    generate_demo_password,
)


class Command(BaseCommand):
    help = "Seed safe, idempotent Tournament 1 validation demo data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Clear existing validation demo data first, then reseed it.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not getattr(settings, "IS_TEST", False):
            raise CommandError(
                "seed_validation_data is restricted to DEBUG=True local settings or Django test settings."
            )

        demo_password = generate_demo_password()

        if options["reset"]:
            self.stdout.write("Reset requested. Clearing existing validation demo data first.")
            from django.core.management import call_command

            call_command("clear_validation_data")

        created_summary = []
        reused_summary = []

        def mark_status(created, label):
            if created:
                created_summary.append(label)
            else:
                reused_summary.append(label)

        staff = self._upsert_player(
            DEMO_STAFF,
            password=demo_password,
            is_staff=True,
            is_superuser=True,
        )
        mark_status(staff["_created"], f"staff account: {staff['player'].username}")

        teams = {}
        captains = []
        for spec in DEMO_TEAM_SPECS:
            team, created, captain = self._upsert_team(spec, password=demo_password)
            teams[spec["name"]] = team
            captains.append(captain)
            mark_status(created, f"team: {team.name}")

        non_grouped = self._upsert_tournament(
            DEMO_TOURNAMENTS["non_grouped"],
            status=Tournament.REGISTRATION,
            format=Tournament.ROUND_ROBIN,
            description=(
                f"{demo_entity_note('validation tournament')} "
                "Seeded with entrants only so registration and fixture generation can be validated manually."
            ),
        )
        mark_status(non_grouped["_created"], f"tournament: {non_grouped['tournament'].name}")

        grouped = self._upsert_tournament(
            DEMO_TOURNAMENTS["grouped"],
            status=Tournament.ACTIVE,
            format=Tournament.ROUND_ROBIN,
            description=(
                f"{demo_entity_note('validation tournament')} "
                "Seeded through the real registration and fixture generation path with moderation-ready results."
            ),
        )
        mark_status(grouped["_created"], f"tournament: {grouped['tournament'].name}")

        non_grouped_registrations = [
            ("Atlas FC", 1, ""),
            ("Blaze FC", 2, ""),
            ("Comets FC", 3, ""),
        ]
        for team_name, seed, group_label in non_grouped_registrations:
            registration = self._upsert_registration(
                non_grouped["tournament"],
                teams[team_name],
                seed=seed,
                group_label=group_label,
            )
            mark_status(
                registration["_created"],
                f"non-grouped registration: {teams[team_name].name}",
            )

        grouped_registrations = [
            ("Atlas FC", 1, "A"),
            ("Blaze FC", 2, "A"),
            ("Comets FC", 3, "A"),
            ("Dragons FC", 4, "B"),
            ("Eclipse FC", 5, "B"),
            ("Falcons FC", 6, "B"),
        ]
        for team_name, seed, group_label in grouped_registrations:
            registration = self._upsert_registration(
                grouped["tournament"],
                teams[team_name],
                seed=seed,
                group_label=group_label,
            )
            mark_status(
                registration["_created"],
                f"grouped registration: {teams[team_name].name} -> Group {group_label}",
            )

        grouped_fixtures_created = 0
        grouped_fixture_error = None
        if not Fixture.objects.filter(tournament=grouped["tournament"]).exists():
            grouped_fixtures_created, grouped_fixture_error = generate_fixtures_for_tournament(grouped["tournament"])
        else:
            grouped_fixture_error = "Fixtures already existed and were reused."

        grouped_fixtures = list(
            Fixture.objects.filter(tournament=grouped["tournament"], is_bye=False)
            .select_related("home_team", "away_team")
            .order_by("group_label", "round_number", "pk")
        )
        grouped_fixtures_by_group = {}
        for fixture in grouped_fixtures:
            grouped_fixtures_by_group.setdefault(fixture.group_label, []).append(fixture)

        group_a_fixture_approved = grouped_fixtures_by_group["A"][0]
        group_a_fixture_pending = grouped_fixtures_by_group["A"][1]
        group_b_fixture_approved = grouped_fixtures_by_group["B"][0]

        approved_a = self._sync_result(
            fixture=group_a_fixture_approved,
            status=Result.APPROVED,
            home_score=2,
            away_score=1,
            submitted_by=group_a_fixture_approved.home_team.captain,
            reviewed_by=staff["player"],
            screenshot_key="group-a-approved",
        )
        approved_b = self._sync_result(
            fixture=group_b_fixture_approved,
            status=Result.APPROVED,
            home_score=3,
            away_score=0,
            submitted_by=group_b_fixture_approved.home_team.captain,
            reviewed_by=staff["player"],
            screenshot_key="group-b-approved",
        )
        pending = self._sync_result(
            fixture=group_a_fixture_pending,
            status=Result.PENDING,
            home_score=1,
            away_score=1,
            submitted_by=group_a_fixture_pending.home_team.captain,
            reviewed_by=None,
            screenshot_key="group-a-pending",
        )
        mark_status(approved_a["_created"], f"approved grouped result: fixture {group_a_fixture_approved.pk}")
        mark_status(approved_b["_created"], f"approved grouped result: fixture {group_b_fixture_approved.pk}")
        mark_status(pending["_created"], f"pending grouped result: fixture {group_a_fixture_pending.pk}")

        self.stdout.write(self.style.SUCCESS("Tournament 1 validation demo data is ready."))
        self.stdout.write("")
        self.stdout.write("Created objects:")
        for line in created_summary or ["- none"]:
            self.stdout.write(f"- {line}")
        self.stdout.write("")
        self.stdout.write("Reused/updated objects:")
        for line in reused_summary or ["- none"]:
            self.stdout.write(f"- {line}")
        self.stdout.write("")
        self.stdout.write("Demo login credentials:")
        self.stdout.write(f"- Shared password for this seed run: {demo_password}")
        self.stdout.write(f"- Staff/admin: {staff['player'].username} / {staff['player'].email}")
        for captain in captains:
            self.stdout.write(f"- Captain: {captain.username} / {captain.email}")
        self.stdout.write("")
        self.stdout.write("Demo tournaments:")
        self.stdout.write(f"- Non-grouped: {non_grouped['tournament'].name}")
        self.stdout.write(f"- Grouped: {grouped['tournament'].name}")
        self.stdout.write("")
        self.stdout.write("Demo teams:")
        for team in Team.objects.filter(name__in=DEMO_TEAM_NAMES).order_by("name"):
            self.stdout.write(f"- {team.name}")
        self.stdout.write("")
        self.stdout.write("Ready now:")
        self.stdout.write("- Grouped tournament fixtures were generated via the real fixture generator.")
        self.stdout.write("- Grouped standings should be visible immediately because both groups have an approved result.")
        self.stdout.write("- One grouped fixture already has a pending result for moderation testing.")
        self.stdout.write("- Grouped entrant locking should already be in effect because fixtures/results exist.")
        self.stdout.write("")
        self.stdout.write("Manual validation still required:")
        self.stdout.write("- Non-grouped tournament is seeded without fixtures so registration and fixture generation can be tested manually.")
        self.stdout.write("- Register one of the remaining approved demo teams to the non-grouped tournament through the normal player flow before generating fixtures.")
        self.stdout.write("- Use the short-handed demo team owner for negative registration testing.")
        self.stdout.write("")
        self.stdout.write("Fixture generation status:")
        self.stdout.write(f"- Grouped tournament: {grouped_fixtures_created} fixture(s) created this run.")
        if grouped_fixture_error:
            self.stdout.write(f"- Grouped tournament note: {grouped_fixture_error}")
        self.stdout.write("- Non-grouped tournament: fixtures are not pre-generated on purpose.")

    def _upsert_player(self, spec, *, password, is_staff=False, is_superuser=False):
        defaults = {
            "email": spec["email"],
            "unique_id": spec["unique_id"],
            "in_game_name": spec["in_game_name"],
            "player_type": Player.ADMIN_CREATED,
            "is_staff": is_staff,
            "is_superuser": is_superuser,
            "is_active": True,
        }
        player, created = Player.objects.get_or_create(username=spec["username"], defaults=defaults)
        changed = created
        for field, value in defaults.items():
            if getattr(player, field) != value:
                setattr(player, field, value)
                changed = True
        if not player.check_password(password):
            player.set_password(password)
            changed = True
        if changed:
            player.save()
        return {"player": player, "_created": created}

    def _upsert_team(self, spec, *, password):
        captain_spec = spec["members"][0]
        captain = self._upsert_player(captain_spec, password=password)["player"]
        for member_spec in spec["members"][1:]:
            self._upsert_player(member_spec, password=password)

        team, created = Team.objects.get_or_create(
            name=spec["name"],
            defaults={
                "captain": captain,
                "is_approved": spec["approved"],
                "description": (
                    f"{demo_entity_note('validation team')} "
                    "Seeded for manual Tournament 1 workflow checks."
                ),
            },
        )
        changed = created
        if team.captain_id != captain.pk:
            team.captain = captain
            changed = True
        if team.is_approved != spec["approved"]:
            team.is_approved = spec["approved"]
            changed = True
        desired_description = (
            f"{demo_entity_note('validation team')} "
            "Seeded for manual Tournament 1 workflow checks."
        )
        if team.description != desired_description:
            team.description = desired_description
            changed = True
        if changed:
            team.save()

        desired_usernames = {member["username"] for member in spec["members"]}
        TeamMembership.objects.filter(team=team, is_active=True).exclude(
            player__username__in=desired_usernames
        ).update(is_active=False, left_at=timezone.now())

        for member_spec in spec["members"]:
            player = Player.objects.get(username=member_spec["username"])
            TeamMembership.objects.filter(player=player, is_active=True).exclude(team=team).update(
                is_active=False,
                left_at=timezone.now(),
            )
            membership, membership_created = TeamMembership.objects.get_or_create(
                player=player,
                team=team,
                is_active=True,
                defaults={"role": member_spec["role"]},
            )
            if membership_created:
                continue
            membership_changed = False
            if membership.role != member_spec["role"]:
                membership.role = member_spec["role"]
                membership_changed = True
            if not membership.is_active:
                membership.is_active = True
                membership.left_at = None
                membership_changed = True
            if membership_changed:
                membership.save(update_fields=["role", "is_active", "left_at"])

        return team, created, captain

    def _upsert_tournament(self, name, *, status, format, description):
        defaults = {
            "tournament_type": Tournament.TEAM,
            "format": format,
            "status": status,
            "max_teams": 8,
            "registration_deadline": demo_deadline(),
            "start_date": demo_start_date(),
            "end_date": demo_end_date(),
            "description": description,
            "tiebreaker_rules": ["goal_difference", "goals_for", "team_name"],
        }
        tournament, created = Tournament.objects.get_or_create(name=name, defaults=defaults)
        changed = created
        for field, value in defaults.items():
            if getattr(tournament, field) != value:
                setattr(tournament, field, value)
                changed = True
        if changed:
            tournament.save()
        return {"tournament": tournament, "_created": created}

    def _upsert_registration(self, tournament, team, *, seed, group_label):
        registration, created = TournamentRegistration.objects.get_or_create(
            tournament=tournament,
            team=team,
            defaults={"seed": seed, "group_label": group_label, "is_active": True},
        )
        registration.seed = seed
        registration.group_label = group_label
        registration.is_active = True
        registration.full_clean()
        registration.save()
        return {"registration": registration, "_created": created}

    def _sync_result(
        self,
        *,
        fixture,
        status,
        home_score,
        away_score,
        submitted_by,
        reviewed_by,
        screenshot_key,
    ):
        result = fixture.results.filter(status=status).order_by("pk").first()
        created = result is None
        if result is None:
            result = Result(
                fixture=fixture,
                status=status,
                submitted_by=submitted_by,
            )

        result.home_score = home_score
        result.away_score = away_score
        result.submitted_by = submitted_by
        result.screenshot = f"{DEMO_RESULT_SCREENSHOT_PREFIX}{screenshot_key}"
        result.status = status
        if status == Result.APPROVED:
            result.reviewed_by = reviewed_by
            result.reviewed_at = timezone.now()
        else:
            result.reviewed_by = None
            result.reviewed_at = None
        result.save()
        return {"result": result, "_created": created}
