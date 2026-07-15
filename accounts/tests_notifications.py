import uuid
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Notification, Player
from accounts.notifications import notify_staff, notify_user, notify_user_with_optional_celery
from accounts.tasks import create_notification_task, create_staff_notifications_task


def make_player(username, *, is_active=True, is_staff=False):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_active=is_active,
        is_staff=is_staff,
        unique_id=uuid.uuid4().hex[:20],
    )


class NotificationHelperTests(TestCase):
    def test_notification_can_be_created_for_user(self):
        player = make_player("notifhelper")

        notification = notify_user(
            player,
            title="Result approved",
            message="Your result was approved.",
            kind=Notification.Kind.RESULT_APPROVED,
            url="/fixture/1/",
        )

        self.assertIsNotNone(notification)
        self.assertEqual(notification.user, player)
        self.assertFalse(notification.is_read)

    def test_missing_invalid_or_inactive_recipients_do_not_crash(self):
        inactive = make_player("notifinactive", is_active=False)

        self.assertIsNone(
            notify_user(
                None,
                title="Ignored",
                message="No recipient.",
                kind=Notification.Kind.RESULT_APPROVED,
            )
        )
        self.assertIsNone(
            notify_user(
                AnonymousUser(),
                title="Ignored",
                message="Anonymous recipient.",
                kind=Notification.Kind.RESULT_APPROVED,
            )
        )
        self.assertIsNone(
            notify_user(
                inactive,
                title="Ignored",
                message="Inactive recipient.",
                kind=Notification.Kind.RESULT_APPROVED,
            )
        )
        self.assertEqual(Notification.objects.count(), 0)

    def test_duplicate_unread_notification_is_reused(self):
        player = make_player("notifdedupe")

        first = notify_user(
            player,
            title="Opponent responded",
            message="The opponent responded.",
            kind=Notification.Kind.OPPONENT_RESPONSE,
            url="/fixture/1/",
        )
        second = notify_user(
            player,
            title="Opponent responded",
            message="The opponent responded.",
            kind=Notification.Kind.OPPONENT_RESPONSE,
            url="/fixture/1/",
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Notification.objects.count(), 1)

    def test_notify_staff_skips_inactive_staff(self):
        active_staff = make_player("notifactivestaff", is_staff=True)
        inactive_staff = make_player("notifinactivestaff", is_staff=True, is_active=False)

        notify_staff(
            title="New result submitted",
            message="A result is ready for review.",
            kind=Notification.Kind.RESULT_SUBMITTED,
            url="/queue/",
        )

        self.assertTrue(Notification.objects.filter(user=active_staff).exists())
        self.assertFalse(Notification.objects.filter(user=inactive_staff).exists())

    @override_settings(NOTIFICATIONS_USE_CELERY=True)
    def test_optional_celery_notification_falls_back_to_sync_when_dispatch_fails(self):
        player = make_player("notiffallback")

        with patch(
            "accounts.tasks.create_notification_task.delay",
            side_effect=RuntimeError("broker down"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                notify_user_with_optional_celery(
                    player,
                    title="Fallback item",
                    message="Celery dispatch failed, so this should still be created.",
                    kind=Notification.Kind.RESULT_APPROVED,
                )

        self.assertTrue(
            Notification.objects.filter(
                user=player,
                title="Fallback item",
                kind=Notification.Kind.RESULT_APPROVED,
            ).exists()
        )


class NotificationTaskTests(TestCase):
    def test_create_notification_task_creates_notification_from_user_id(self):
        player = make_player("notiftask")

        result = create_notification_task.delay(
            player.pk,
            "Task notification",
            "Created from a Celery task.",
            Notification.Kind.RESULT_APPROVED,
            "/fixture/1/",
        )

        notification_id = result.get(timeout=1)
        notification = Notification.objects.get(pk=notification_id)
        self.assertEqual(notification.user, player)
        self.assertEqual(notification.title, "Task notification")

    def test_create_notification_task_skips_missing_user_id(self):
        result = create_notification_task.delay(
            999999,
            "Missing user",
            "This should not create anything.",
            Notification.Kind.RESULT_APPROVED,
        )

        self.assertIsNone(result.get(timeout=1))
        self.assertEqual(Notification.objects.count(), 0)

    def test_create_notification_task_skips_inactive_user(self):
        inactive = make_player("notiftaskinactive", is_active=False)

        result = create_notification_task.delay(
            inactive.pk,
            "Inactive user",
            "This should not create anything.",
            Notification.Kind.RESULT_APPROVED,
        )

        self.assertIsNone(result.get(timeout=1))
        self.assertEqual(Notification.objects.count(), 0)

    def test_create_staff_notifications_task_skips_inactive_staff(self):
        active_staff = make_player("notiftaskactivestaff", is_staff=True)
        inactive_staff = make_player("notiftaskinactivestaff", is_staff=True, is_active=False)

        result = create_staff_notifications_task.delay(
            "Staff task",
            "Created for active staff only.",
            Notification.Kind.RESULT_SUBMITTED,
            "/queue/",
        )

        self.assertEqual(result.get(timeout=1), 1)
        self.assertTrue(Notification.objects.filter(user=active_staff).exists())
        self.assertFalse(Notification.objects.filter(user=inactive_staff).exists())


class NotificationUITests(TestCase):
    def test_new_account_registration_notifies_staff(self):
        staff = make_player("notifregisterstaff", is_staff=True)

        self.client.post(
            reverse("accounts:register"),
            {
                "username": "notifnewowner",
                "email": "notifnewowner@test.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
                "role": "owner",
                "team_name": "New Notify FC",
            },
        )

        self.assertTrue(
            Notification.objects.filter(
                user=staff,
                kind=Notification.Kind.REGISTRATION_SUBMITTED,
                title="New team registration",
            ).exists()
        )

    def test_unread_count_is_available_for_authenticated_user(self):
        player = make_player("notifcount")
        notify_user(
            player,
            title="Unread item",
            message="Dashboard should count this.",
            kind=Notification.Kind.RESULT_APPROVED,
        )

        self.client.login(username="notifcount", password="testpass123")
        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.context["unread_notification_count"], 1)

    def test_navbar_unread_badge_appears_for_authenticated_user(self):
        player = make_player("notifbadge")
        notify_user(
            player,
            title="Badge item",
            message="Navbar should show a count.",
            kind=Notification.Kind.RESULT_APPROVED,
        )

        self.client.login(username="notifbadge", password="testpass123")
        response = self.client.get(reverse("accounts:dashboard"))

        self.assertContains(response, "Badge item")
        self.assertContains(response, ">1</span")

    def test_navbar_unread_badge_does_not_appear_for_anonymous_users(self):
        response = self.client.get(reverse("tournament:home"))

        self.assertNotContains(response, "bg-red-500 text-white text-[10px] font-bold rounded-full")

    def test_dashboard_shows_recent_notifications(self):
        player = make_player("notifdashboard")
        notify_user(
            player,
            title="Dashboard notification",
            message="This should appear in the recent list.",
            kind=Notification.Kind.RESULT_APPROVED,
        )

        self.client.login(username="notifdashboard", password="testpass123")
        response = self.client.get(reverse("accounts:dashboard"))

        self.assertContains(response, "Recent notifications")
        self.assertContains(response, "Dashboard notification")

    def test_mark_all_read_marks_only_current_user_notifications(self):
        player = make_player("notifread")
        other = make_player("notifother")
        own_notification = notify_user(
            player,
            title="Own unread",
            message="Owned by current user.",
            kind=Notification.Kind.RESULT_APPROVED,
        )
        other_notification = notify_user(
            other,
            title="Other unread",
            message="Owned by another user.",
            kind=Notification.Kind.RESULT_APPROVED,
        )

        self.client.login(username="notifread", password="testpass123")
        response = self.client.post(reverse("accounts:notifications_mark_all_read"))

        self.assertEqual(response.status_code, 302)
        own_notification.refresh_from_db()
        other_notification.refresh_from_db()
        self.assertTrue(own_notification.is_read)
        self.assertFalse(other_notification.is_read)

    def test_mark_all_read_requires_authentication(self):
        response = self.client.post(reverse("accounts:notifications_mark_all_read"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])


class DashboardRecentStatsRenderingTest(TestCase):
    """
    Regression test for dashboard recent stats rendering bug.
    Ensures that template variables are not split across lines,
    causing raw template syntax to appear in the rendered HTML.

    Bug: {{ stat.fixture.away_team.name }} was split as:
        {{ stat.fixture.away_team.name
        }}
    which rendered the literal text instead of the team name.
    """

    def setUp(self):
        """Create test data: player, teams, fixture, and player stat"""
        from accounts.models import Team
        from standings.models import PlayerStat
        from tournament.models import Tournament, Fixture

        # Create player
        self.player = make_player("dashplayer")

        # Create tournament with two teams
        self.tournament = Tournament.objects.create(
            name="Test Tournament",
            format=Tournament.ROUND_ROBIN,
            status=Tournament.ACTIVE,
            max_teams=4,
            tournament_type=Tournament.TEAM,
        )

        self.home_team = Team.objects.create(
            name="Home Team",
            is_approved=True
        )
        self.away_team = Team.objects.create(
            name="Away Team",
            is_approved=True
        )

        # Create fixture with distinct team names
        self.fixture = Fixture.objects.create(
            tournament=self.tournament,
            home_team=self.home_team,
            away_team=self.away_team,
            round_number=1,
            stage=Fixture.GROUP,
        )

        # Create player stat for this fixture
        self.stat = PlayerStat.objects.create(
            player=self.player,
            fixture=self.fixture,
            team=self.home_team,
            goals=2,
            assists=1,
            yellow_cards=0,
            red_cards=0,
        )

    def test_dashboard_recent_stats_renders_team_names_correctly(self):
        """
        Test that the Recent Recorded Stats section renders actual team names,
        not raw template syntax.

        This is a regression test for the bug where:
        - Expected: "Home Team vs Away Team"
        - Actual (buggy): "Skill Issue vs {{ stat.fixture.away_team.name }}"
        """
        self.client.login(username="dashplayer", password="testpass123")
        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.status_code, 200)

        # Should contain the expected rendered text
        self.assertContains(response, "Home Team vs Away Team")

        # Should NOT contain any raw template syntax in the recent stats area
        self.assertNotContains(response, "{{ stat.fixture")
        self.assertNotContains(response, "{{ stat.fixture.home_team.name }}")
        self.assertNotContains(response, "{{ stat.fixture.away_team.name }}")

    def test_dashboard_recent_stats_renders_stats_values(self):
        """
        Test that the stat values (goals, assists, cards) are rendered correctly.
        """
        self.client.login(username="dashplayer", password="testpass123")
        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.status_code, 200)

        # Check that stat values appear in the table
        self.assertContains(response, "2")  # goals
        self.assertContains(response, "1")  # assists
