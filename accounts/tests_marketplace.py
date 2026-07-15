import uuid

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Notification, Player, Team, TeamInvitation, TeamMembership


def make_player(username, *, available=False, is_staff=False):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        unique_id=uuid.uuid4().hex[:12],
        available_for_recruitment=available,
        is_staff=is_staff,
    )


def make_team(name, *, captain=None, recruiting=True, approved=True):
    team = Team.objects.create(
        name=name,
        captain=captain,
        is_recruiting=recruiting,
        is_approved=approved,
    )
    if captain:
        TeamMembership.objects.create(
            player=captain,
            team=team,
            role=TeamMembership.CAPTAIN,
        )
    return team


def add_member(team, username, *, role=TeamMembership.PLAYER):
    player = make_player(username)
    TeamMembership.objects.create(player=player, team=team, role=role)
    return player


class TeamInvitationModelTests(TestCase):
    def test_team_invitation_can_be_created(self):
        captain = make_player("modelcaptain")
        player = make_player("modelplayer", available=True)
        team = make_team("Model FC", captain=captain)

        invitation = TeamInvitation.objects.create(
            team=team,
            player=player,
            invited_by=captain,
            message="Join us",
        )

        self.assertEqual(invitation.status, TeamInvitation.PENDING)
        self.assertTrue(invitation.is_pending)

    def test_duplicate_pending_invite_is_blocked(self):
        captain = make_player("dupecaptain")
        player = make_player("dupeplayer", available=True)
        team = make_team("Duplicate FC", captain=captain)
        TeamInvitation.objects.create(team=team, player=player, invited_by=captain)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TeamInvitation.objects.create(team=team, player=player, invited_by=captain)

    def test_status_transition_fields_can_be_recorded(self):
        captain = make_player("transitioncaptain")
        player = make_player("transitionplayer", available=True)
        team = make_team("Transition FC", captain=captain)
        invitation = TeamInvitation.objects.create(team=team, player=player, invited_by=captain)

        invitation.status = TeamInvitation.REJECTED
        invitation.responded_at = timezone.now()
        invitation.responded_by = player
        invitation.save()

        invitation.refresh_from_db()
        self.assertEqual(invitation.status, TeamInvitation.REJECTED)
        self.assertIsNotNone(invitation.responded_at)
        self.assertEqual(invitation.responded_by, player)


class MarketplacePageTests(TestCase):
    def test_marketplace_page_loads_and_shows_available_players(self):
        player = make_player("availablepage", available=True)

        response = self.client.get(reverse("marketplace"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, player.display_name)

    def test_unavailable_players_are_hidden_from_available_listing(self):
        available = make_player("shownplayer", available=True)
        unavailable = make_player("hiddenplayer", available=False)

        response = self.client.get(reverse("marketplace"))

        self.assertContains(response, available.display_name)
        self.assertNotContains(response, unavailable.display_name)

    def test_recruiting_teams_are_shown_and_non_recruiting_teams_hidden(self):
        captain = make_player("listingcaptain")
        closed_captain = make_player("closedlistingcaptain")
        recruiting = make_team("Recruiting Listing FC", captain=captain, recruiting=True)
        non_recruiting = make_team("Closed Listing FC", captain=closed_captain, recruiting=False)

        response = self.client.get(reverse("marketplace"))

        self.assertContains(response, recruiting.name)
        self.assertNotContains(response, non_recruiting.name)

    def test_marketplace_does_not_expose_player_email_addresses(self):
        player = make_player("emailhidden", available=True)
        player.email = "private-player@example.com"
        player.save(update_fields=["email"])

        response = self.client.get(reverse("marketplace"))

        self.assertContains(response, player.display_name)
        self.assertNotContains(response, "private-player@example.com")


class PlayerAvailabilityTests(TestCase):
    def test_anonymous_user_is_blocked_from_availability_page(self):
        response = self.client.get(reverse("marketplace_my_availability"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_player_can_toggle_own_availability(self):
        player = make_player("toggleplayer")
        self.client.login(username=player.username, password="testpass123")

        response = self.client.post(
            reverse("marketplace_my_availability"),
            {"available_for_recruitment": "on"},
        )

        self.assertEqual(response.status_code, 302)
        player.refresh_from_db()
        self.assertTrue(player.available_for_recruitment)

    def test_player_cannot_toggle_another_players_availability(self):
        player = make_player("ownavailability")
        other = make_player("otheravailability")
        self.client.login(username=player.username, password="testpass123")

        self.client.post(
            reverse("marketplace_my_availability"),
            {"available_for_recruitment": "on"},
        )

        player.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(player.available_for_recruitment)
        self.assertFalse(other.available_for_recruitment)


class TeamRecruitingTests(TestCase):
    def test_captain_can_toggle_own_team_recruiting(self):
        captain = make_player("recruitcaptain")
        team = make_team("Recruit Toggle FC", captain=captain, recruiting=False)
        self.client.login(username=captain.username, password="testpass123")

        response = self.client.post(
            reverse("marketplace_team_recruiting"),
            {"team": team.pk, "is_recruiting": "on"},
        )

        self.assertEqual(response.status_code, 302)
        team.refresh_from_db()
        self.assertTrue(team.is_recruiting)

    def test_non_captain_cannot_toggle_team_recruiting(self):
        player = make_player("notcaptain")
        self.client.login(username=player.username, password="testpass123")

        response = self.client.get(reverse("marketplace_team_recruiting"))

        self.assertEqual(response.status_code, 403)

    def test_captain_cannot_manage_another_team(self):
        captain = make_player("owncaptain")
        other_captain = make_player("othercaptain")
        own_team = make_team("Own Team FC", captain=captain, recruiting=False)
        other_team = make_team("Other Team FC", captain=other_captain, recruiting=False)
        self.client.login(username=captain.username, password="testpass123")

        response = self.client.post(
            reverse("marketplace_team_recruiting"),
            {"team": other_team.pk, "is_recruiting": "on"},
        )

        self.assertEqual(response.status_code, 200)
        own_team.refresh_from_db()
        other_team.refresh_from_db()
        self.assertFalse(own_team.is_recruiting)
        self.assertFalse(other_team.is_recruiting)


class CaptainInviteTests(TestCase):
    def setUp(self):
        self.captain = make_player("invitecaptain")
        self.team = make_team("Invite FC", captain=self.captain, recruiting=True)
        self.player = make_player("inviteplayer", available=True)

    def post_invite(self, player=None, team=None):
        return self.client.post(
            reverse("marketplace_invite_player", args=[(player or self.player).pk]),
            {"team": (team or self.team).pk, "message": "Come play"},
        )

    def test_captain_can_invite_available_player(self):
        self.client.login(username=self.captain.username, password="testpass123")

        response = self.post_invite()

        self.assertEqual(response.status_code, 302)
        invitation = TeamInvitation.objects.get(team=self.team, player=self.player)
        self.assertEqual(invitation.status, TeamInvitation.PENDING)
        self.assertTrue(
            Notification.objects.filter(
                user=self.player,
                kind=Notification.Kind.MARKETPLACE_INVITE,
            ).exists()
        )

    def test_non_captain_cannot_invite(self):
        player = make_player("plaininviteuser")
        self.client.login(username=player.username, password="testpass123")

        response = self.post_invite()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(TeamInvitation.objects.exists())

    def test_captain_cannot_invite_to_team_they_do_not_captain(self):
        other_captain = make_player("inviteothercaptain")
        other_team = make_team("Other Invite FC", captain=other_captain, recruiting=True)
        self.client.login(username=self.captain.username, password="testpass123")

        response = self.post_invite(team=other_team)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeamInvitation.objects.exists())

    def test_cannot_invite_unavailable_player(self):
        unavailable = make_player("unavailableinvite", available=False)
        self.client.login(username=self.captain.username, password="testpass123")

        response = self.post_invite(player=unavailable)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeamInvitation.objects.exists())

    def test_cannot_invite_already_rostered_player(self):
        rostered = make_player("rosteredinvite", available=True)
        other_captain = make_player("rosteredcaptain")
        other_team = make_team("Rostered Other FC", captain=other_captain, recruiting=True)
        TeamMembership.objects.create(player=rostered, team=other_team, role=TeamMembership.PLAYER)
        self.client.login(username=self.captain.username, password="testpass123")

        response = self.post_invite(player=rostered)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeamInvitation.objects.filter(player=rostered).exists())

    def test_cannot_invite_to_full_team(self):
        add_member(self.team, "fullinviteone")
        add_member(self.team, "fullinvitetwo")
        self.client.login(username=self.captain.username, password="testpass123")

        response = self.post_invite()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeamInvitation.objects.exists())

    def test_duplicate_pending_invite_is_prevented(self):
        TeamInvitation.objects.create(team=self.team, player=self.player, invited_by=self.captain)
        self.client.login(username=self.captain.username, password="testpass123")

        response = self.post_invite()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(TeamInvitation.objects.count(), 1)


class InviteResponseTests(TestCase):
    def setUp(self):
        self.captain = make_player("responsecaptain")
        self.team = make_team("Response FC", captain=self.captain, recruiting=True)
        self.player = make_player("responseplayer", available=True)
        self.invitation = TeamInvitation.objects.create(
            team=self.team,
            player=self.player,
            invited_by=self.captain,
        )

    def test_invited_player_can_accept(self):
        self.client.login(username=self.player.username, password="testpass123")

        response = self.client.post(reverse("marketplace_invitation_accept", args=[self.invitation.pk]))

        self.assertEqual(response.status_code, 302)
        self.invitation.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(self.invitation.status, TeamInvitation.ACCEPTED)
        self.assertIsNotNone(self.invitation.responded_at)
        self.assertFalse(self.player.available_for_recruitment)
        self.assertTrue(
            TeamMembership.objects.filter(
                player=self.player,
                team=self.team,
                role=TeamMembership.PLAYER,
                is_active=True,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.captain,
                kind=Notification.Kind.MARKETPLACE_INVITE_ACCEPTED,
            ).exists()
        )

    def test_invited_player_can_reject(self):
        self.client.login(username=self.player.username, password="testpass123")

        response = self.client.post(reverse("marketplace_invitation_reject", args=[self.invitation.pk]))

        self.assertEqual(response.status_code, 302)
        self.invitation.refresh_from_db()
        self.player.refresh_from_db()
        self.assertEqual(self.invitation.status, TeamInvitation.REJECTED)
        self.assertTrue(self.player.available_for_recruitment)
        self.assertFalse(TeamMembership.objects.filter(player=self.player, team=self.team).exists())
        self.assertTrue(
            Notification.objects.filter(
                user=self.captain,
                kind=Notification.Kind.MARKETPLACE_INVITE_REJECTED,
            ).exists()
        )

    def test_other_player_cannot_accept_or_reject_someone_elses_invite(self):
        other = make_player("responseother", available=True)
        self.client.login(username=other.username, password="testpass123")

        accept_response = self.client.post(reverse("marketplace_invitation_accept", args=[self.invitation.pk]))
        reject_response = self.client.post(reverse("marketplace_invitation_reject", args=[self.invitation.pk]))

        self.assertEqual(accept_response.status_code, 403)
        self.assertEqual(reject_response.status_code, 403)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.status, TeamInvitation.PENDING)

    def test_accept_respects_capacity_at_response_time(self):
        add_member(self.team, "capacityresponseone")
        add_member(self.team, "capacityresponsetwo")
        self.client.login(username=self.player.username, password="testpass123")

        response = self.client.post(reverse("marketplace_invitation_accept", args=[self.invitation.pk]))

        self.assertEqual(response.status_code, 302)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.status, TeamInvitation.PENDING)
        self.assertFalse(TeamMembership.objects.filter(player=self.player, team=self.team).exists())

    def test_accept_updates_team_recruiting_if_team_becomes_full(self):
        add_member(self.team, "fullresponseone")
        self.client.login(username=self.player.username, password="testpass123")

        response = self.client.post(reverse("marketplace_invitation_accept", args=[self.invitation.pk]))

        self.assertEqual(response.status_code, 302)
        self.team.refresh_from_db()
        self.assertFalse(self.team.is_recruiting)

    def test_reject_does_not_create_membership(self):
        self.client.login(username=self.player.username, password="testpass123")

        self.client.post(reverse("marketplace_invitation_reject", args=[self.invitation.pk]))

        self.assertFalse(TeamMembership.objects.filter(player=self.player, team=self.team).exists())


class StaffAssignmentTests(TestCase):
    def setUp(self):
        self.staff = make_player("marketstaff", is_staff=True)
        self.captain = make_player("assigncaptain")
        self.team = make_team("Assign FC", captain=self.captain, recruiting=True)
        self.player = make_player("assignplayer", available=True)

    def post_assignment(self, player=None, team=None):
        return self.client.post(
            reverse("staff_marketplace_assign"),
            {
                "player": (player or self.player).pk,
                "team": (team or self.team).pk,
                "note": "Staff placement",
            },
        )

    def test_staff_can_access_assignment_page(self):
        self.client.login(username=self.staff.username, password="testpass123")

        response = self.client.get(reverse("staff_marketplace_assign"))

        self.assertEqual(response.status_code, 200)

    def test_non_staff_cannot_access_assignment_page(self):
        self.client.login(username=self.player.username, password="testpass123")

        response = self.client.get(reverse("staff_marketplace_assign"))

        self.assertEqual(response.status_code, 403)

    def test_staff_can_assign_available_player_to_team(self):
        self.client.login(username=self.staff.username, password="testpass123")

        response = self.post_assignment()

        self.assertEqual(response.status_code, 302)
        self.player.refresh_from_db()
        self.assertFalse(self.player.available_for_recruitment)
        self.assertTrue(
            TeamMembership.objects.filter(
                player=self.player,
                team=self.team,
                role=TeamMembership.PLAYER,
                is_active=True,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.player,
                kind=Notification.Kind.MARKETPLACE_ASSIGNMENT,
            ).exists()
        )

    def test_staff_cannot_assign_to_full_team(self):
        add_member(self.team, "assignfullone")
        add_member(self.team, "assignfulltwo")
        self.client.login(username=self.staff.username, password="testpass123")

        response = self.post_assignment()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeamMembership.objects.filter(player=self.player, team=self.team).exists())

    def test_assignment_updates_availability_and_recruiting_state(self):
        add_member(self.team, "assignalmostfull")
        self.client.login(username=self.staff.username, password="testpass123")

        response = self.post_assignment()

        self.assertEqual(response.status_code, 302)
        self.player.refresh_from_db()
        self.team.refresh_from_db()
        self.assertFalse(self.player.available_for_recruitment)
        self.assertFalse(self.team.is_recruiting)
