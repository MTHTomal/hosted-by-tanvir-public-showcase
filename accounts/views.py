# accounts/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.db.models import Sum, Count, Q
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.forms import (
    CaptainInviteForm,
    PlayerRegistrationForm,
    PlayerAvailabilityForm,
    PlayerProfileForm,
    StaffAssignmentForm,
    TeamIdentityForm,
    TeamRecruitingForm,
    StaffPlayerIdentityForm,
)
from accounts.marketplace import (
    accept_invitation,
    active_membership_for_player,
    assign_player_to_team,
    captained_teams_for_user,
    create_team_invitation,
    marketplace_status,
    reject_invitation,
    team_open_spots,
    user_captains_team,
)
from accounts.models import Notification, Player, Team, TeamInvitation, TeamMembership
from accounts.notifications import notify_staff, notify_user
from accounts.permissions import (
    admin_required,
    can_manage_player_identity,
    can_manage_team_identity,
    is_admin,
)
from standings.models import PlayerStat, Standing
from standings.services import (
    get_player_career_stats,
    get_player_team_history,
    get_team_historical_stats,
    official_player_stat_rows,
)
from tournament.models import Result, Tournament


LOGIN_ERROR_MESSAGE = "Invalid username/email or password."


def _safe_redirect_target(request, fallback):
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


def _safe_login_redirect(request):
    return _safe_redirect_target(request, "tournament:home")


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


def _captain_alignment_context(team, active_memberships=None):
    def build_context(*, ok, state, message, warning, active_captain_membership, team_captain_membership):
        return {
            "captain_alignment_ok": ok,
            "captain_alignment_state": state,
            "captain_alignment_label": state.replace("_", " ").title(),
            "captain_alignment_message": message,
            "captain_alignment_warning": warning,
            "has_captain_membership": active_captain_membership is not None,
            "current_captain_membership": active_captain_membership,
            "team_captain_membership": team_captain_membership,
        }

    if active_memberships is None:
        active_memberships = list(
            TeamMembership.objects
            .filter(team=team, is_active=True)
            .select_related("player")
        )

    active_captain_membership = next(
        (membership for membership in active_memberships if membership.role == TeamMembership.CAPTAIN),
        None,
    )
    team_captain_membership = (
        next((membership for membership in active_memberships if membership.player_id == team.captain_id), None)
        if team.captain_id else None
    )

    if not team.captain_id:
        return build_context(
            ok=False,
            state="no_captain_set",
            message="No team captain is set on the team record.",
            warning="Set a captain on the team record and confirm an active captain membership exists.",
            active_captain_membership=active_captain_membership,
            team_captain_membership=None,
        )

    if team_captain_membership is None:
        return build_context(
            ok=False,
            state="captain_not_on_active_roster",
            message="The team captain is set, but that player has no active membership on this roster.",
            warning="The captain field points to a player who is not currently on the active roster.",
            active_captain_membership=active_captain_membership,
            team_captain_membership=None,
        )

    if team_captain_membership.role != TeamMembership.CAPTAIN:
        return build_context(
            ok=False,
            state="captain_not_marked_as_captain",
            message="The team captain is on the active roster, but their membership role is not Captain.",
            warning=(
                f"The captain field points to {team.captain.display_name}, "
                f"but their active membership role is {team_captain_membership.get_role_display()}."
            ),
            active_captain_membership=active_captain_membership,
            team_captain_membership=team_captain_membership,
        )

    return build_context(
        ok=True,
        state="aligned",
        message="The team captain matches an active captain membership row.",
        warning="",
        active_captain_membership=active_captain_membership,
        team_captain_membership=team_captain_membership,
    )


def _team_integrity_summary_context(*, captain_alignment, roster_eligibility):
    issues = []
    if not captain_alignment["captain_alignment_ok"]:
        issues.append(captain_alignment["captain_alignment_message"])
    if not roster_eligibility["roster_eligibility_ok"]:
        issues.append(roster_eligibility["roster_eligibility_message"])

    if len(issues) > 1:
        badge_reason = "Multiple issues"
    elif not captain_alignment["captain_alignment_ok"]:
        badge_reason = "Captain mismatch"
    elif not roster_eligibility["roster_eligibility_ok"]:
        badge_reason = "Roster ineligible"
    else:
        badge_reason = ""

    if issues:
        return {
            "team_integrity_ok": False,
            "team_integrity_state": "needs_attention",
            "team_integrity_label": "Needs Attention",
            "team_integrity_message": "This team has one or more integrity checks that need review before tournament operations.",
            "team_integrity_issues": issues,
            "team_integrity_badge_reason": badge_reason,
        }

    return {
        "team_integrity_ok": True,
        "team_integrity_state": "healthy",
        "team_integrity_label": "Healthy",
        "team_integrity_message": "This team is currently ready for tournament operations based on captain alignment and roster size.",
        "team_integrity_issues": [],
        "team_integrity_badge_reason": badge_reason,
    }


def register(request):
    if request.user.is_authenticated:
        return redirect("tournament:home")

    if request.method == "POST":
        form = PlayerRegistrationForm(request.POST)
        if form.is_valid():
            player = form.save()
            role = form.cleaned_data.get("role")
            if role == "owner":
                team = player.captained_teams.order_by("-created_at").first()
                title = "New team registration"
                message = (
                    f"{player.display_name} registered {team.name if team else 'a team'} "
                    "and is waiting for staff review."
                )
                url = reverse("accounts:staff_team_list")
            else:
                title = "New player registration"
                message = f"{player.display_name} created a player account."
                url = reverse("accounts:staff_player_list")
            notify_staff(
                title=title,
                message=message,
                kind=Notification.Kind.REGISTRATION_SUBMITTED,
                url=url,
            )
            messages.success(
                request,
                "Registration submitted. An admin will approve your account shortly."
            )
            return redirect("accounts:login")
    else:
        form = PlayerRegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("tournament:home")

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password")

        # Look up the player by email, then authenticate with their username
        try:
            player = Player.objects.get(email__iexact=email)
        except Player.DoesNotExist:
            messages.error(request, LOGIN_ERROR_MESSAGE)
            return render(request, "accounts/login.html")
        except Player.MultipleObjectsReturned:
            messages.error(request, LOGIN_ERROR_MESSAGE)
            return render(request, "accounts/login.html")

        user = authenticate(request, username=player.username, password=password)

        if user is not None:
            login(request, user)
            return redirect(_safe_login_redirect(request))
        else:
            messages.error(request, LOGIN_ERROR_MESSAGE)

    return render(request, "accounts/login.html")


@require_POST
def logout_view(request):
    logout(request)
    return redirect("tournament:home")


def player_list(request):
    players = Player.objects.filter(is_active=True).order_by("username")
    return render(request, "accounts/player_list.html", {
        "players": players,
    })


def team_list(request):
    teams = Team.objects.filter(is_approved=True).select_related("captain").order_by("name")
    return render(request, "accounts/team_list.html", {
        "teams": teams,
    })


def _form_error_messages(error):
    if hasattr(error, "messages"):
        return error.messages
    return [str(error)]


def marketplace(request):
    available_players = list(
        Player.objects
        .filter(is_active=True, is_staff=False, available_for_recruitment=True)
        .annotate(
            active_team_count=Count(
                "memberships",
                filter=Q(memberships__is_active=True),
                distinct=True,
            )
        )
        .filter(active_team_count=0)
        .order_by("username")
    )
    for player in available_players:
        player.marketplace_career_stats = get_player_career_stats(player)
        player.marketplace_current_membership = player.marketplace_career_stats["current_membership"]

    recruiting_teams = list(
        Team.objects
        .filter(is_approved=True, is_recruiting=True)
        .select_related("captain")
        .annotate(
            active_memberships_count=Count(
                "memberships",
                filter=Q(memberships__is_active=True),
                distinct=True,
            )
        )
        .filter(active_memberships_count__lt=Tournament.TEAM_ROSTER_MAX_PLAYERS)
        .order_by("name")
    )
    for team in recruiting_teams:
        team.marketplace_open_spots = team_open_spots(team)
        team.marketplace_historical_stats = get_team_historical_stats(team)

    captain_recruiting_teams = Team.objects.none()
    if request.user.is_authenticated:
        captain_recruiting_teams = (
            captained_teams_for_user(request.user)
            .filter(is_recruiting=True)
            .annotate(
                active_memberships_count=Count(
                    "memberships",
                    filter=Q(memberships__is_active=True),
                    distinct=True,
                )
            )
            .filter(active_memberships_count__lt=Tournament.TEAM_ROSTER_MAX_PLAYERS)
        )

    return render(request, "accounts/marketplace.html", {
        "available_players": available_players,
        "recruiting_teams": recruiting_teams,
        "captain_recruiting_team_count": captain_recruiting_teams.count(),
        "marketplace_status": marketplace_status(),
    })


@login_required
def marketplace_my_availability(request):
    if request.method == "POST":
        form = PlayerAvailabilityForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Marketplace availability updated.")
            return redirect("marketplace")
    else:
        form = PlayerAvailabilityForm(instance=request.user)

    return render(request, "accounts/marketplace_availability.html", {
        "form": form,
        "active_membership": active_membership_for_player(request.user),
    })


@login_required
def marketplace_team_recruiting(request):
    captain_teams = captained_teams_for_user(request.user)
    if not captain_teams.exists():
        raise PermissionDenied("You must captain a team to manage recruiting.")

    if request.method == "POST":
        form = TeamRecruitingForm(request.POST, captain=request.user)
        if form.is_valid():
            team = form.cleaned_data["team"]
            team.is_recruiting = form.cleaned_data["is_recruiting"]
            team.save(update_fields=["is_recruiting"])
            messages.success(request, f"{team.name} recruiting status updated.")
            return redirect("marketplace")
    else:
        first_team = captain_teams.first()
        form = TeamRecruitingForm(
            captain=request.user,
            initial={
                "team": first_team,
                "is_recruiting": first_team.is_recruiting if first_team else False,
            },
        )

    return render(request, "accounts/marketplace_team_recruiting.html", {
        "form": form,
        "captain_teams": captain_teams,
    })


@login_required
def marketplace_invite_player(request, player_id):
    player = get_object_or_404(Player, pk=player_id, is_active=True, is_staff=False)
    if not captained_teams_for_user(request.user).exists():
        raise PermissionDenied("You must captain a team to invite players.")

    if request.method == "POST":
        form = CaptainInviteForm(request.POST, captain=request.user)
        if form.is_valid():
            try:
                create_team_invitation(
                    team=form.cleaned_data["team"],
                    player=player,
                    invited_by=request.user,
                    message=form.cleaned_data.get("message", ""),
                )
            except DjangoValidationError as error:
                for message in _form_error_messages(error):
                    form.add_error(None, message)
            else:
                messages.success(request, f"Invite sent to {player.display_name}.")
                return redirect("marketplace")
    else:
        form = CaptainInviteForm(captain=request.user)

    return render(request, "accounts/marketplace_invite.html", {
        "form": form,
        "invite_player": player,
    })


@login_required
def marketplace_invitations(request):
    invitations = (
        TeamInvitation.objects
        .filter(player=request.user)
        .select_related("team", "invited_by", "responded_by")
        .order_by(
            models.Case(
                models.When(status=TeamInvitation.PENDING, then=models.Value(0)),
                default=models.Value(1),
                output_field=models.IntegerField(),
            ),
            "-created_at",
            "-pk",
        )
    )
    return render(request, "accounts/marketplace_invitations.html", {
        "invitations": invitations,
    })


@login_required
@require_POST
def marketplace_invitation_accept(request, invite_id):
    invitation = get_object_or_404(TeamInvitation, pk=invite_id)
    try:
        accept_invitation(invitation=invitation, player=request.user)
    except DjangoValidationError as error:
        for message in _form_error_messages(error):
            messages.error(request, message)
    else:
        messages.success(request, f"You joined {invitation.team.name}.")
    return redirect("marketplace_invitations")


@login_required
@require_POST
def marketplace_invitation_reject(request, invite_id):
    invitation = get_object_or_404(TeamInvitation, pk=invite_id)
    try:
        reject_invitation(invitation=invitation, player=request.user)
    except DjangoValidationError as error:
        for message in _form_error_messages(error):
            messages.error(request, message)
    else:
        messages.success(request, f"You rejected the invite from {invitation.team.name}.")
    return redirect("marketplace_invitations")


@admin_required
def staff_marketplace(request):
    available_players = (
        Player.objects
        .filter(is_active=True, is_staff=False, available_for_recruitment=True)
        .annotate(
            active_team_count=Count(
                "memberships",
                filter=Q(memberships__is_active=True),
                distinct=True,
            )
        )
        .filter(active_team_count=0)
        .order_by("username")
    )
    recruiting_teams = (
        Team.objects
        .filter(is_recruiting=True)
        .select_related("captain")
        .annotate(
            active_memberships_count=Count(
                "memberships",
                filter=Q(memberships__is_active=True),
                distinct=True,
            )
        )
        .order_by("name")
    )
    pending_invitations = (
        TeamInvitation.objects
        .filter(status=TeamInvitation.PENDING)
        .select_related("team", "player", "invited_by")
        .order_by("-created_at")
    )
    return render(request, "accounts/staff_marketplace.html", {
        "available_players": available_players,
        "recruiting_teams": recruiting_teams,
        "pending_invitations": pending_invitations,
        "marketplace_status": marketplace_status(),
    })


@admin_required
def staff_marketplace_assign(request):
    if request.method == "POST":
        form = StaffAssignmentForm(request.POST)
        if form.is_valid():
            try:
                assign_player_to_team(
                    player=form.cleaned_data["player"],
                    team=form.cleaned_data["team"],
                    assigned_by=request.user,
                    note=form.cleaned_data.get("note", ""),
                )
            except DjangoValidationError as error:
                for message in _form_error_messages(error):
                    form.add_error(None, message)
            else:
                messages.success(request, "Marketplace assignment completed.")
                return redirect("staff_marketplace")
    else:
        form = StaffAssignmentForm()

    return render(request, "accounts/staff_marketplace_assign.html", {
        "form": form,
    })


@login_required
def dashboard(request):
    player = request.user

    membership = (
        TeamMembership.objects
        .filter(player=player, is_active=True)
        .select_related("team")
        .first()
    )

    stat_totals = PlayerStat.objects.filter(player=player).aggregate(
        goals=Sum("goals"),
        assists=Sum("assists"),
        yellow_cards=Sum("yellow_cards"),
        red_cards=Sum("red_cards"),
    )

    recent_stats = (
        PlayerStat.objects
        .filter(player=player)
        .select_related("fixture__home_team", "fixture__away_team", "fixture__tournament")
        .prefetch_related("fixture__results")
        .order_by("-fixture__match_date", "-created_at")[:10]
    )

    pending_submissions = (
        Result.objects
        .filter(submitted_by=player, status__in=[Result.PENDING, Result.REJECTED, Result.DISPUTED])
        .select_related("fixture__home_team", "fixture__away_team")
        .order_by("-submitted_at")[:5]
    )
    recent_notifications = Notification.objects.filter(user=player).order_by("-created_at")[:5]

    profile_form = PlayerProfileForm(instance=player)

    return render(request, "accounts/dashboard.html", {
        "is_admin_user": is_admin(player),
        "membership": membership,
        "can_manage_current_team_recruiting": (
            user_captains_team(player, membership.team) if membership else False
        ),
        "career_goals": stat_totals["goals"] or 0,
        "career_assists": stat_totals["assists"] or 0,
        "career_yellow_cards": stat_totals["yellow_cards"] or 0,
        "career_red_cards": stat_totals["red_cards"] or 0,
        "recent_stats": recent_stats,
        "pending_submissions": pending_submissions,
        "recent_notifications": recent_notifications,
        "profile_form": profile_form,
    })


@login_required
def update_profile(request):
    if request.method != "POST":
        return redirect("accounts:dashboard")

    form = PlayerProfileForm(request.POST, request.FILES, instance=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, "Profile updated successfully.")
        return redirect("accounts:dashboard")

    player = request.user
    membership = (
        TeamMembership.objects
        .filter(player=player, is_active=True)
        .select_related("team")
        .first()
    )
    stat_totals = PlayerStat.objects.filter(player=player).aggregate(
        goals=Sum("goals"),
        assists=Sum("assists"),
        yellow_cards=Sum("yellow_cards"),
        red_cards=Sum("red_cards"),
    )
    recent_stats = (
        PlayerStat.objects
        .filter(player=player)
        .select_related("fixture__home_team", "fixture__away_team", "fixture__tournament")
        .prefetch_related("fixture__results")
        .order_by("-fixture__match_date", "-created_at")[:10]
    )
    pending_submissions = (
        Result.objects
        .filter(submitted_by=player, status__in=[Result.PENDING, Result.REJECTED, Result.DISPUTED])
        .select_related("fixture__home_team", "fixture__away_team")
        .order_by("-submitted_at")[:5]
    )
    recent_notifications = Notification.objects.filter(user=player).order_by("-created_at")[:5]
    return render(request, "accounts/dashboard.html", {
        "is_admin_user": is_admin(player),
        "membership": membership,
        "can_manage_current_team_recruiting": (
            user_captains_team(player, membership.team) if membership else False
        ),
        "career_goals": stat_totals["goals"] or 0,
        "career_assists": stat_totals["assists"] or 0,
        "career_yellow_cards": stat_totals["yellow_cards"] or 0,
        "career_red_cards": stat_totals["red_cards"] or 0,
        "recent_stats": recent_stats,
        "pending_submissions": pending_submissions,
        "recent_notifications": recent_notifications,
        "profile_form": form,
        "show_edit_form": True,
    })


def player_profile(request, username):
    profile_player = get_object_or_404(Player, username=username)

    career_stats = get_player_career_stats(profile_player)
    team_history = get_player_team_history(profile_player)
    current_tournament_stats = career_stats["current_tournament_stats"]

    recent_stats = (
        official_player_stat_rows(player=profile_player)
        .select_related("fixture__home_team", "fixture__away_team", "fixture__tournament")
        .order_by("-fixture__match_date", "-created_at")[:5]
    )

    return render(request, "accounts/profile.html", {
        "profile_player": profile_player,
        "membership": career_stats["current_membership"],
        "career_stats": career_stats,
        "team_history": team_history,
        "current_tournament_stats": current_tournament_stats,
        "career_goals": career_stats["total_goals"],
        "career_assists": career_stats["total_assists"] or 0,
        "career_yellow_cards": career_stats["total_yellow_cards"],
        "career_red_cards": career_stats["total_red_cards"],
        "recent_stats": recent_stats,
        "can_edit_player_identity": can_manage_player_identity(request.user),
    })


def team_detail(request, pk):
    team = get_object_or_404(Team.objects.select_related("captain"), pk=pk)
    is_team_member = False
    if request.user.is_authenticated:
        is_team_member = TeamMembership.objects.filter(
            player=request.user,
            team=team,
            is_active=True,
        ).exists()
    if not team.is_approved and not is_admin(request.user) and not is_team_member:
        team = get_object_or_404(Team.objects.filter(is_approved=True), pk=pk)

    memberships = (
        TeamMembership.objects
        .filter(team=team, is_active=True)
        .select_related("player")
        .order_by("role", "joined_at")
    )

    historical_stats = get_team_historical_stats(team)
    tournament_history = historical_stats["tournament_history"]

    return render(request, "accounts/team_detail.html", {
        "team": team,
        "memberships": memberships,
        "standings": tournament_history,
        "tournament_history": tournament_history,
        "historical_stats": historical_stats,
        "total_played": historical_stats["total_played"],
        "total_wins": historical_stats["total_wins"],
        "total_goals": historical_stats["total_goals_for"],
        "total_points": historical_stats["total_points"],
        "can_edit_team_identity": can_manage_team_identity(request.user, team),
        "can_manage_team_recruiting": user_captains_team(request.user, team),
    })


@login_required
def team_edit(request, pk):
    team = get_object_or_404(Team.objects.select_related("captain"), pk=pk)
    if not can_manage_team_identity(request.user, team):
        raise PermissionDenied("You do not have permission to edit this team.")

    if request.method == "POST":
        form = TeamIdentityForm(request.POST, request.FILES, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, "Team details updated successfully.")
            return redirect("accounts:team_detail", pk=team.pk)
    else:
        form = TeamIdentityForm(instance=team)

    return render(request, "accounts/team_edit.html", {
        "team": team,
        "form": form,
    })


@admin_required
def staff_player_edit(request, username):
    player = get_object_or_404(Player, username=username)

    if request.method == "POST":
        form = StaffPlayerIdentityForm(request.POST, request.FILES, instance=player)
        if form.is_valid():
            form.save()
            messages.success(request, "Player identity updated successfully.")
            return redirect("accounts:profile", username=player.username)
    else:
        form = StaffPlayerIdentityForm(instance=player)

    return render(request, "accounts/player_edit.html", {
        "profile_player": player,
        "form": form,
    })


@admin_required
def staff_team_detail(request, pk):
    team = get_object_or_404(Team.objects.select_related("captain"), pk=pk)

    active_memberships = list(
        TeamMembership.objects
        .filter(team=team, is_active=True)
        .select_related("player")
        .order_by("role", "joined_at", "player__username")
    )
    inactive_memberships = list(
        TeamMembership.objects
        .filter(team=team, is_active=False)
        .select_related("player")
        .order_by("-left_at", "-joined_at", "player__username")
    )
    standings = (
        Standing.objects
        .filter(team=team)
        .select_related("tournament")
        .order_by("-tournament__start_date")
    )
    registrations = list(
        team.tournament_entries
        .select_related("tournament")
        .order_by("-is_active", "tournament__name", "-registered_at")
    )
    captain_alignment = _captain_alignment_context(team, active_memberships=active_memberships)
    roster_eligibility = Tournament.team_roster_eligibility_for_count(len(active_memberships))
    team_integrity_summary = _team_integrity_summary_context(
        captain_alignment=captain_alignment,
        roster_eligibility=roster_eligibility,
    )

    context = {
        "team": team,
        "active_memberships": active_memberships,
        "inactive_memberships": inactive_memberships,
        "standings": standings[:5],
        "registrations": registrations[:5],
        "active_membership_count": len(active_memberships),
        "inactive_membership_count": len(inactive_memberships),
        "total_membership_count": len(active_memberships) + len(inactive_memberships),
    }
    context.update(captain_alignment)
    context.update(roster_eligibility)
    context.update(team_integrity_summary)
    return render(request, "accounts/staff_team_detail.html", context)


@admin_required
def staff_team_list(request):
    teams = (
        Team.objects
        .select_related("captain")
        .annotate(
            active_memberships_count=Count(
                "memberships",
                filter=Q(memberships__is_active=True),
                distinct=True,
            )
        )
        .order_by("is_approved", "name")
    )
    teams = list(teams)
    memberships_by_team = {}
    team_ids = [team.pk for team in teams]
    if team_ids:
        for membership in (
            TeamMembership.objects
            .filter(team_id__in=team_ids, is_active=True)
            .select_related("player")
            .order_by("team_id", "role", "joined_at", "player__username")
        ):
            memberships_by_team.setdefault(membership.team_id, []).append(membership)

    for team in teams:
        team.captain_alignment = _captain_alignment_context(
            team,
            active_memberships=memberships_by_team.get(team.pk, []),
        )
        team.roster_eligibility = Tournament.team_roster_eligibility_for_count(
            getattr(team, "active_memberships_count", 0)
        )
        team.team_integrity_summary = _team_integrity_summary_context(
            captain_alignment=team.captain_alignment,
            roster_eligibility=team.roster_eligibility,
        )

    return render(request, "accounts/staff_team_list.html", {
        "teams": teams,
    })


@admin_required
def staff_team_approve(request, pk):
    if request.method != "POST":
        return redirect("accounts:staff_team_list")
    team = get_object_or_404(Team, pk=pk)
    team.is_approved = True
    team.save(update_fields=["is_approved"])
    for user in _team_notification_users(team):
        notify_user(
            user,
            title="Registration approved",
            message=f"{team.name} has been approved by staff.",
            kind=Notification.Kind.REGISTRATION_APPROVED,
            url=reverse("accounts:team_detail", args=[team.pk]),
        )
    messages.success(request, f"{team.name} approved. It can now be registered in tournaments.")
    return redirect(_safe_redirect_target(request, "accounts:staff_team_list"))


@admin_required
def staff_team_unapprove(request, pk):
    if request.method != "POST":
        return redirect("accounts:staff_team_list")
    team = get_object_or_404(Team, pk=pk)
    team.is_approved = False
    team.save(update_fields=["is_approved"])
    for user in _team_notification_users(team):
        notify_user(
            user,
            title="Registration rejected",
            message=f"{team.name} has been unapproved by staff.",
            kind=Notification.Kind.REGISTRATION_REJECTED,
            url=reverse("accounts:team_detail", args=[team.pk]),
        )
    messages.warning(request, f"{team.name} unapproved.")
    return redirect(_safe_redirect_target(request, "accounts:staff_team_list"))


@admin_required
def staff_player_list(request):
    players = (
        Player.objects
        .annotate(active_team_count=Count("memberships", filter=Q(memberships__is_active=True), distinct=True))
        .order_by("username")
    )
    return render(request, "accounts/staff_player_list.html", {
        "players": players,
    })


@login_required
@require_POST
def notifications_mark_all_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    next_url = request.META.get("HTTP_REFERER")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("accounts:dashboard")
