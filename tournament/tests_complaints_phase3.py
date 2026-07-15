import uuid

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import Notification, Player, Team, TeamMembership
from tournament.models import Complaint, Fixture, Result, Tournament


def make_player(username, *, is_staff=False, is_active=True):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_staff=is_staff,
        is_active=is_active,
        unique_id=uuid.uuid4().hex[:20],
    )


def make_team(name, captain=None):
    team = Team.objects.create(name=name, captain=captain, is_approved=True)
    if captain is not None:
        TeamMembership.objects.create(
            team=team,
            player=captain,
            role=TeamMembership.CAPTAIN,
        )
    return team


def make_tournament(name="Complaint Cup"):
    return Tournament.objects.create(
        name=name,
        status=Tournament.ACTIVE,
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


def make_result(fixture, player, team):
    return Result.objects.create(
        fixture=fixture,
        submitted_by=player,
        submitting_team=team,
        home_score=2,
        away_score=1,
        status=Result.PENDING,
    )


class ComplaintModelTests(TestCase):
    def setUp(self):
        self.player = make_player("complaintmodel")
        self.opponent = make_player("complaintopponent")
        self.home = make_team("Complaint Model Home", self.player)
        self.away = make_team("Complaint Model Away", self.opponent)
        self.tournament = make_tournament("Complaint Model Cup")
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.result = make_result(self.fixture, self.player, self.home)

    def test_complaint_can_be_created(self):
        complaint = Complaint.objects.create(
            player=self.player,
            complaint_type=Complaint.GENERAL_REQUEST,
            subject="Need help",
            description="Please review this request.",
        )

        self.assertEqual(complaint.player, self.player)
        self.assertIn("Need help", str(complaint))
        self.assertIn(self.player.username, str(complaint))

    def test_default_status_is_open(self):
        complaint = Complaint.objects.create(
            player=self.player,
            complaint_type=Complaint.SCHEDULE_ISSUE,
            subject="Schedule issue",
            description="The time may not work.",
        )

        self.assertEqual(complaint.status, Complaint.OPEN)

    def test_optional_fixture_and_result_links_work(self):
        complaint = Complaint.objects.create(
            player=self.player,
            complaint_type=Complaint.RESULT_ISSUE,
            subject="Result link",
            description="This is about a linked result.",
            fixture=self.fixture,
            result=self.result,
        )

        self.assertEqual(complaint.fixture, self.fixture)
        self.assertEqual(complaint.result, self.result)

    def test_result_must_match_selected_fixture(self):
        other_tournament = make_tournament("Complaint Other Cup")
        other_home = make_team("Complaint Other Home")
        other_away = make_team("Complaint Other Away")
        other_fixture = make_fixture(other_tournament, other_home, other_away)
        complaint = Complaint(
            player=self.player,
            complaint_type=Complaint.RESULT_ISSUE,
            subject="Mismatched link",
            description="This should fail validation.",
            fixture=other_fixture,
            result=self.result,
        )

        with self.assertRaises(ValidationError):
            complaint.full_clean()


class PlayerComplaintViewTests(TestCase):
    def setUp(self):
        self.player = make_player("complaintplayer")
        self.other_player = make_player("complaintother")
        self.staff = make_player("complaintstaff", is_staff=True)
        self.home = make_team("Complaint Player Home", self.player)
        self.away = make_team("Complaint Player Away", self.other_player)
        self.tournament = make_tournament("Complaint Player Cup")
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.result = make_result(self.fixture, self.player, self.home)
        self.complaint = Complaint.objects.create(
            player=self.player,
            complaint_type=Complaint.GENERAL_REQUEST,
            subject="Own request",
            description="My private request.",
            fixture=self.fixture,
            result=self.result,
        )
        self.other_complaint = Complaint.objects.create(
            player=self.other_player,
            complaint_type=Complaint.SCHEDULE_ISSUE,
            subject="Other request",
            description="Another player's private request.",
        )

    def assert_login_redirect(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_anonymous_cannot_access_complaint_pages(self):
        self.assert_login_redirect(self.client.get(reverse("tournament:complaint_list")))
        self.assert_login_redirect(self.client.get(reverse("tournament:complaint_create")))
        self.assert_login_redirect(
            self.client.get(reverse("tournament:complaint_detail", args=[self.complaint.pk]))
        )

    def test_authenticated_player_can_create_complaint(self):
        self.client.login(username="complaintplayer", password="testpass123")

        response = self.client.post(
            reverse("tournament:complaint_create"),
            {
                "complaint_type": Complaint.RESULT_ISSUE,
                "subject": "Please review",
                "description": "This result needs another look.",
                "fixture": self.fixture.pk,
                "result": self.result.pk,
            },
        )

        complaint = Complaint.objects.get(subject="Please review")
        self.assertRedirects(response, reverse("tournament:complaint_detail", args=[complaint.pk]))
        self.assertEqual(complaint.player, self.player)
        self.assertEqual(complaint.status, Complaint.OPEN)
        self.assertEqual(complaint.fixture, self.fixture)
        self.assertEqual(complaint.result, self.result)

    def test_player_can_see_own_complaint_list_and_detail(self):
        self.client.login(username="complaintplayer", password="testpass123")

        list_response = self.client.get(reverse("tournament:complaint_list"))
        detail_response = self.client.get(
            reverse("tournament:complaint_detail", args=[self.complaint.pk])
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Own request")
        self.assertNotContains(list_response, "Other request")
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "My private request.")

    def test_player_cannot_see_another_players_complaint(self):
        self.client.login(username="complaintplayer", password="testpass123")

        response = self.client.get(
            reverse("tournament:complaint_detail", args=[self.other_complaint.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_player_form_cannot_set_status_or_staff_response(self):
        self.client.login(username="complaintplayer", password="testpass123")

        response = self.client.post(
            reverse("tournament:complaint_create"),
            {
                "complaint_type": Complaint.GENERAL_REQUEST,
                "subject": "Narrow form",
                "description": "Players should not set staff-only fields.",
                "status": Complaint.RESOLVED,
                "staff_response": "Injected response",
            },
        )

        complaint = Complaint.objects.get(subject="Narrow form")
        self.assertRedirects(response, reverse("tournament:complaint_detail", args=[complaint.pk]))
        self.assertEqual(complaint.status, Complaint.OPEN)
        self.assertEqual(complaint.staff_response, "")
        self.assertIsNone(complaint.responded_by)
        self.assertIsNone(complaint.responded_at)

    def test_empty_player_list_renders(self):
        empty_player = make_player("complaintempty")
        self.client.login(username="complaintempty", password="testpass123")

        response = self.client.get(reverse("tournament:complaint_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No complaints or requests yet")


class StaffComplaintViewTests(TestCase):
    def setUp(self):
        self.player = make_player("staffcomplaintplayer")
        self.other_player = make_player("staffcomplaintother")
        self.staff = make_player("staffcomplaintstaff", is_staff=True)
        self.home = make_team("Staff Complaint Home", self.player)
        self.away = make_team("Staff Complaint Away", self.other_player)
        self.tournament = make_tournament("Staff Complaint Cup")
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.result = make_result(self.fixture, self.player, self.home)
        self.complaint = Complaint.objects.create(
            player=self.player,
            complaint_type=Complaint.RESULT_ISSUE,
            subject="Staff queue item",
            description="Staff should be able to manage this.",
            fixture=self.fixture,
            result=self.result,
        )

    def test_anonymous_staff_queue_redirects_to_login(self):
        response = self.client.get(reverse("tournament:staff_complaint_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_staff_can_access_queue(self):
        self.client.login(username="staffcomplaintstaff", password="testpass123")

        response = self.client.get(reverse("tournament:staff_complaint_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff queue item")

    def test_non_staff_cannot_access_queue(self):
        self.client.login(username="staffcomplaintplayer", password="testpass123")

        response = self.client.get(reverse("tournament:staff_complaint_list"))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_view_complaint_detail(self):
        self.client.login(username="staffcomplaintstaff", password="testpass123")

        response = self.client.get(
            reverse("tournament:staff_complaint_detail", args=[self.complaint.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff should be able to manage this.")

    def test_staff_can_update_status_and_add_response(self):
        self.client.login(username="staffcomplaintstaff", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_complaint_detail", args=[self.complaint.pk]),
            {
                "status": Complaint.UNDER_REVIEW,
                "staff_response": "We are reviewing this now.",
            },
        )

        self.assertRedirects(
            response,
            reverse("tournament:staff_complaint_detail", args=[self.complaint.pk]),
        )
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.status, Complaint.UNDER_REVIEW)
        self.assertEqual(self.complaint.staff_response, "We are reviewing this now.")
        self.assertEqual(self.complaint.responded_by, self.staff)
        self.assertIsNotNone(self.complaint.responded_at)

    def test_notification_created_when_staff_responds(self):
        self.client.login(username="staffcomplaintstaff", password="testpass123")

        self.client.post(
            reverse("tournament:staff_complaint_detail", args=[self.complaint.pk]),
            {
                "status": Complaint.RESOLVED,
                "staff_response": "This has been resolved.",
            },
        )

        self.assertTrue(
            Notification.objects.filter(
                user=self.player,
                kind=Notification.Kind.COMPLAINT_RESPONSE,
                title="Complaint/request updated",
                url=reverse("tournament:complaint_detail", args=[self.complaint.pk]),
            ).exists()
        )

    def test_staff_update_form_does_not_change_player_ownership(self):
        self.client.login(username="staffcomplaintstaff", password="testpass123")

        self.client.post(
            reverse("tournament:staff_complaint_detail", args=[self.complaint.pk]),
            {
                "status": Complaint.REJECTED,
                "staff_response": "This is not actionable.",
                "player": self.other_player.pk,
            },
        )

        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.player, self.player)
        self.assertEqual(self.complaint.status, Complaint.REJECTED)

    def test_empty_staff_queue_renders(self):
        Complaint.objects.all().delete()
        self.client.login(username="staffcomplaintstaff", password="testpass123")

        response = self.client.get(reverse("tournament:staff_complaint_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No complaints or requests in this view")
