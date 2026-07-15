import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Notification, Player, Team, TeamMembership
from tournament.models import Fixture, Result, Tournament, TournamentRegistration


def make_player(username, *, is_staff=False, is_active=True):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_staff=is_staff,
        is_active=is_active,
        unique_id=uuid.uuid4().hex[:20],
    )


def make_team(name, captain):
    team = Team.objects.create(name=name, captain=captain, is_approved=True)
    TeamMembership.objects.create(
        team=team,
        player=captain,
        role=TeamMembership.CAPTAIN,
    )
    return team


def make_tournament(name="Notification Cup", *, status=Tournament.ACTIVE):
    return Tournament.objects.create(
        name=name,
        status=status,
        format=Tournament.ROUND_ROBIN,
        max_teams=4,
    )


def make_fixture(tournament, home, away):
    return Fixture.objects.create(
        tournament=tournament,
        home_team=home,
        away_team=away,
        round_number=1,
        stage=Fixture.GROUP,
    )


class WorkflowNotificationTests(TestCase):
    def setUp(self):
        self.staff = make_player("notifstaff", is_staff=True)
        self.inactive_staff = make_player("notifstaffinactive", is_staff=True, is_active=False)
        self.home_player = make_player("notifhome")
        self.away_player = make_player("notifaway")
        self.home = make_team("Notify Home", self.home_player)
        self.away = make_team("Notify Away", self.away_player)
        self.tournament = make_tournament()
        self.fixture = make_fixture(self.tournament, self.home, self.away)

    def make_pending_result(self, *, home_score=2, away_score=1):
        return Result.objects.create(
            fixture=self.fixture,
            submitted_by=self.home_player,
            submitting_team=self.home,
            home_score=home_score,
            away_score=away_score,
            status=Result.PENDING,
        )

    def test_team_registration_approval_creates_notification_for_captain(self):
        self.home.is_approved = False
        self.home.save(update_fields=["is_approved"])

        self.client.login(username="notifstaff", password="testpass123")
        self.client.post(reverse("accounts:staff_team_approve", args=[self.home.pk]))

        self.assertTrue(
            Notification.objects.filter(
                user=self.home_player,
                kind=Notification.Kind.REGISTRATION_APPROVED,
                title="Registration approved",
            ).exists()
        )

    def test_team_registration_rejection_creates_notification_for_captain(self):
        self.client.login(username="notifstaff", password="testpass123")
        self.client.post(reverse("accounts:staff_team_unapprove", args=[self.home.pk]))

        self.assertTrue(
            Notification.objects.filter(
                user=self.home_player,
                kind=Notification.Kind.REGISTRATION_REJECTED,
                title="Registration rejected",
            ).exists()
        )

    def test_tournament_registration_submission_notifies_staff_and_submitter(self):
        extra_player = make_player("notifhomeextra")
        TeamMembership.objects.create(team=self.home, player=extra_player)
        registration_tournament = make_tournament(
            "Notification Registration Cup",
            status=Tournament.REGISTRATION,
        )
        self.client.login(username="notifhome", password="testpass123")

        self.client.post(
            reverse("tournament:tournament_register", args=[registration_tournament.pk]),
            {"team_id": self.home.pk},
        )

        self.assertTrue(
            Notification.objects.filter(
                user=self.staff,
                kind=Notification.Kind.TOURNAMENT_REGISTRATION_SUBMITTED,
            ).exists()
        )
        self.assertFalse(
            Notification.objects.filter(
                user=self.inactive_staff,
                kind=Notification.Kind.TOURNAMENT_REGISTRATION_SUBMITTED,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.home_player,
                kind=Notification.Kind.TOURNAMENT_REGISTRATION_APPROVED,
            ).exists()
        )

    def test_staff_registration_toggle_rejection_notifies_submitter(self):
        extra_player = make_player("notifinactiveentryextra")
        TeamMembership.objects.create(team=self.home, player=extra_player)
        registration_tournament = make_tournament("Notification Editable Registration")
        registration = TournamentRegistration.objects.create(
            tournament=registration_tournament,
            team=self.home,
            is_active=True,
        )

        self.client.login(username="notifstaff", password="testpass123")
        self.client.post(
            reverse(
                "tournament:staff_tournament_registration_update",
                args=[registration_tournament.pk, registration.pk],
            ),
            {"seed": "", "is_active": ""},
        )

        self.assertTrue(
            Notification.objects.filter(
                user=self.home_player,
                kind=Notification.Kind.TOURNAMENT_REGISTRATION_REJECTED,
            ).exists()
        )

    def test_result_approval_creates_notification_for_submitter(self):
        result = self.make_pending_result()

        self.client.login(username="notifstaff", password="testpass123")
        self.client.post(reverse("tournament:result_approve", args=[result.pk]))

        self.assertTrue(
            Notification.objects.filter(
                user=self.home_player,
                kind=Notification.Kind.RESULT_APPROVED,
            ).exists()
        )

    def test_result_rejection_creates_notification_for_submitter(self):
        result = self.make_pending_result()

        self.client.login(username="notifstaff", password="testpass123")
        self.client.post(reverse("tournament:result_reject", args=[result.pk]))

        self.assertTrue(
            Notification.objects.filter(
                user=self.home_player,
                kind=Notification.Kind.RESULT_REJECTED,
            ).exists()
        )

    def test_staff_dispute_creates_notification_for_submitter(self):
        result = self.make_pending_result()

        self.client.login(username="notifstaff", password="testpass123")
        self.client.post(reverse("tournament:result_dispute", args=[result.pk]))

        self.assertTrue(
            Notification.objects.filter(
                user=self.home_player,
                kind=Notification.Kind.RESULT_DISPUTED,
            ).exists()
        )

    def test_new_result_submission_notifies_staff(self):
        self.client.login(username="notifhome", password="testpass123")

        self.client.post(
            reverse("tournament:result_submit", args=[self.fixture.pk]),
            {"home_score": 3, "away_score": 1},
        )

        self.assertTrue(
            Notification.objects.filter(
                user=self.staff,
                kind=Notification.Kind.RESULT_SUBMITTED,
            ).exists()
        )
        self.assertFalse(
            Notification.objects.filter(
                user=self.inactive_staff,
                kind=Notification.Kind.RESULT_SUBMITTED,
            ).exists()
        )

    def test_opponent_response_notifies_submitter_and_conflict_notifies_staff(self):
        result = self.make_pending_result(home_score=2, away_score=1)

        self.client.login(username="notifaway", password="testpass123")
        self.client.post(
            reverse("tournament:result_opponent_response", args=[result.pk]),
            {
                "opponent_home_score": 1,
                "opponent_away_score": 2,
                "action": Result.OPPONENT_RESPONSE_DISPUTED,
                "note": "Scores are flipped.",
            },
        )

        self.assertTrue(
            Notification.objects.filter(
                user=self.home_player,
                kind=Notification.Kind.OPPONENT_RESPONSE,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.staff,
                kind=Notification.Kind.OPPONENT_SCORE_CONFLICT,
            ).exists()
        )

    def test_fixture_schedule_update_notifies_active_participants(self):
        self.client.login(username="notifstaff", password="testpass123")

        self.client.post(
            reverse("tournament:staff_fixture_schedule_update", args=[self.fixture.pk]),
            {
                "match_date": timezone.localtime(timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
                "submission_deadline": "",
            },
        )

        self.assertTrue(
            Notification.objects.filter(
                user=self.home_player,
                kind=Notification.Kind.FIXTURE_SCHEDULED,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.away_player,
                kind=Notification.Kind.FIXTURE_SCHEDULED,
            ).exists()
        )
