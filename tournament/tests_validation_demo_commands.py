from io import StringIO
from unittest.mock import patch

import django
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.test import override_settings

if not apps.ready:
    django.setup()

from accounts.models import Player, Team
from tournament.models import Fixture, Result, Tournament, TournamentRegistration
from tournament.validation_demo import (
    DEMO_TEAM_NAMES,
    DEMO_TOURNAMENTS,
    DEMO_TOURNAMENT_NAMES,
    DEMO_USER_PREFIX,
)


class ValidationDemoCommandTests(TestCase):
    def test_seed_validation_data_is_idempotent(self):
        output = StringIO()
        generated_password = "generated-demo-password-for-test"

        with patch(
            "tournament.management.commands.seed_validation_data.generate_demo_password",
            return_value=generated_password,
        ) as password_generator:
            call_command("seed_validation_data", stdout=output)
            call_command("seed_validation_data", stdout=output)

        self.assertEqual(password_generator.call_count, 2)
        self.assertEqual(
            output.getvalue().count(f"Shared password for this seed run: {generated_password}"),
            2,
        )
        self.assertTrue(
            all(
                player.check_password(generated_password)
                for player in Player.objects.filter(username__startswith=DEMO_USER_PREFIX)
            )
        )

        self.assertEqual(Player.objects.filter(username__startswith=DEMO_USER_PREFIX).count(), 20)
        self.assertEqual(Team.objects.filter(name__in=DEMO_TEAM_NAMES).count(), 7)
        self.assertEqual(Tournament.objects.filter(name__in=DEMO_TOURNAMENT_NAMES).count(), 2)
        self.assertEqual(
            TournamentRegistration.objects.filter(tournament__name=DEMO_TOURNAMENTS["grouped"]).count(),
            6,
        )
        self.assertEqual(
            TournamentRegistration.objects.filter(tournament__name=DEMO_TOURNAMENTS["non_grouped"]).count(),
            3,
        )
        self.assertEqual(
            Fixture.objects.filter(tournament__name=DEMO_TOURNAMENTS["grouped"]).count(),
            12,
        )
        self.assertEqual(
            Result.objects.filter(fixture__tournament__name=DEMO_TOURNAMENTS["grouped"], status=Result.APPROVED).count(),
            2,
        )
        self.assertEqual(
            Result.objects.filter(fixture__tournament__name=DEMO_TOURNAMENTS["grouped"], status=Result.PENDING).count(),
            1,
        )

    @override_settings(DEBUG=False, IS_TEST=False)
    def test_seed_validation_data_requires_debug_mode(self):
        with self.assertRaises(CommandError):
            call_command("seed_validation_data")

        self.assertFalse(Player.objects.filter(username__startswith=DEMO_USER_PREFIX).exists())

    def test_clear_validation_data_removes_only_demo_records(self):
        real_user = Player.objects.create_user(
            username="real_user",
            password="realpass123",
            email="real@example.com",
            unique_id="REALUSER",
        )
        real_team = Team.objects.create(name="Real Team FC", captain=real_user, is_approved=True)
        real_tournament = Tournament.objects.create(
            name="Real Tournament",
            tournament_type=Tournament.TEAM,
            format=Tournament.ROUND_ROBIN,
            status=Tournament.REGISTRATION,
            max_teams=4,
        )
        TournamentRegistration.objects.create(tournament=real_tournament, team=real_team, is_active=True)

        call_command("seed_validation_data")
        call_command("clear_validation_data")

        self.assertTrue(Player.objects.filter(username="real_user").exists())
        self.assertTrue(Team.objects.filter(name="Real Team FC").exists())
        self.assertTrue(Tournament.objects.filter(name="Real Tournament").exists())
        self.assertFalse(Player.objects.filter(username__startswith=DEMO_USER_PREFIX).exists())
        self.assertFalse(Team.objects.filter(name__in=DEMO_TEAM_NAMES).exists())
        self.assertFalse(Tournament.objects.filter(name__in=DEMO_TOURNAMENT_NAMES).exists())
