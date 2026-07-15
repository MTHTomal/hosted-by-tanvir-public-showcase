from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from accounts.models import TeamMembership


def is_admin(user):
    return bool(user and user.is_authenticated and user.is_staff)


def is_registered_player(user):
    return bool(user and user.is_authenticated and not user.is_staff)


def can_manage_player_identity(user):
    return is_admin(user)


def can_manage_team_identity(user, team):
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    if team.captain_id == user.pk:
        return True
    return TeamMembership.objects.filter(
        player=user,
        team=team,
        role=TeamMembership.CAPTAIN,
        is_active=True,
    ).exists()


def admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if not is_admin(request.user):
            raise PermissionDenied("You do not have permission to access this page.")
        return view_func(request, *args, **kwargs)

    return _wrapped_view


def can_submit_fixture_result(user, fixture):
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    if fixture.is_bye or not fixture.away_team_id:
        return False

    fixture_team_ids = [fixture.home_team_id, fixture.away_team_id]
    return TeamMembership.objects.filter(
        player=user,
        is_active=True,
        team_id__in=fixture_team_ids,
    ).exists()


def can_respond_to_fixture_result(user, result):
    if not user or not user.is_authenticated or user.is_staff:
        return False
    if not getattr(result, "opponent_response_open", False):
        return False

    user_team_ids = set(
        TeamMembership.objects.filter(
            player=user,
            is_active=True,
            team_id__in=[result.fixture.home_team_id, result.fixture.away_team_id],
        ).values_list("team_id", flat=True)
    )

    if not user_team_ids:
        return False
    if result.submitting_team_id in user_team_ids:
        return False
    return result.opponent_team_id in user_team_ids
