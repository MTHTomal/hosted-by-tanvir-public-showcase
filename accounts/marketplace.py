from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import Notification, Player, Team, TeamInvitation, TeamMembership
from accounts.notifications import notify_user
from tournament.models import Tournament


def marketplace_status():
    registration_tournaments = list(
        Tournament.objects
        .filter(status=Tournament.REGISTRATION)
        .order_by("registration_deadline", "start_date", "pk")[:3]
    )
    active_registration_tournaments = [
        tournament for tournament in registration_tournaments if tournament.is_registration_open
    ]

    if len(active_registration_tournaments) == 1:
        tournament = active_registration_tournaments[0]
        return {
            "is_open": True,
            "deadline_enforced": False,
            "deadline": tournament.registration_deadline,
            "tournament": tournament,
            "message": (
                f"{tournament.name} is in registration, but Phase 3.5A keeps "
                "marketplace availability separate from tournament deadlines."
            ),
        }

    if len(active_registration_tournaments) > 1:
        return {
            "is_open": True,
            "deadline_enforced": False,
            "deadline": None,
            "tournament": None,
            "message": (
                "Multiple registration-open tournaments exist, so Phase 3.5A "
                "does not infer one global marketplace deadline."
            ),
        }

    return {
        "is_open": True,
        "deadline_enforced": False,
        "deadline": None,
        "tournament": None,
        "message": (
            "No registration-open tournament deadline is applied to marketplace "
            "actions in Phase 3.5A."
        ),
    }


def captained_teams_for_user(user):
    if not user or not user.is_authenticated:
        return Team.objects.none()
    return (
        Team.objects
        .filter(
            Q(captain=user)
            | Q(
                memberships__player=user,
                memberships__role=TeamMembership.CAPTAIN,
                memberships__is_active=True,
            )
        )
        .distinct()
        .select_related("captain")
    )


def user_captains_team(user, team):
    if not user or not user.is_authenticated or not team:
        return False
    if team.captain_id == user.pk:
        return True
    return TeamMembership.objects.filter(
        player=user,
        team=team,
        role=TeamMembership.CAPTAIN,
        is_active=True,
    ).exists()


def active_membership_for_player(player):
    return (
        TeamMembership.objects
        .filter(player=player, is_active=True)
        .select_related("team")
        .first()
    )


def active_roster_count(team):
    annotated_count = getattr(team, "active_memberships_count", None)
    if annotated_count is not None:
        return annotated_count
    return TeamMembership.objects.filter(team=team, is_active=True).count()


def team_open_spots(team):
    return max(Tournament.TEAM_ROSTER_MAX_PLAYERS - active_roster_count(team), 0)


def team_has_capacity(team):
    return team_open_spots(team) > 0


def _team_notification_users(team):
    users = []
    seen_ids = set()

    def add_user(user):
        if user and user.pk and user.is_active and user.pk not in seen_ids:
            users.append(user)
            seen_ids.add(user.pk)

    add_user(team.captain)
    for membership in (
        TeamMembership.objects
        .filter(team=team, role=TeamMembership.CAPTAIN, is_active=True)
        .select_related("player")
    ):
        add_user(membership.player)
    return users


def _raise_validation(message):
    raise ValidationError(message)


def validate_invite_rules(*, team, player, invited_by=None, staff_override=False):
    if not staff_override and not user_captains_team(invited_by, team):
        raise PermissionDenied("You can only invite players to a team you captain.")
    if not player.is_active or player.is_staff:
        _raise_validation("Only active player accounts can be invited.")
    if invited_by and invited_by.pk == player.pk and not staff_override:
        _raise_validation("Captains cannot invite themselves through the marketplace.")
    if not team_has_capacity(team):
        _raise_validation("This team is full.")
    if active_membership_for_player(player):
        _raise_validation("This player is already on an active roster.")
    if not staff_override and not team.is_recruiting:
        _raise_validation("This team is not marked as recruiting.")
    if not staff_override and not player.available_for_recruitment:
        _raise_validation("This player is not available for recruitment.")
    if TeamInvitation.objects.filter(
        team=team,
        player=player,
        status=TeamInvitation.PENDING,
    ).exists():
        _raise_validation("A pending invite already exists for this team and player.")


def create_team_invitation(*, team, player, invited_by, message=""):
    with transaction.atomic():
        team = Team.objects.select_for_update().get(pk=team.pk)
        player = Player.objects.select_for_update().get(pk=player.pk)
        validate_invite_rules(team=team, player=player, invited_by=invited_by)
        try:
            invitation = TeamInvitation.objects.create(
                team=team,
                player=player,
                invited_by=invited_by,
                message=(message or "").strip(),
            )
        except IntegrityError as exc:
            raise ValidationError("A pending invite already exists for this team and player.") from exc

    notify_user(
        player,
        title=f"Invitation from {team.name}",
        message=f"{team.name} invited you to join their roster.",
        kind=Notification.Kind.MARKETPLACE_INVITE,
        url=reverse("marketplace_invitations"),
    )
    return invitation


def accept_invitation(*, invitation, player):
    timestamp = timezone.now()

    with transaction.atomic():
        invitation = (
            TeamInvitation.objects
            .select_for_update()
            .select_related("team", "player", "invited_by")
            .get(pk=invitation.pk)
        )
        if invitation.player_id != player.pk:
            raise PermissionDenied("You can only respond to your own invitations.")
        if invitation.status != TeamInvitation.PENDING:
            _raise_validation("This invitation is no longer pending.")

        invited_player = Player.objects.select_for_update().get(pk=invitation.player_id)
        team = Team.objects.select_for_update().get(pk=invitation.team_id)

        if not invited_player.available_for_recruitment:
            _raise_validation("You are no longer marked available for recruitment.")
        if active_membership_for_player(invited_player):
            _raise_validation("You are already on an active roster.")
        if not team_has_capacity(team):
            _raise_validation("This team is full.")

        membership = TeamMembership.objects.create(
            player=invited_player,
            team=team,
            role=TeamMembership.PLAYER,
        )
        invitation.status = TeamInvitation.ACCEPTED
        invitation.responded_at = timestamp
        invitation.responded_by = invited_player
        invitation.save(update_fields=["status", "responded_at", "responded_by", "updated_at"])

        invited_player.available_for_recruitment = False
        invited_player.save(update_fields=["available_for_recruitment", "updated_at"])

        if not team_has_capacity(team) and team.is_recruiting:
            team.is_recruiting = False
            team.save(update_fields=["is_recruiting"])

        TeamInvitation.objects.filter(
            player=invited_player,
            status=TeamInvitation.PENDING,
        ).exclude(pk=invitation.pk).update(
            status=TeamInvitation.CANCELLED,
            responded_at=timestamp,
            responded_by=invited_player,
        )

    for user in _team_notification_users(team):
        notify_user(
            user,
            title="Marketplace invite accepted",
            message=f"{invited_player.display_name} accepted the invite to join {team.name}.",
            kind=Notification.Kind.MARKETPLACE_INVITE_ACCEPTED,
            url=reverse("accounts:team_detail", args=[team.pk]),
        )
    return membership


def reject_invitation(*, invitation, player):
    timestamp = timezone.now()

    with transaction.atomic():
        invitation = (
            TeamInvitation.objects
            .select_for_update()
            .select_related("team", "player")
            .get(pk=invitation.pk)
        )
        if invitation.player_id != player.pk:
            raise PermissionDenied("You can only respond to your own invitations.")
        if invitation.status != TeamInvitation.PENDING:
            _raise_validation("This invitation is no longer pending.")

        invitation.status = TeamInvitation.REJECTED
        invitation.responded_at = timestamp
        invitation.responded_by = player
        invitation.save(update_fields=["status", "responded_at", "responded_by", "updated_at"])

    for user in _team_notification_users(invitation.team):
        notify_user(
            user,
            title="Marketplace invite rejected",
            message=f"{player.display_name} rejected the invite to join {invitation.team.name}.",
            kind=Notification.Kind.MARKETPLACE_INVITE_REJECTED,
            url=reverse("marketplace"),
        )
    return invitation


def assign_player_to_team(*, player, team, assigned_by, note=""):
    if not assigned_by or not assigned_by.is_authenticated or not assigned_by.is_staff:
        raise PermissionDenied("Only staff can assign marketplace players.")

    timestamp = timezone.now()
    with transaction.atomic():
        player = Player.objects.select_for_update().get(pk=player.pk)
        team = Team.objects.select_for_update().get(pk=team.pk)

        if not player.is_active or player.is_staff:
            _raise_validation("Only active player accounts can be assigned.")
        if active_membership_for_player(player):
            _raise_validation("This player is already on an active roster.")
        if not team_has_capacity(team):
            _raise_validation("This team is full.")

        membership = TeamMembership.objects.create(
            player=player,
            team=team,
            role=TeamMembership.PLAYER,
        )
        player.available_for_recruitment = False
        player.save(update_fields=["available_for_recruitment", "updated_at"])

        if not team_has_capacity(team) and team.is_recruiting:
            team.is_recruiting = False
            team.save(update_fields=["is_recruiting"])

        TeamInvitation.objects.filter(
            player=player,
            status=TeamInvitation.PENDING,
        ).update(
            status=TeamInvitation.CANCELLED,
            responded_at=timestamp,
            responded_by=assigned_by,
        )

    note = (note or "").strip()
    assignment_message = f"Staff assigned you to {team.name}."
    if note:
        assignment_message = f"{assignment_message} Note: {note}"
    notify_user(
        player,
        title=f"Assigned to {team.name}",
        message=assignment_message,
        kind=Notification.Kind.MARKETPLACE_ASSIGNMENT,
        url=reverse("accounts:team_detail", args=[team.pk]),
    )
    return membership
