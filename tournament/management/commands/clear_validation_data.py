from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError

from accounts.models import Player, Team, TeamMembership
from standings.models import PlayerStat, Standing
from tournament.models import Fixture, Result, Tournament, TournamentRegistration
from tournament.validation_demo import (
    DEMO_RESULT_SCREENSHOT_PREFIX,
    DEMO_TEAM_NAME_SET,
    DEMO_TOURNAMENT_NAME_SET,
    DEMO_USERNAME_SET,
)


class Command(BaseCommand):
    help = "Remove only Tournament 1 validation demo data created by seed_validation_data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many validation demo records would be removed without deleting anything.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            result_qs = Result.objects.filter(
                fixture__tournament__name__in=DEMO_TOURNAMENT_NAME_SET,
                submitted_by__username__in=DEMO_USERNAME_SET,
                screenshot__startswith=DEMO_RESULT_SCREENSHOT_PREFIX,
            )
            player_stat_qs = PlayerStat.objects.filter(
                fixture__tournament__name__in=DEMO_TOURNAMENT_NAME_SET,
            )
            standing_qs = Standing.objects.filter(tournament__name__in=DEMO_TOURNAMENT_NAME_SET)
            registration_qs = TournamentRegistration.objects.filter(
                tournament__name__in=DEMO_TOURNAMENT_NAME_SET,
                team__name__in=DEMO_TEAM_NAME_SET,
            )
            fixture_qs = Fixture.objects.filter(
                tournament__name__in=DEMO_TOURNAMENT_NAME_SET,
                home_team__name__in=DEMO_TEAM_NAME_SET,
            )
            tournament_qs = Tournament.objects.filter(name__in=DEMO_TOURNAMENT_NAME_SET)
            membership_qs = TeamMembership.objects.filter(
                team__name__in=DEMO_TEAM_NAME_SET,
                player__username__in=DEMO_USERNAME_SET,
            )
            team_qs = Team.objects.filter(name__in=DEMO_TEAM_NAME_SET)
            player_qs = Player.objects.filter(username__in=DEMO_USERNAME_SET)

            counts = {
                "tournaments": tournament_qs.count(),
                "registrations": registration_qs.count(),
                "fixtures": fixture_qs.count(),
                "results": result_qs.count(),
                "standings": standing_qs.count(),
                "player_stats": player_stat_qs.count(),
                "teams": team_qs.count(),
                "team_memberships": membership_qs.count(),
                "players": player_qs.count(),
            }
        except (OperationalError, ProgrammingError) as exc:
            transaction.set_rollback(True)
            self.stderr.write(
                self.style.ERROR(
                    "Validation demo cleanup could not inspect the database. "
                    "Run migrations first or point this command at the correct live database."
                )
            )
            self.stderr.write(f"Database error: {exc}")
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run only. No validation demo data was deleted."))
            self.stdout.write(f"Tournaments that would be deleted: {counts['tournaments']}")
            self.stdout.write(f"Registrations that would be deleted: {counts['registrations']}")
            self.stdout.write(f"Fixtures that would be deleted: {counts['fixtures']}")
            self.stdout.write(f"Results that would be deleted: {counts['results']}")
            self.stdout.write(f"Standings that would be deleted: {counts['standings']}")
            self.stdout.write(f"Player stats that would be deleted: {counts['player_stats']}")
            self.stdout.write(f"Teams that would be deleted: {counts['teams']}")
            self.stdout.write(f"Team memberships that would be deleted: {counts['team_memberships']}")
            self.stdout.write(f"Players that would be deleted: {counts['players']}")
            transaction.set_rollback(True)
            return

        result_count, _ = result_qs.delete()
        player_stat_count, _ = player_stat_qs.delete()
        standing_count, _ = standing_qs.delete()
        registration_count, _ = registration_qs.delete()
        fixture_count, _ = fixture_qs.delete()
        tournament_count, _ = tournament_qs.delete()
        membership_count, _ = membership_qs.delete()
        team_count, _ = team_qs.delete()
        player_count, _ = player_qs.delete()

        self.stdout.write(self.style.SUCCESS("Cleared Tournament 1 validation demo data."))
        self.stdout.write(f"Deleted tournaments: {tournament_count}")
        self.stdout.write(f"Deleted registrations: {registration_count}")
        self.stdout.write(f"Deleted fixtures: {fixture_count}")
        self.stdout.write(f"Deleted results: {result_count}")
        self.stdout.write(f"Deleted standings: {standing_count}")
        self.stdout.write(f"Deleted player stats: {player_stat_count}")
        self.stdout.write(f"Deleted teams: {team_count}")
        self.stdout.write(f"Deleted team memberships: {membership_count}")
        self.stdout.write(f"Deleted players: {player_count}")
