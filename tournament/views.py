# tournament/views.py
import csv
import re

from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Value, When
from django.urls import reverse
from django.utils import timezone
from tournament.models import Announcement, Complaint, Tournament, TournamentRegistration, Fixture, Result
from tournament.services import get_fixture_prediction
from tournament.forms import (
    OpponentResultResponseForm,
    PlayerComplaintForm,
    StaffComplaintUpdateForm,
    build_result_player_stat_formset,
    validate_result_goal_totals,
    ResultSubmitForm,
    save_result_player_stats,
    StaffFixtureScheduleForm,
    TournamentStaffForm,
    TournamentManualTeamEntryForm,
    TournamentRegistrationStaffForm,
    TournamentGroupAssignmentForm,
)
from standings.models import Standing, PlayerStat
from standings.services import get_head_to_head_stats
from accounts.models import Notification, Team, TeamMembership, Player
from accounts.notifications import notify_staff, notify_user
from accounts.views import _captain_alignment_context, _team_integrity_summary_context
from accounts.permissions import admin_required, can_respond_to_fixture_result, can_submit_fixture_result
from tournament import exporters
from tournament.fixtures import generate_fixtures_for_tournament


def _visible_tournaments_for(user):
    tournaments = Tournament.objects.all()
    if not user.is_authenticated or not user.is_staff:
        tournaments = tournaments.exclude(status=Tournament.DRAFT)
    return tournaments


def _operational_results_queryset():
    return Result.objects.exclude(fixture__tournament__status=Tournament.ARCHIVED)


def _complaint_status_priority():
    return Case(
        When(status=Complaint.OPEN, then=Value(0)),
        When(status=Complaint.UNDER_REVIEW, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )


def _ordered_complaints(queryset):
    return queryset.order_by(_complaint_status_priority(), "-created_at", "-pk")


def _complaints_select_related(queryset):
    return queryset.select_related(
        "player",
        "fixture",
        "fixture__tournament",
        "fixture__home_team",
        "fixture__away_team",
        "result",
        "result__fixture",
        "result__fixture__home_team",
        "result__fixture__away_team",
        "responded_by",
    )


def _annotated_staff_tournaments():
    return Tournament.objects.annotate(
        active_registrations_count=Count(
            "registrations",
            filter=Q(registrations__is_active=True),
            distinct=True,
        ),
        fixtures_count=Count("fixtures", distinct=True),
    )


def _owned_teams_for(user):
    if not user.is_authenticated or user.is_staff:
        return Team.objects.none()
    return Team.objects.filter(
        Q(captain=user) |
        Q(
            memberships__player=user,
            memberships__role=TeamMembership.CAPTAIN,
            memberships__is_active=True,
        )
    ).distinct()


def _active_team_ids_for_user(user):
    if not user.is_authenticated:
        return []
    return list(
        Team.objects.filter(
            Q(captain=user)
            | Q(memberships__player=user, memberships__is_active=True)
        )
        .values_list("pk", flat=True)
        .distinct()
    )


def _unique_active_users(users):
    unique_users = []
    seen_ids = set()
    for user in users:
        if user and user.pk and user.is_active and user.pk not in seen_ids:
            unique_users.append(user)
            seen_ids.add(user.pk)
    return unique_users


def _team_notification_users(team):
    if team is None:
        return []
    users = [team.captain]
    users.extend(
        membership.player
        for membership in (
            TeamMembership.objects
            .filter(team=team, role=TeamMembership.CAPTAIN, is_active=True)
            .select_related("player")
        )
    )
    return _unique_active_users(users)


def _registration_notification_users(registration):
    if registration.player_id:
        return _unique_active_users([registration.player])
    return _team_notification_users(registration.team)


def _fixture_notification_users(fixture):
    if fixture.is_bye or not fixture.away_team_id:
        return []
    return _unique_active_users(
        membership.player
        for membership in (
            TeamMembership.objects
            .filter(
                team_id__in=[fixture.home_team_id, fixture.away_team_id],
                is_active=True,
            )
            .select_related("player")
        )
    )


def _registration_entrant_name(registration):
    if registration.team_id:
        return registration.team.name
    return registration.player.display_name


def _eligible_registration_teams(user, tournament):
    if (
        tournament.tournament_type != Tournament.TEAM
        or not tournament.is_registration_open
        or tournament.registration_count >= tournament.max_teams
    ):
        return Team.objects.none()
    return (
        _owned_teams_for(user)
        .filter(is_approved=True)
        .annotate(
            active_player_count=Count(
                "memberships",
                filter=Q(memberships__is_active=True),
                distinct=True,
            )
        )
        .filter(
            active_player_count__gte=Tournament.TEAM_ROSTER_MIN_PLAYERS,
            active_player_count__lte=Tournament.TEAM_ROSTER_MAX_PLAYERS,
        )
        .exclude(tournament_entries__tournament=tournament)
        .order_by("name")
    )


def _can_register_as_player(user, tournament):
    if (
        not user.is_authenticated
        or user.is_staff
        or tournament.tournament_type != Tournament.SINGLE
        or not tournament.is_registration_open
        or tournament.registration_count >= tournament.max_teams
    ):
        return False
    return not TournamentRegistration.objects.filter(
        tournament=tournament,
        player=user,
    ).exists()


def _attach_registration_context(tournaments, user):
    for tournament in tournaments:
        eligible_teams = list(_eligible_registration_teams(user, tournament))
        tournament.eligible_registration_teams = eligible_teams
        tournament.can_register_as_player = _can_register_as_player(user, tournament)
        tournament.can_register = bool(eligible_teams) or tournament.can_register_as_player
        tournament.registration_panel = _build_registration_panel_context(
            user,
            tournament,
            eligible_teams,
        )
    return tournaments


def _build_registration_panel_context(user, tournament, eligible_registration_teams):
    registration_open = tournament.is_registration_open
    is_full = tournament.registration_count >= tournament.max_teams
    can_register_as_player = _can_register_as_player(user, tournament)
    owned_teams = list(_owned_teams_for(user))
    registered_team_names = []
    already_registered = False

    if user.is_authenticated and not user.is_staff:
        if tournament.is_single_tournament:
            already_registered = TournamentRegistration.objects.filter(
                tournament=tournament,
                player=user,
            ).exists()
        elif owned_teams:
            registered_team_names = list(
                TournamentRegistration.objects.filter(
                    tournament=tournament,
                    team__in=owned_teams,
                )
                .select_related("team")
                .values_list("team__name", flat=True)
            )
            already_registered = bool(registered_team_names)

    if user.is_staff:
        return {
            "variant": "staff",
            "title": "Registration is for players and team owners",
            "message": "Staff accounts can view progress here, but registration actions stay in the player flow.",
            "detail": "",
            "can_register_as_player": False,
            "eligible_registration_teams": [],
            "registered_team_names": [],
            "already_registered": False,
        }

    if not registration_open:
        return {
            "variant": "closed",
            "title": "Registration closed",
            "message": "This tournament is not currently accepting new registrations.",
            "detail": "Check the fixtures and standings below for tournament progress.",
            "can_register_as_player": False,
            "eligible_registration_teams": [],
            "registered_team_names": registered_team_names,
            "already_registered": already_registered,
        }

    if is_full and not already_registered:
        return {
            "variant": "full",
            "title": "Registration full",
            "message": f"All {tournament.max_teams} {tournament.participant_label} slots have been filled.",
            "detail": "No additional registrations can be accepted for this tournament.",
            "can_register_as_player": False,
            "eligible_registration_teams": [],
            "registered_team_names": [],
            "already_registered": False,
        }

    if already_registered:
        detail = ""
        if registered_team_names:
            detail = "Registered team: " + ", ".join(registered_team_names)
        return {
            "variant": "registered",
            "title": "Already registered",
            "message": "Your place in this tournament is already confirmed.",
            "detail": detail,
            "can_register_as_player": False,
            "eligible_registration_teams": [],
            "registered_team_names": registered_team_names,
            "already_registered": True,
        }

    if can_register_as_player:
        return {
            "variant": "single",
            "title": "Register as a player",
            "message": "This single-player tournament is open for individual registration.",
            "detail": "Use the button below to join with your player account.",
            "can_register_as_player": True,
            "eligible_registration_teams": [],
            "registered_team_names": [],
            "already_registered": False,
        }

    if eligible_registration_teams:
        return {
            "variant": "team",
            "title": "Register an eligible team",
            "message": "Choose one of your approved teams with 2 to 3 active players.",
            "detail": "Each button below submits that team directly to this tournament.",
            "can_register_as_player": False,
            "eligible_registration_teams": eligible_registration_teams,
            "registered_team_names": [],
            "already_registered": False,
        }

    if not user.is_authenticated:
        return {
            "variant": "login",
            "title": "Login required to register",
            "message": "You can browse the tournament details now, but you need to sign in before joining.",
            "detail": "Once logged in, eligible players or team owners can register from this panel while registration is open.",
            "can_register_as_player": False,
            "eligible_registration_teams": [],
            "registered_team_names": [],
            "already_registered": False,
        }

    if tournament.is_team_tournament:
        return {
            "variant": "ineligible_team",
            "title": "No eligible team available",
            "message": "You need an approved team you own with 2 to 3 active players before you can register here.",
            "detail": "If you already manage a team, make sure it is approved and has enough active players.",
            "can_register_as_player": False,
            "eligible_registration_teams": [],
            "registered_team_names": [],
            "already_registered": False,
        }

    return {
        "variant": "unavailable",
        "title": "Registration unavailable",
        "message": "You cannot register for this tournament right now.",
        "detail": "",
        "can_register_as_player": False,
        "eligible_registration_teams": [],
        "registered_team_names": [],
        "already_registered": False,
    }


def _validate_team_registration_request(user, tournament, team):
    if not user.is_authenticated:
        raise PermissionDenied("You must be logged in to register a team.")
    if user.is_staff:
        raise PermissionDenied("Admins cannot use the player registration flow.")
    if tournament.tournament_type != Tournament.TEAM:
        raise PermissionDenied("This tournament uses single-player registration.")
    if not tournament.is_registration_open:
        raise PermissionDenied("Tournament registration is closed.")
    if tournament.registration_count >= tournament.max_teams:
        raise PermissionDenied("This tournament is already full.")
    if not team.is_approved:
        raise PermissionDenied("Only approved teams can register for tournaments.")
    if not _owned_teams_for(user).filter(pk=team.pk).exists():
        raise PermissionDenied("You do not own this team.")
    if TournamentRegistration.objects.filter(tournament=tournament, team=team).exists():
        raise PermissionDenied("This team is already registered for the tournament.")


def _validate_single_registration_request(user, tournament):
    if not user.is_authenticated:
        raise PermissionDenied("You must be logged in to register.")
    if user.is_staff:
        raise PermissionDenied("Admins cannot use the player registration flow.")
    if tournament.tournament_type != Tournament.SINGLE:
        raise PermissionDenied("This tournament requires team registration.")
    if not tournament.is_registration_open:
        raise PermissionDenied("Tournament registration is closed.")
    if tournament.registration_count >= tournament.max_teams:
        raise PermissionDenied("This tournament is already full.")
    if TournamentRegistration.objects.filter(tournament=tournament, player=user).exists():
        raise PermissionDenied("You are already registered for this tournament.")


def _message_for_validation_error(error):
    if hasattr(error, "as_data"):
        for field_errors in error.as_data().values():
            if field_errors:
                return field_errors[0].messages[0]
    if hasattr(error, "message_dict"):
        for messages_list in error.message_dict.values():
            if messages_list:
                return messages_list[0]
    if hasattr(error, "messages") and error.messages:
        return error.messages[0]
    return "Registration could not be completed."


def _staff_registration_management_context(tournament):
    add_team_form = None
    add_team_locked_reason = tournament.entrant_change_lock_reason()
    if tournament.tournament_type == Tournament.TEAM:
        add_team_form = TournamentManualTeamEntryForm(tournament=tournament)
        if tournament.registration_count >= tournament.max_teams:
            add_team_locked_reason = (
                f"This tournament already has the maximum of {tournament.max_teams} active teams."
            )
        elif not add_team_locked_reason and not add_team_form.fields["team"].queryset.exists():
            add_team_locked_reason = "No approved eligible teams are currently available to add."
    return add_team_form, add_team_locked_reason


def _staff_tournament_management_context(tournament):
    registrations = TournamentRegistration.objects.filter(tournament=tournament)
    active_registrations = registrations.filter(is_active=True)
    inactive_registrations = registrations.filter(is_active=False)
    lock_reason = tournament.entrant_change_lock_reason()

    return {
        "archive_lock_reason": tournament.archive_lock_reason(),
        "is_archived": tournament.is_archived,
        "entrant_lock_reason": lock_reason,
        "entrant_changes_allowed": lock_reason is None,
        "entrant_lock_state": tournament.entrant_lock_state,
        "active_registration_count": active_registrations.count(),
        "inactive_registration_count": inactive_registrations.count(),
        "total_registration_count": registrations.count(),
        "fixtures_count": tournament.fixtures.count(),
        "has_fixtures": tournament.has_fixtures,
        "has_results": tournament.has_results,
    }


def _staff_group_assignment_context(tournament, registrations):
    active_team_registrations = [
        registration
        for registration in registrations
        if registration.team_id and registration.is_active
    ]
    grouped_registrations = {}

    for registration in active_team_registrations:
        if registration.group_label:
            grouped_registrations.setdefault(registration.group_label, []).append(registration)

    existing_groups = [
        {
            "label": label,
            "registrations": sorted(items, key=lambda item: ((item.seed or 9999), item.team.name)),
        }
        for label, items in sorted(grouped_registrations.items())
    ]
    unassigned_registrations = [
        registration for registration in active_team_registrations if not registration.group_label
    ]

    return {
        "group_assignments_locked": tournament.entrant_change_lock_reason() is not None,
        "group_assignment_lock_reason": tournament.entrant_change_lock_reason(),
        "active_team_registration_count": len(active_team_registrations),
        "assigned_team_registration_count": sum(len(group["registrations"]) for group in existing_groups),
        "unassigned_team_registrations": unassigned_registrations,
        "existing_groups": existing_groups,
    }


def _staff_registrations_groups_redirect_target(tournament):
    return f"{reverse('tournament:staff_tournament_registrations', args=[tournament.pk])}#groups"


def _fixture_display_sections(fixtures):
    grouped_fixtures = any(fixture.group_label for fixture in fixtures)
    elimination_fixtures = any(fixture.stage in {Fixture.KNOCKOUT, Fixture.FINAL} for fixture in fixtures)

    # Non-grouped, non-elimination: organize by round only
    if not grouped_fixtures and not elimination_fixtures:
        fixtures_by_round = {}
        for fixture in fixtures:
            fixtures_by_round.setdefault(fixture.round_number, []).append(fixture)
        return {
            "has_grouped_fixtures": False,
            "fixture_sections": [
                {
                    "group_label": "",
                    "rounds": [
                        {"round_number": round_number, "fixtures": round_fixtures}
                        for round_number, round_fixtures in sorted(fixtures_by_round.items())
                    ],
                }
            ] if fixtures_by_round else [],
        }

    # Grouped tournaments: organize by round first, with group labels on fixtures
    if grouped_fixtures:
        fixtures_by_round = {}
        for fixture in fixtures:
            fixtures_by_round.setdefault(fixture.round_number, []).append(fixture)
        return {
            "has_grouped_fixtures": True,
            "fixture_sections": [
                {
                    "group_label": "",
                    "rounds": [
                        {"round_number": round_number, "fixtures": round_fixtures}
                        for round_number, round_fixtures in sorted(fixtures_by_round.items())
                    ],
                }
            ] if fixtures_by_round else [],
        }

    # Elimination fixtures: organize by stage
    grouped_sections = {}
    for fixture in fixtures:
        if fixture.stage == Fixture.KNOCKOUT:
            label = "Knockout stage"
        elif fixture.stage == Fixture.FINAL:
            label = "Final"
        else:
            label = "Ungrouped"
        round_map = grouped_sections.setdefault(label, {})
        round_map.setdefault(fixture.round_number, []).append(fixture)

    return {
        "has_grouped_fixtures": False,
        "fixture_sections": [
            {
                "group_label": group_label,
                "rounds": [
                    {"round_number": round_number, "fixtures": round_fixtures}
                    for round_number, round_fixtures in sorted(round_map.items())
                ],
            }
            for group_label, round_map in grouped_sections.items()
        ],
    }


def _viewer_side_for_fixture(fixture, viewer_team_ids):
    if fixture.home_team_id in viewer_team_ids:
        return "home"
    if fixture.away_team_id in viewer_team_ids:
        return "away"
    return "none"


def _viewer_relative_score_classes(
    *,
    home_score,
    away_score,
    viewer_side,
    allow_viewer_directional=True,
    allow_draw_highlight=True,
):
    if home_score == away_score and allow_draw_highlight:
        return {
            "chip_class": "score-chip-draw",
            "home_class": "score-draw",
            "away_class": "score-draw",
        }

    neutral_chip_class = "score-chip-neutral bg-gray-100 text-gray-700 border border-gray-200"
    neutral_score_class = "score-viewer-neutral text-gray-600"
    viewer_win_class = "score-viewer-win text-green-700"
    viewer_loss_class = "score-viewer-loss text-red-700"

    if not allow_viewer_directional or viewer_side == "none":
        return {
            "chip_class": neutral_chip_class,
            "home_class": neutral_score_class,
            "away_class": neutral_score_class,
        }

    if viewer_side == "home":
        return {
            "chip_class": neutral_chip_class,
            "home_class": viewer_win_class if home_score > away_score else viewer_loss_class,
            "away_class": neutral_score_class,
        }

    return {
        "chip_class": neutral_chip_class,
        "home_class": neutral_score_class,
        "away_class": viewer_win_class if away_score > home_score else viewer_loss_class,
    }


def _grouped_standings_sections(fixtures):
    grouped_fixtures = [fixture for fixture in fixtures if fixture.group_label]
    if not grouped_fixtures:
        return {
            "has_grouped_standings": False,
            "grouped_standings_sections": [],
        }

    grouped_sections = {}
    for fixture in grouped_fixtures:
        section = grouped_sections.setdefault(
            fixture.group_label,
            {
                "label": fixture.group_label,
                "teams": {},
                "stats": {},
                "total_fixture_count": 0,
                "approved_result_count": 0,
            },
        )
        section["total_fixture_count"] += 1
        section["teams"][fixture.home_team_id] = fixture.home_team
        if fixture.away_team_id:
            section["teams"][fixture.away_team_id] = fixture.away_team

        if fixture.display_result is None:
            continue

        section["approved_result_count"] += 1

        for team in section["teams"].values():
            section["stats"].setdefault(
                team.pk,
                {
                    "team": team,
                    "played": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "goals_for": 0,
                    "goals_against": 0,
                    "points": 0,
                    "goal_difference": 0,
                },
            )

        home_stats = section["stats"][fixture.home_team_id]
        away_stats = section["stats"][fixture.away_team_id]
        result = fixture.display_result

        home_stats["played"] += 1
        away_stats["played"] += 1
        home_stats["goals_for"] += result.home_score
        home_stats["goals_against"] += result.away_score
        away_stats["goals_for"] += result.away_score
        away_stats["goals_against"] += result.home_score

        if result.home_score > result.away_score:
            home_stats["wins"] += 1
            away_stats["losses"] += 1
            home_stats["points"] += 3
        elif result.home_score < result.away_score:
            away_stats["wins"] += 1
            home_stats["losses"] += 1
            away_stats["points"] += 3
        else:
            home_stats["draws"] += 1
            away_stats["draws"] += 1
            home_stats["points"] += 1
            away_stats["points"] += 1

    sections = []
    for label, section in sorted(grouped_sections.items()):
        for team in section["teams"].values():
            section["stats"].setdefault(
                team.pk,
                {
                    "team": team,
                    "played": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "goals_for": 0,
                    "goals_against": 0,
                    "points": 0,
                    "goal_difference": 0,
                },
            )

        standings_rows = []
        if section["approved_result_count"]:
            for row in section["stats"].values():
                row["goal_difference"] = row["goals_for"] - row["goals_against"]
                standings_rows.append(row)
            standings_rows.sort(
                key=lambda row: (
                    -row["points"],
                    -row["goal_difference"],
                    -row["goals_for"],
                    row["team"].name,
                )
            )

        sections.append(
            {
                "label": label,
                "standings_rows": standings_rows,
                "total_fixture_count": section["total_fixture_count"],
                "approved_result_count": section["approved_result_count"],
                "team_names": sorted(team.name for team in section["teams"].values()),
            }
        )

    return {
        "has_grouped_standings": True,
        "grouped_standings_sections": sections,
    }


def _fixtures_with_result_state(tournament):
    fixtures = Fixture.objects.filter(
        tournament=tournament,
        is_bye=False,
    ).select_related("home_team", "away_team").prefetch_related(
        Prefetch(
            "results",
            queryset=(
                Result.objects
                .only(
                    "fixture_id",
                    "home_score",
                    "away_score",
                    "status",
                    "reviewed_at",
                    "submitted_at",
                    "pk",
                )
                .order_by("-reviewed_at", "-submitted_at", "-pk")
            ),
        )
    ).order_by("round_number", "match_date")

    for fixture in fixtures:
        approved_result = None
        has_pending_result = False
        for result in fixture.results.all():
            if result.status == Result.APPROVED and approved_result is None:
                approved_result = result
            elif result.status in [Result.PENDING, Result.DISPUTED]:
                has_pending_result = True

        fixture.display_result = approved_result
        fixture.has_pending_result = has_pending_result
        fixture.fixture_state = (
            "completed" if approved_result
            else "scheduled" if fixture.match_date
            else "unscheduled"
        )

    return fixtures


def tournament_list(request):
    tournaments = _attach_registration_context(
        list(_visible_tournaments_for(request.user)),
        request.user,
    )
    return render(request, "tournament/tournament_list.html", {
        "tournaments": tournaments,
    })


_HOME_INTERNAL_TAG_RE = re.compile(r"\[[^\]]+\]")


def _public_home_text(value):
    """Strip demo-like bracket tags for visitor-facing homepage copy."""
    if not value:
        return ""
    cleaned = _HOME_INTERNAL_TAG_RE.sub("", value)
    return " ".join(cleaned.split()).strip()


def home(request):
    visible_tournaments = _visible_tournaments_for(request.user)
    announcements = Announcement.objects.filter(is_active=True).select_related("tournament").order_by("-is_pinned", "-updated_at", "-created_at")[:5]
    active = visible_tournaments.filter(status=Tournament.ACTIVE).first()
    current_tournaments = visible_tournaments.filter(
        status__in=[Tournament.REGISTRATION, Tournament.ACTIVE]
    )
    total_visible_tournaments = visible_tournaments.count()
    total_approved_teams = Team.objects.filter(is_approved=True).count()
    total_active_players = Player.objects.filter(is_active=True).count()
    featured_tournaments = _attach_registration_context(
        list(
            current_tournaments.exclude(
                pk=active.pk if active else None
            ).order_by("status", "start_date", "name")[:4]
        ),
        request.user,
    )
    registration_open_tournaments = _attach_registration_context(
        list(
            visible_tournaments.filter(status=Tournament.REGISTRATION)
            .order_by("registration_deadline", "start_date", "name")[:4]
        ),
        request.user,
    )
    home_tournament_cards = []
    seen_tournament_ids = set()

    for tournament in ([active] if active else []):
        if tournament.pk not in seen_tournament_ids:
            home_tournament_cards.append(tournament)
            seen_tournament_ids.add(tournament.pk)

    for tournament in [*registration_open_tournaments, *featured_tournaments]:
        if tournament.pk in seen_tournament_ids:
            continue
        tournament.public_name = _public_home_text(tournament.name) or tournament.name
        home_tournament_cards.append(tournament)
        seen_tournament_ids.add(tournament.pk)
        if len(home_tournament_cards) == 4:
            break

    if active:
        active.public_name = _public_home_text(active.name) or active.name
        if active.pk not in seen_tournament_ids:
            home_tournament_cards.insert(0, active)
            if len(home_tournament_cards) > 4:
                home_tournament_cards = home_tournament_cards[:4]

    return render(request, "tournament/home.html", {
        "announcements": announcements,
        "active_tournament": active,
        "featured_tournaments": featured_tournaments,
        "registration_open_tournaments": registration_open_tournaments,
        "home_tournament_cards": home_tournament_cards,
        "total_visible_tournaments": total_visible_tournaments,
        "total_approved_teams": total_approved_teams,
        "total_active_players": total_active_players,
    })


@admin_required
def staff_dashboard(request):
    tournaments = _annotated_staff_tournaments()
    recent_tournaments = tournaments.order_by("-created_at", "name")[:5]
    operational_results = _operational_results_queryset()
    recent_results = (
        operational_results
        .select_related(
            "fixture__tournament",
            "fixture__home_team",
            "fixture__away_team",
            "submitted_by",
        )
        .order_by("-submitted_at")[:5]
    )
    current_tournaments = tournaments.filter(
        status__in=[Tournament.REGISTRATION, Tournament.ACTIVE]
    ).order_by("status", "registration_deadline", "start_date", "name")
    draft_tournaments = tournaments.filter(
        status=Tournament.DRAFT
    ).order_by("-created_at", "name")
    past_tournaments = tournaments.filter(
        status__in=[Tournament.COMPLETED, Tournament.ARCHIVED]
    ).order_by("-end_date", "-start_date", "name")
    operational_registration_count = TournamentRegistration.objects.filter(
        is_active=True,
        tournament__status__in=[
            Tournament.DRAFT,
            Tournament.REGISTRATION,
            Tournament.ACTIVE,
        ],
    )
    active_complaint_count = Complaint.objects.filter(
        status__in=[Complaint.OPEN, Complaint.UNDER_REVIEW],
    ).count()

    return render(request, "tournament/staff_dashboard.html", {
        "tournament_count": tournaments.count(),
        "registration_open_count": tournaments.filter(status=Tournament.REGISTRATION).count(),
        "pending_result_count": operational_results.filter(status=Result.PENDING).count(),
        "disputed_result_count": operational_results.filter(status=Result.DISPUTED).count(),
        "matching_result_count": operational_results.filter(
            status=Result.PENDING,
            opponent_score_state=Result.OPPONENT_SCORE_MATCHING,
        ).count(),
        "score_conflict_result_count": operational_results.filter(
            status__in=[Result.PENDING, Result.DISPUTED],
            opponent_score_state=Result.OPPONENT_SCORE_CONFLICT,
        ).count(),
        "active_complaint_count": active_complaint_count,
        "pending_team_count": Team.objects.filter(is_approved=False).count(),
        "player_count": Player.objects.count(),
        "registration_count": operational_registration_count.count(),
        "recent_tournaments": recent_tournaments,
        "recent_results": recent_results,
        "registration_tournaments": current_tournaments,
        "current_tournaments": current_tournaments,
        "draft_tournaments": draft_tournaments,
        "past_tournaments": past_tournaments,
        "recent_notifications": Notification.objects.filter(user=request.user).order_by("-created_at")[:5],
    })


def _csv_response(*, headers, rows, filename):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.DictWriter(response, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            header: "" if row.get(header) is None else row.get(header)
            for header in headers
        })
    return response


@admin_required
def staff_export_dashboard(request):
    exports = [
        {
            "name": "Approved results",
            "description": "One row per approved result, excluding draft tournaments and screenshots.",
            "url_name": "tournament:staff_export_results_csv",
        },
        {
            "name": "Player stats",
            "description": "One row per official PlayerStat row. Zero-stat appearances may be missing.",
            "url_name": "tournament:staff_export_player_stats_csv",
        },
        {
            "name": "Team stats",
            "description": "One row per Standing row with derived win rate.",
            "url_name": "tournament:staff_export_team_stats_csv",
        },
        {
            "name": "Head-to-head",
            "description": "One row per approved fixture result using fixture home/away teams as team A/B.",
            "url_name": "tournament:staff_export_head_to_head_csv",
        },
    ]
    package_export = {
        "name": "Prediction dataset package",
        "description": (
            "ZIP bundle with approved results, fixture context, tournament context, "
            "official player stats, standings/history inputs, head-to-head rows, "
            "manifest metadata, and a data dictionary."
        ),
        "url_name": "tournament:staff_export_prediction_dataset_zip",
    }
    return render(request, "tournament/staff_exports.html", {
        "exports": exports,
        "package_export": package_export,
    })


@admin_required
def staff_export_results_csv(request):
    return _csv_response(
        headers=exporters.APPROVED_RESULTS_HEADERS,
        rows=exporters.approved_results_export_rows(),
        filename="approved-results.csv",
    )


@admin_required
def staff_export_player_stats_csv(request):
    return _csv_response(
        headers=exporters.PLAYER_STATS_HEADERS,
        rows=exporters.player_stats_export_rows(),
        filename="player-stats.csv",
    )


@admin_required
def staff_export_team_stats_csv(request):
    return _csv_response(
        headers=exporters.TEAM_STATS_HEADERS,
        rows=exporters.team_stats_export_rows(),
        filename="team-stats.csv",
    )


@admin_required
def staff_export_head_to_head_csv(request):
    return _csv_response(
        headers=exporters.HEAD_TO_HEAD_HEADERS,
        rows=exporters.head_to_head_export_rows(),
        filename="head-to-head.csv",
    )


@admin_required
def staff_export_prediction_dataset_zip(request):
    response = HttpResponse(
        exporters.build_prediction_dataset_package(),
        content_type="application/zip",
    )
    response["Content-Disposition"] = (
        'attachment; filename="hosted-by-tanvir-prediction-dataset.zip"'
    )
    return response


@admin_required
def staff_tournament_list(request):
    tournaments = (
        _annotated_staff_tournaments()
        .order_by("status", "-start_date", "name")
    )
    return render(request, "tournament/staff_tournament_list.html", {
        "tournaments": tournaments,
        "current_tournaments": tournaments.filter(
            status__in=[Tournament.REGISTRATION, Tournament.ACTIVE]
        ).order_by("status", "registration_deadline", "start_date", "name"),
        "draft_tournaments": tournaments.filter(status=Tournament.DRAFT).order_by("-created_at", "name"),
        "past_tournaments": tournaments.filter(
            status__in=[Tournament.COMPLETED, Tournament.ARCHIVED]
        ).order_by("-end_date", "-start_date", "name"),
    })


@admin_required
def staff_tournament_create(request):
    if request.method == "POST":
        form = TournamentStaffForm(request.POST)
        if form.is_valid():
            tournament = form.save()
            messages.success(request, f"{tournament.name} created successfully.")
            return redirect("tournament:staff_tournament_edit", pk=tournament.pk)
    else:
        form = TournamentStaffForm()

    return render(request, "tournament/staff_tournament_form.html", {
        "form": form,
        "page_title": "Create tournament",
        "submit_label": "Create tournament",
        "tournament_obj": None,
    })


@admin_required
def staff_tournament_edit(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    if request.method == "POST":
        form = TournamentStaffForm(request.POST, instance=tournament)
        if form.is_valid():
            form.save()
            messages.success(request, f"{tournament.name} updated successfully.")
            return redirect("tournament:staff_tournament_edit", pk=tournament.pk)
    else:
        form = TournamentStaffForm(instance=tournament)

    registrations = (
        TournamentRegistration.objects
        .filter(tournament=tournament)
        .select_related("team", "player")
        .order_by("seed", "registered_at")[:8]
    )
    context = {
        "form": form,
        "page_title": f"Edit {tournament.name}",
        "submit_label": "Save changes",
        "tournament_obj": tournament,
        "registration_preview": registrations,
    }
    context.update(_staff_tournament_management_context(tournament))
    return render(request, "tournament/staff_tournament_form.html", context)


@admin_required
@require_POST
def staff_tournament_archive(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    if tournament.is_archived:
        messages.info(request, f"{tournament.name} is already archived.")
    else:
        tournament.status = Tournament.ARCHIVED
        tournament.save(update_fields=["status", "updated_at"])
        messages.success(request, f"{tournament.name} archived. Public history remains visible.")

    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("tournament:staff_tournament_edit", pk=tournament.pk)


@admin_required
def staff_tournament_registrations(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    registrations = list(
        TournamentRegistration.objects
        .filter(tournament=tournament)
        .select_related("team", "team__captain", "player")
        .order_by("seed", "registered_at")
    )
    for registration in registrations:
        registration.staff_form = TournamentRegistrationStaffForm(instance=registration)
        if registration.team_id and registration.is_active:
            registration.group_form = TournamentGroupAssignmentForm(instance=registration)
    add_team_form, add_team_locked_reason = _staff_registration_management_context(tournament)
    team_player_counts = {}
    memberships_by_team = {}
    team_ids = [registration.team_id for registration in registrations if registration.team_id]
    if team_ids:
        team_player_counts = {
            row["team_id"]: row["active_count"]
            for row in (
                TeamMembership.objects
                .filter(team_id__in=team_ids, is_active=True)
                .values("team_id")
                .annotate(active_count=Count("id"))
            )
        }
        for membership in (
            TeamMembership.objects
            .filter(team_id__in=team_ids, is_active=True)
            .select_related("player")
            .order_by("team_id", "role", "joined_at", "player__username")
        ):
            memberships_by_team.setdefault(membership.team_id, []).append(membership)

    active_team_registrations_needing_attention = 0
    for registration in registrations:
        registration.active_roster_count = team_player_counts.get(registration.team_id, 0)
        if registration.team_id:
            registration.team.roster_eligibility = Tournament.team_roster_eligibility_for_count(
                registration.active_roster_count
            )
            registration.team.captain_alignment = _captain_alignment_context(
                registration.team,
                active_memberships=memberships_by_team.get(registration.team_id, []),
            )
            registration.team.team_integrity_summary = _team_integrity_summary_context(
                captain_alignment=registration.team.captain_alignment,
                roster_eligibility=registration.team.roster_eligibility,
            )
            if registration.is_active and not registration.team.team_integrity_summary["team_integrity_ok"]:
                active_team_registrations_needing_attention += 1

    context = {
        "tournament": tournament,
        "registrations": registrations,
        "add_team_form": add_team_form,
        "add_team_locked_reason": add_team_locked_reason,
        "active_team_registrations_needing_attention": active_team_registrations_needing_attention,
    }
    context.update(_staff_tournament_management_context(tournament))
    context.update(_staff_group_assignment_context(tournament, registrations))
    return render(request, "tournament/staff_tournament_registrations.html", context)


@admin_required
@require_POST
def staff_tournament_registration_add(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    form = TournamentManualTeamEntryForm(request.POST, tournament=tournament)

    if tournament.entrant_change_lock_reason():
        messages.error(request, tournament.entrant_change_lock_reason())
        return redirect("tournament:staff_tournament_registrations", pk=tournament.pk)

    if tournament.tournament_type != Tournament.TEAM:
        messages.error(request, "Manual team entry is only available for team tournaments.")
        return redirect("tournament:staff_tournament_registrations", pk=tournament.pk)

    if tournament.registration_count >= tournament.max_teams:
        messages.error(
            request,
            f"{tournament.name} already has the maximum of {tournament.max_teams} active teams.",
        )
        return redirect("tournament:staff_tournament_registrations", pk=tournament.pk)

    if not form.is_valid():
        messages.error(request, _message_for_validation_error(form.errors))
        return redirect("tournament:staff_tournament_registrations", pk=tournament.pk)

    team = form.cleaned_data["team"]
    registration, _ = TournamentRegistration.objects.get_or_create(
        tournament=tournament,
        team=team,
        defaults={"is_active": True},
    )
    registration.is_active = True

    try:
        registration.full_clean()
    except ValidationError as error:
        messages.error(request, _message_for_validation_error(error))
    else:
        registration.save()
        for user in _registration_notification_users(registration):
            notify_user(
                user,
                title="Tournament registration approved",
                message=f"{team.name} has been added to {tournament.name} by staff.",
                kind=Notification.Kind.TOURNAMENT_REGISTRATION_APPROVED,
                url=reverse("tournament:tournament_detail", args=[tournament.pk]),
            )
        messages.success(request, f"{team.name} has been added to {tournament.name}.")

    return redirect("tournament:staff_tournament_registrations", pk=tournament.pk)


@admin_required
@require_POST
def staff_tournament_registration_update(request, pk, registration_pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    registration = get_object_or_404(
        TournamentRegistration.objects.filter(tournament=tournament),
        pk=registration_pk,
    )
    was_active = registration.is_active
    form = TournamentRegistrationStaffForm(request.POST, instance=registration)
    if form.is_valid():
        try:
            saved_registration = form.save()
        except ValidationError as error:
            messages.error(request, _message_for_validation_error(error))
        else:
            entrant_name = _registration_entrant_name(saved_registration)
            if not was_active and saved_registration.is_active:
                for user in _registration_notification_users(saved_registration):
                    notify_user(
                        user,
                        title="Tournament registration approved",
                        message=f"{entrant_name} is active in {tournament.name}.",
                        kind=Notification.Kind.TOURNAMENT_REGISTRATION_APPROVED,
                        url=reverse("tournament:tournament_detail", args=[tournament.pk]),
                    )
            elif was_active and not saved_registration.is_active:
                for user in _registration_notification_users(saved_registration):
                    notify_user(
                        user,
                        title="Tournament registration rejected",
                        message=f"{entrant_name} was marked inactive for {tournament.name}.",
                        kind=Notification.Kind.TOURNAMENT_REGISTRATION_REJECTED,
                        url=reverse("tournament:tournament_detail", args=[tournament.pk]),
                    )
            messages.success(request, f"Updated registration for {entrant_name}.")
    else:
        messages.error(request, "Registration update could not be saved.")
    return redirect("tournament:staff_tournament_registrations", pk=tournament.pk)


@admin_required
@require_POST
def staff_tournament_group_assignment_update(request, pk, registration_pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    registration = get_object_or_404(
        TournamentRegistration.objects.filter(tournament=tournament),
        pk=registration_pk,
    )
    next_url = request.POST.get("next") or _staff_registrations_groups_redirect_target(tournament)
    form = TournamentGroupAssignmentForm(request.POST, instance=registration)
    if form.is_valid():
        try:
            form.save()
        except ValidationError as error:
            messages.error(request, _message_for_validation_error(error))
        else:
            if registration.group_label:
                messages.success(
                    request,
                    f"{registration.team.name} assigned to Group {registration.group_label}.",
                )
            else:
                messages.success(request, f"Cleared group assignment for {registration.team.name}.")
    else:
        messages.error(request, _message_for_validation_error(form.errors))
    return redirect(next_url)


@admin_required
@require_POST
def staff_tournament_group_assignment_bulk_update(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    next_url = request.POST.get("next") or _staff_registrations_groups_redirect_target(tournament)

    if tournament.is_archived:
        messages.error(request, tournament.archive_lock_reason())
        return redirect(next_url)

    selected_raw_ids = request.POST.getlist("registration_ids")
    group_label = (request.POST.get("group_label") or "").strip().upper()

    if not selected_raw_ids:
        messages.error(request, "Select at least one unassigned team to bulk assign.")
        return redirect(next_url)

    if not group_label:
        messages.error(request, "Enter a group label for bulk assignment.")
        return redirect(next_url)

    try:
        selected_ids = [int(value) for value in selected_raw_ids]
    except (TypeError, ValueError):
        messages.error(request, "Invalid team selection for bulk assignment.")
        return redirect(next_url)

    selected_ids = list(dict.fromkeys(selected_ids))
    registrations = list(
        TournamentRegistration.objects
        .filter(tournament=tournament, pk__in=selected_ids)
        .select_related("team")
    )

    if len(registrations) != len(selected_ids):
        messages.error(request, "One or more selected teams were not found.")
        return redirect(next_url)

    invalid_selection = any(
        (not registration.team_id) or (not registration.is_active) or bool(registration.group_label)
        for registration in registrations
    )
    if invalid_selection:
        messages.error(
            request,
            "Bulk assignment only supports active, unassigned team entrants.",
        )
        return redirect(next_url)

    forms = []
    for registration in registrations:
        form = TournamentGroupAssignmentForm({"group_label": group_label}, instance=registration)
        if not form.is_valid():
            messages.error(request, _message_for_validation_error(form.errors))
            return redirect(next_url)
        forms.append(form)

    try:
        with transaction.atomic():
            for form in forms:
                form.save()
    except ValidationError as error:
        messages.error(request, _message_for_validation_error(error))
        return redirect(next_url)

    assigned_count = len(forms)
    team_word = "team" if assigned_count == 1 else "teams"
    messages.success(request, f"{assigned_count} {team_word} assigned to Group {group_label}.")
    return redirect(next_url)


@admin_required
@require_POST
def staff_generate_fixtures(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    if tournament.is_archived:
        messages.error(request, tournament.archive_lock_reason())
        next_url = request.POST.get("next")
        if next_url:
            return redirect(next_url)
        return redirect("tournament:staff_tournament_edit", pk=tournament.pk)

    if tournament.tournament_type == Tournament.SINGLE:
        messages.error(
            request,
            "Single-player tournaments are currently registration-only. "
            "Fixture generation is not available for them yet.",
        )
        next_url = request.POST.get("next")
        if next_url:
            return redirect(next_url)
        return redirect("tournament:staff_tournament_edit", pk=tournament.pk)

    count, error = generate_fixtures_for_tournament(tournament)
    if error:
        messages.error(request, error)
    else:
        action_label = getattr(tournament, "fixture_generation_action", "").strip()
        if action_label:
            messages.success(request, f"{action_label}: {count} fixture(s) generated for {tournament.name}.")
        else:
            messages.success(request, f"{count} fixture(s) generated for {tournament.name}.")
        if getattr(tournament, "fixture_generation_notice", ""):
            messages.warning(request, tournament.fixture_generation_notice)
    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("tournament:staff_tournament_edit", pk=tournament.pk)


def tournament_detail(request, pk):
    tournament = get_object_or_404(_visible_tournaments_for(request.user), pk=pk)
    eligible_registration_teams = list(_eligible_registration_teams(request.user, tournament))
    registration_panel = _build_registration_panel_context(
        request.user,
        tournament,
        eligible_registration_teams,
    )

    standings = Standing.objects.filter(
        tournament=tournament
    ).select_related("team").order_by("-points", "-goal_difference", "-goals_for")

    fixture_filter_teams = list(
        Team.objects.filter(
            tournament_entries__tournament=tournament,
            tournament_entries__is_active=True,
        ).order_by("name").distinct()
    )
    fixture_filter_team_ids = {team.pk for team in fixture_filter_teams}
    selected_fixture_team_id = None
    raw_fixture_team_id = (request.GET.get("team") or "").strip()
    has_fixture_team_query = "team" in request.GET
    if raw_fixture_team_id:
        try:
            candidate_fixture_team_id = int(raw_fixture_team_id)
        except (TypeError, ValueError):
            candidate_fixture_team_id = None
        if candidate_fixture_team_id in fixture_filter_team_ids:
            selected_fixture_team_id = candidate_fixture_team_id

    current_user_team_ids = _active_team_ids_for_user(request.user)
    current_user_team_ids_set = set(current_user_team_ids)

    fixtures = list(_fixtures_with_result_state(tournament))
    for fixture in fixtures:
        fixture.viewer_side = _viewer_side_for_fixture(
            fixture,
            current_user_team_ids_set,
        )
        fixture.viewer_involved = fixture.viewer_side != "none"
        if fixture.display_result:
            score_classes = _viewer_relative_score_classes(
                home_score=fixture.display_result.home_score,
                away_score=fixture.display_result.away_score,
                viewer_side=fixture.viewer_side,
                allow_viewer_directional=True,
                allow_draw_highlight=True,
            )
            fixture.score_chip_class = score_classes["chip_class"]
            fixture.home_score_class = score_classes["home_class"]
            fixture.away_score_class = score_classes["away_class"]

    fixture_display_fixtures = fixtures
    if selected_fixture_team_id is not None:
        fixture_display_fixtures = [
            fixture for fixture in fixtures
            if fixture.home_team_id == selected_fixture_team_id
            or fixture.away_team_id == selected_fixture_team_id
        ]

    fixture_display = _fixture_display_sections(fixture_display_fixtures)
    grouped_standings = _grouped_standings_sections(fixtures)

    context = {
        "tournament": tournament,
        "standings": standings,
        "registration_panel": registration_panel,
        "current_user_team_ids": current_user_team_ids,
        "fixture_filter_teams": fixture_filter_teams,
        "selected_fixture_team_id": selected_fixture_team_id,
        "default_tab": "fixtures" if has_fixture_team_query else "overview",
        "has_top_scorers_data": PlayerStat.objects.filter(
            fixture__tournament=tournament,
            fixture__results__status=Result.APPROVED,
            goals__gt=0,
        ).exists(),
        "has_top_assists_data": PlayerStat.objects.filter(
            fixture__tournament=tournament,
            fixture__results__status=Result.APPROVED,
            assists__gt=0,
        ).exists(),
    }
    context.update(fixture_display)
    context.update(grouped_standings)
    return render(request, "tournament/tournament_detail.html", context)


def fixture_detail(request, pk):
    fixtures = Fixture.objects.select_related("tournament", "home_team", "away_team")
    if not request.user.is_authenticated or not request.user.is_staff:
        fixtures = fixtures.exclude(tournament__status=Tournament.DRAFT)
    fixture = get_object_or_404(fixtures, pk=pk)
    current_user_team_ids = _active_team_ids_for_user(request.user)
    current_user_team_ids_set = set(current_user_team_ids)
    viewer_side = _viewer_side_for_fixture(fixture, current_user_team_ids_set)
    viewer_involved = viewer_side != "none"

    approved_result = fixture.results.filter(status=Result.APPROVED).first()
    pending_result = (
        fixture.results
        .filter(status__in=[Result.PENDING, Result.DISPUTED])
        .select_related("submitted_by", "submitting_team", "opponent_responded_by")
        .prefetch_related("submitted_player_stats__player", "submitted_player_stats__team")
        .order_by("-submitted_at")
        .first()
    )
    is_staff_viewer = request.user.is_authenticated and request.user.is_staff
    pending_result_submitted_by_viewer = bool(
        pending_result
        and request.user.is_authenticated
        and pending_result.submitted_by_id == request.user.pk
    )
    pending_result_submitted_by_viewer_team = bool(
        pending_result
        and pending_result.submitting_team_id
        and pending_result.submitting_team_id in current_user_team_ids_set
    )
    can_respond = bool(
        pending_result
        and not fixture.tournament.is_archived
        and can_respond_to_fixture_result(request.user, pending_result)
    )
    can_view_pending_result_details = bool(
        pending_result
        and (
            is_staff_viewer
            or pending_result_submitted_by_viewer
            or pending_result_submitted_by_viewer_team
        )
    )
    can_view_pending_opponent_note = bool(is_staff_viewer)
    can_view_result_evidence = bool(is_staff_viewer)
    show_generic_pending_result_notice = bool(
        pending_result and not approved_result and not can_view_pending_result_details
    )
    pending_result_for_details = pending_result if can_view_pending_result_details else None
    pending_result_for_response = pending_result if can_respond else None
    can_edit_pending_player_stats = bool(
        pending_result
        and not approved_result
        and pending_result.status in [Result.PENDING, Result.DISPUTED]
        and fixture.tournament.tournament_type == Tournament.TEAM
        and not fixture.tournament.is_archived
        and request.user.is_authenticated
        and not request.user.is_staff
        and can_submit_fixture_result(request.user, fixture)
    )
    pending_result_for_player_stats = pending_result if can_edit_pending_player_stats else None
    evidence_result = (approved_result or pending_result) if can_view_result_evidence else None
    has_result_evidence = bool(
        evidence_result
        and (
            evidence_result.screenshot
            or evidence_result.home_player_stats_screenshot
            or evidence_result.away_player_stats_screenshot
        )
    )
    staff_action_result = Result.latest_actionable_for_fixture(fixture) if is_staff_viewer else None

    player_stats = []
    pending_player_stats = []
    approved_score_classes = None
    pending_score_classes = None
    if approved_result:
        from standings.models import PlayerStat
        player_stats = PlayerStat.objects.filter(
            fixture=fixture
        ).select_related("player", "team").order_by("-goals")
        approved_score_classes = _viewer_relative_score_classes(
            home_score=approved_result.home_score,
            away_score=approved_result.away_score,
            viewer_side=viewer_side,
            allow_viewer_directional=True,
            allow_draw_highlight=True,
        )
    elif pending_result and can_view_pending_result_details:
        pending_player_stats = list(
            pending_result.submitted_player_stats.select_related("player", "team").order_by("-goals", "player__username")
        )
        pending_score_classes = _viewer_relative_score_classes(
            home_score=pending_result.home_score,
            away_score=pending_result.away_score,
            viewer_side=viewer_side,
            allow_viewer_directional=False,
            allow_draw_highlight=False,
        )

    can_submit = (
        request.user.is_authenticated
        and not fixture.tournament.is_archived
        and not approved_result
        and pending_result is None
        and (
            request.user.is_staff
            or can_submit_fixture_result(request.user, fixture)
        )
    )
    opponent_response_form = OpponentResultResponseForm() if can_respond else None
    staff_schedule_form = (
        StaffFixtureScheduleForm(instance=fixture)
        if is_staff_viewer
        and not fixture.tournament.is_archived
        else None
    )
    show_team_rosters = fixture.tournament.is_team_tournament
    head_to_head_stats = (
        get_head_to_head_stats(fixture.home_team, fixture.away_team)
        if fixture.away_team_id
        else None
    )
    fixture_prediction = get_fixture_prediction(fixture)
    home_team_player_names = []
    away_team_player_names = []
    if show_team_rosters:
        team_ids = [fixture.home_team_id]
        if fixture.away_team_id:
            team_ids.append(fixture.away_team_id)
        active_memberships = (
            TeamMembership.objects
            .filter(team_id__in=team_ids, is_active=True)
            .select_related("player")
            .order_by("team_id", "role", "player__username")
        )
        for membership in active_memberships:
            player_name = membership.player.display_name
            if membership.team_id == fixture.home_team_id:
                home_team_player_names.append(player_name)
            elif membership.team_id == fixture.away_team_id:
                away_team_player_names.append(player_name)

    return render(request, "tournament/fixture_detail.html", {
        "fixture": fixture,
        "approved_result": approved_result,
        "pending_result": pending_result_for_details,
        "pending_result_for_response": pending_result_for_response,
        "pending_result_for_player_stats": pending_result_for_player_stats,
        "has_pending_result": bool(pending_result),
        "evidence_result": evidence_result,
        "has_result_evidence": has_result_evidence,
        "pending_player_stats": pending_player_stats,
        "player_stats": player_stats,
        "can_submit": can_submit,
        "can_respond_to_pending_result": can_respond,
        "can_edit_pending_player_stats": can_edit_pending_player_stats,
        "can_view_pending_result_details": can_view_pending_result_details,
        "can_view_pending_opponent_note": can_view_pending_opponent_note,
        "can_view_result_evidence": can_view_result_evidence,
        "show_generic_pending_result_notice": show_generic_pending_result_notice,
        "current_user_team_ids": current_user_team_ids,
        "viewer_side": viewer_side,
        "viewer_involved": viewer_involved,
        "approved_score_classes": approved_score_classes,
        "pending_score_classes": pending_score_classes,
        "opponent_response_form": opponent_response_form,
        "has_group_context": bool(fixture.group_label),
        "staff_action_result": staff_action_result,
        "staff_schedule_form": staff_schedule_form,
        "archive_lock_reason": fixture.tournament.archive_lock_reason(),
        "show_team_rosters": show_team_rosters,
        "home_team_player_names": home_team_player_names,
        "away_team_player_names": away_team_player_names,
        "head_to_head_stats": head_to_head_stats,
        "fixture_prediction": fixture_prediction,
    })


@login_required
def complaint_list(request):
    complaints = _ordered_complaints(
        _complaints_select_related(Complaint.objects.filter(player=request.user))
    )
    return render(request, "tournament/complaint_list.html", {
        "complaints": complaints,
    })


@login_required
def complaint_create(request):
    if request.method == "POST":
        form = PlayerComplaintForm(request.POST, player=request.user)
        if form.is_valid():
            complaint = form.save()
            messages.success(request, "Complaint/request submitted for staff review.")
            return redirect("tournament:complaint_detail", pk=complaint.pk)
    else:
        form = PlayerComplaintForm(player=request.user)

    return render(request, "tournament/complaint_form.html", {
        "form": form,
    })


@login_required
def complaint_detail(request, pk):
    complaint = get_object_or_404(
        _complaints_select_related(Complaint.objects.filter(player=request.user)),
        pk=pk,
    )
    return render(request, "tournament/complaint_detail.html", {
        "complaint": complaint,
    })


@admin_required
def staff_complaint_list(request):
    current_status = (request.GET.get("status") or "active").strip()
    valid_statuses = {choice[0] for choice in Complaint.STATUS_CHOICES}
    if current_status not in valid_statuses and current_status not in {"active", "all"}:
        current_status = "active"

    complaints = _complaints_select_related(Complaint.objects.all())
    if current_status == "active":
        complaints = complaints.filter(status__in=[Complaint.OPEN, Complaint.UNDER_REVIEW])
    elif current_status != "all":
        complaints = complaints.filter(status=current_status)
    complaints = _ordered_complaints(complaints)

    all_complaints = Complaint.objects.all()
    return render(request, "tournament/staff/complaint_list.html", {
        "complaints": complaints,
        "current_status": current_status,
        "active_count": all_complaints.filter(
            status__in=[Complaint.OPEN, Complaint.UNDER_REVIEW],
        ).count(),
        "open_count": all_complaints.filter(status=Complaint.OPEN).count(),
        "under_review_count": all_complaints.filter(status=Complaint.UNDER_REVIEW).count(),
        "resolved_count": all_complaints.filter(status=Complaint.RESOLVED).count(),
        "rejected_count": all_complaints.filter(status=Complaint.REJECTED).count(),
    })


@admin_required
def staff_complaint_detail(request, pk):
    complaint = get_object_or_404(
        _complaints_select_related(Complaint.objects.all()),
        pk=pk,
    )
    old_status = complaint.status
    old_staff_response = complaint.staff_response

    if request.method == "POST":
        form = StaffComplaintUpdateForm(request.POST, instance=complaint)
        if form.is_valid():
            complaint = form.save(commit=False)
            status_changed = complaint.status != old_status
            response_changed = (
                bool(complaint.staff_response.strip())
                and complaint.staff_response.strip() != old_staff_response.strip()
            )
            should_notify = status_changed or response_changed
            if should_notify:
                complaint.responded_by = request.user
                complaint.responded_at = timezone.now()
            complaint.save()
            if should_notify:
                notify_user(
                    complaint.player,
                    title="Complaint/request updated",
                    message=(
                        f'Staff updated "{complaint.subject}". '
                        f"Status: {complaint.get_status_display()}."
                    ),
                    kind=Notification.Kind.COMPLAINT_RESPONSE,
                    url=reverse("tournament:complaint_detail", args=[complaint.pk]),
                )
            messages.success(request, "Complaint/request updated.")
            return redirect("tournament:staff_complaint_detail", pk=complaint.pk)
    else:
        form = StaffComplaintUpdateForm(instance=complaint)

    return render(request, "tournament/staff/complaint_detail.html", {
        "complaint": complaint,
        "form": form,
    })


@login_required
@require_POST
def tournament_register(request, pk):
    tournament = get_object_or_404(_visible_tournaments_for(request.user), pk=pk)

    if tournament.tournament_type == Tournament.SINGLE:
        _validate_single_registration_request(request.user, tournament)
        registration = TournamentRegistration(
            tournament=tournament,
            player=request.user,
            is_active=True,
        )
        try:
            registration.full_clean()
        except ValidationError as error:
            messages.error(request, _message_for_validation_error(error))
            return redirect("tournament:tournament_detail", pk=tournament.pk)

        registration.save()
        notify_user(
            request.user,
            title="Tournament registration confirmed",
            message=f"You are registered for {tournament.name}.",
            kind=Notification.Kind.TOURNAMENT_REGISTRATION_APPROVED,
            url=reverse("tournament:tournament_detail", args=[tournament.pk]),
        )
        notify_staff(
            title="New tournament registration",
            message=f"{request.user.display_name} registered for {tournament.name}.",
            kind=Notification.Kind.TOURNAMENT_REGISTRATION_SUBMITTED,
            url=reverse("tournament:staff_tournament_registrations", args=[tournament.pk]),
        )
        messages.success(request, f"You are now registered for {tournament.name}.")
        return redirect("tournament:tournament_detail", pk=tournament.pk)

    team = get_object_or_404(Team, pk=request.POST.get("team_id"))
    _validate_team_registration_request(request.user, tournament, team)

    registration = TournamentRegistration(
        tournament=tournament,
        team=team,
        is_active=True,
    )
    try:
        registration.full_clean()
    except ValidationError as error:
        messages.error(request, _message_for_validation_error(error))
        return redirect("tournament:tournament_detail", pk=tournament.pk)

    registration.save()
    for user in _registration_notification_users(registration):
        notify_user(
            user,
            title="Tournament registration confirmed",
            message=f"{team.name} is registered for {tournament.name}.",
            kind=Notification.Kind.TOURNAMENT_REGISTRATION_APPROVED,
            url=reverse("tournament:tournament_detail", args=[tournament.pk]),
        )
    notify_staff(
        title="New tournament registration",
        message=f"{team.name} registered for {tournament.name}.",
        kind=Notification.Kind.TOURNAMENT_REGISTRATION_SUBMITTED,
        url=reverse("tournament:staff_tournament_registrations", args=[tournament.pk]),
    )
    messages.success(request, f"{team.name} is now registered for {tournament.name}.")
    return redirect("tournament:tournament_detail", pk=tournament.pk)


@login_required
def result_submit(request, fixture_pk):
    fixtures = Fixture.objects.filter(is_bye=False)
    if not request.user.is_staff:
        fixtures = fixtures.exclude(tournament__status=Tournament.DRAFT)
    fixture = get_object_or_404(fixtures, pk=fixture_pk)
    approved_result = fixture.results.filter(status=Result.APPROVED).first()
    actionable_result = Result.latest_actionable_for_fixture(fixture)

    if fixture.tournament.is_archived:
        messages.info(request, fixture.tournament.archive_lock_reason())
        return redirect("tournament:fixture_detail", pk=fixture.pk)

    if not request.user.is_staff and not can_submit_fixture_result(request.user, fixture):
        raise PermissionDenied("You do not have permission to submit a result for this fixture.")

    if request.user.is_staff and actionable_result is not None:
        if actionable_result.status == Result.APPROVED:
            messages.info(request, "This fixture already has an approved result. Edit it here to correct the official score.")
        else:
            messages.info(request, "This fixture already has a submitted result in progress. Edit the existing record instead of creating another one.")
        return redirect("tournament:result_edit", pk=actionable_result.pk)

    if approved_result and not request.user.is_staff:
        messages.info(request, "This fixture already has an approved result.")
        return redirect("tournament:fixture_detail", pk=fixture_pk)

    if (
        not request.user.is_staff
        and fixture.results.filter(status__in=[Result.PENDING, Result.DISPUTED]).exists()
    ):
        messages.info(request, "A result for this fixture is already under review.")
        return redirect("tournament:fixture_detail", pk=fixture_pk)

    stat_formset = None
    stat_formset_bound = request.method == "POST" and "player_stats-TOTAL_FORMS" in request.POST
    if fixture.tournament.tournament_type == Tournament.TEAM:
        stat_formset = build_result_player_stat_formset(
            fixture=fixture,
            data=request.POST if stat_formset_bound else None,
        )

    if request.method == "POST":
        form = ResultSubmitForm(
            request.POST,
            request.FILES,
            fixture=fixture,
            player=request.user,
        )
        formset_is_valid = (
            stat_formset.is_valid()
            if stat_formset is not None and stat_formset.is_bound
            else True
        )
        if form.is_valid() and formset_is_valid and stat_formset is not None and stat_formset.is_bound:
            try:
                validate_result_goal_totals(
                    fixture=fixture,
                    home_score=form.cleaned_data["home_score"],
                    away_score=form.cleaned_data["away_score"],
                    stat_formset=stat_formset,
                )
            except ValidationError as error:
                stat_formset._non_form_errors = stat_formset.error_class(error.messages)
                formset_is_valid = False
        if form.is_valid() and formset_is_valid:
            submitted_result = form.save()
            if stat_formset is not None and stat_formset.is_bound:
                save_result_player_stats(result=submitted_result, formset=stat_formset)
            notify_staff(
                title="New result submitted",
                message=f"A result was submitted for {fixture.home_team.name} vs {fixture.away_team.name}.",
                kind=Notification.Kind.RESULT_SUBMITTED,
                url=f"{reverse('tournament:admin_queue')}?status=pending",
            )
            messages.success(request, "Result submitted. An admin will review it shortly.")
            return redirect("tournament:result_pending", fixture_pk=fixture_pk)
    else:
        form = ResultSubmitForm(fixture=fixture, player=request.user)

    return render(request, "tournament/result_submit.html", {
        "fixture": fixture,
        "form": form,
        "is_edit": False,
        "stat_formset": stat_formset,
    })


@login_required
def result_edit(request, pk):
    result = get_object_or_404(
        Result.objects.select_related(
            "fixture",
            "fixture__tournament",
            "fixture__home_team",
            "fixture__away_team",
        ),
        pk=pk,
    )

    if result.fixture.tournament.is_archived:
        messages.info(request, result.fixture.tournament.archive_lock_reason())
        return redirect("tournament:fixture_detail", pk=result.fixture.pk)

    stats_only_edit = False
    if not request.user.is_staff:
        can_edit_pending_stats = (
            result.status in [Result.PENDING, Result.DISPUTED]
            and result.fixture.tournament.tournament_type == Tournament.TEAM
            and can_submit_fixture_result(request.user, result.fixture)
        )
        if not can_edit_pending_stats:
            raise PermissionDenied("You do not have permission to edit player stats for this result.")
        stats_only_edit = True

    stat_formset = None
    stat_formset_bound = request.method == "POST" and "player_stats-TOTAL_FORMS" in request.POST
    if result.fixture.tournament.tournament_type == Tournament.TEAM:
        stat_formset = build_result_player_stat_formset(
            fixture=result.fixture,
            data=request.POST if stat_formset_bound else None,
            result=result,
        )

    if request.method == "POST":
        if stats_only_edit:
            form = ResultSubmitForm(
                instance=result,
                fixture=result.fixture,
                player=request.user,
            )
        else:
            form = ResultSubmitForm(
                request.POST,
                request.FILES,
                instance=result,
                fixture=result.fixture,
                player=request.user,
            )
        formset_is_valid = (
            stat_formset.is_valid()
            if stat_formset is not None and stat_formset.is_bound
            else True
        )
        form_is_valid = True if stats_only_edit else form.is_valid()
        if form_is_valid and formset_is_valid and stat_formset is not None and stat_formset.is_bound:
            try:
                validate_result_goal_totals(
                    fixture=result.fixture,
                    home_score=result.home_score if stats_only_edit else form.cleaned_data["home_score"],
                    away_score=result.away_score if stats_only_edit else form.cleaned_data["away_score"],
                    stat_formset=stat_formset,
                )
            except ValidationError as error:
                stat_formset._non_form_errors = stat_formset.error_class(error.messages)
                formset_is_valid = False
        if form_is_valid and formset_is_valid:
            edited_result = result if stats_only_edit else form.save()
            if stat_formset is not None and stat_formset.is_bound:
                save_result_player_stats(result=edited_result, formset=stat_formset)
            if not stats_only_edit and edited_result.status == Result.APPROVED:
                edited_result.reviewed_by = request.user
                edited_result.reviewed_at = timezone.now()
                edited_result.save(update_fields=["reviewed_by", "reviewed_at"])
                edited_result.sync_official_player_stats()
            if stats_only_edit:
                messages.success(request, "Player stats updated. Staff will review them before approval.")
            else:
                messages.success(request, f"Result updated: {result.fixture}")
            return redirect("tournament:fixture_detail", pk=result.fixture.pk)
    else:
        form = ResultSubmitForm(
            instance=result,
            fixture=result.fixture,
            player=request.user,
        )

    return render(request, "tournament/result_submit.html", {
        "fixture": result.fixture,
        "form": form,
        "is_edit": True,
        "result": result,
        "stats_only_edit": stats_only_edit,
        "stat_formset": stat_formset,
    })


@login_required
def result_pending(request, fixture_pk):
    fixtures = Fixture.objects.all()
    if not request.user.is_staff:
        fixtures = fixtures.exclude(tournament__status=Tournament.DRAFT)
    fixture = get_object_or_404(fixtures, pk=fixture_pk)
    result_qs = fixture.results.order_by("-submitted_at")
    if not request.user.is_staff:
        result_qs = result_qs.filter(submitted_by=request.user)
    latest_result = result_qs.first()

    if latest_result is None:
        raise PermissionDenied("You do not have permission to view this result status.")

    return render(request, "tournament/result_pending.html", {
        "fixture": fixture,
        "result": latest_result,
    })


@login_required
@require_POST
def result_opponent_response(request, pk):
    result = get_object_or_404(
        Result.objects.select_related(
            "fixture",
            "fixture__home_team",
            "fixture__away_team",
            "submitting_team",
        ),
        pk=pk,
    )
    if result.fixture.tournament.is_archived:
        messages.error(request, result.fixture.tournament.archive_lock_reason())
        return redirect("tournament:fixture_detail", pk=result.fixture.pk)
    if not can_respond_to_fixture_result(request.user, result):
        raise PermissionDenied("You do not have permission to respond to this submitted result.")

    form = OpponentResultResponseForm(request.POST)
    if form.is_valid():
        result.record_opponent_response(
            player=request.user,
            status=form.cleaned_data["action"],
            home_score=form.cleaned_data["opponent_home_score"],
            away_score=form.cleaned_data["opponent_away_score"],
            note=form.cleaned_data["note"],
        )
        response_label = (
            "confirmed"
            if form.cleaned_data["action"] == Result.OPPONENT_RESPONSE_CONFIRMED
            else "disputed"
        )
        notify_user(
            result.submitted_by,
            title="Opponent responded",
            message=(
                f"The opposing team {response_label} your submitted result for "
                f"{result.fixture.home_team.name} vs {result.fixture.away_team.name}."
            ),
            kind=Notification.Kind.OPPONENT_RESPONSE,
            url=reverse("tournament:fixture_detail", args=[result.fixture.pk]),
        )
        if result.opponent_score_state == Result.OPPONENT_SCORE_CONFLICT:
            notify_staff(
                title="Opponent score conflict",
                message=(
                    f"Opponent-entered scores conflict for "
                    f"{result.fixture.home_team.name} vs {result.fixture.away_team.name}."
                ),
                kind=Notification.Kind.OPPONENT_SCORE_CONFLICT,
                url=f"{reverse('tournament:admin_queue')}?status=pending",
            )
        if result.opponent_score_state == Result.OPPONENT_SCORE_MATCHING:
            messages.success(
                request,
                "Opponent score recorded. Scores match; staff will still review before approval.",
            )
        elif result.opponent_score_state == Result.OPPONENT_SCORE_CONFLICT:
            messages.success(
                request,
                "Opponent score recorded. Score conflict flagged for staff review.",
            )
        elif form.cleaned_data["action"] == Result.OPPONENT_RESPONSE_CONFIRMED:
            messages.success(request, "Result confirmed. Staff will still review before it becomes official.")
        else:
            messages.success(request, "Result disputed. Staff will see your note before reviewing it.")
    else:
        messages.error(request, _message_for_validation_error(form.errors))

    return redirect("tournament:fixture_detail", pk=result.fixture.pk)


# ── Admin Result Queue ────────────────────────────────────────────────

@admin_required
def admin_result_queue(request):
    status = request.GET.get("status", "pending")
    operational_results = _operational_results_queryset()

    results = (
        operational_results
        .filter(status=status)
        .select_related(
            "fixture__tournament",
            "fixture__home_team",
            "fixture__away_team",
            "submitted_by",
            "submitting_team",
            "opponent_responded_by",
            "reviewed_by",
        )
        .prefetch_related("submitted_player_stats__player", "submitted_player_stats__team")
        .order_by("-submitted_at")
    )

    pending_count = operational_results.filter(status=Result.PENDING).count()
    disputed_count = operational_results.filter(status=Result.DISPUTED).count()
    matching_count = operational_results.filter(
        status=Result.PENDING,
        opponent_score_state=Result.OPPONENT_SCORE_MATCHING,
    ).count()
    score_conflict_count = operational_results.filter(
        status__in=[Result.PENDING, Result.DISPUTED],
        opponent_score_state=Result.OPPONENT_SCORE_CONFLICT,
    ).count()

    return render(request, "tournament/admin_queue.html", {
        "results": results,
        "current_status": status,
        "pending_count": pending_count,
        "disputed_count": disputed_count,
        "matching_count": matching_count,
        "score_conflict_count": score_conflict_count,
    })


@admin_required
def staff_pending_results(request):
    return admin_result_queue(request)


@admin_required
@require_POST
def staff_fixture_schedule_update(request, pk):
    fixture = get_object_or_404(
        Fixture.objects.select_related("tournament", "home_team", "away_team"),
        pk=pk,
    )
    if fixture.tournament.is_archived:
        messages.error(request, fixture.tournament.archive_lock_reason())
        return redirect("tournament:fixture_detail", pk=fixture.pk)

    form = StaffFixtureScheduleForm(request.POST, instance=fixture)
    if form.is_valid():
        old_match_date = fixture.match_date
        old_submission_deadline = fixture.submission_deadline
        form.save()
        if (
            old_match_date != fixture.match_date
            or old_submission_deadline != fixture.submission_deadline
        ):
            if fixture.match_date:
                title = "Fixture schedule updated" if old_match_date else "Fixture scheduled"
                message = f"{fixture.home_team.name} vs {fixture.away_team.name} has a schedule update."
            else:
                title = "Fixture schedule cleared"
                message = f"The schedule for {fixture.home_team.name} vs {fixture.away_team.name} was cleared."
            for user in _fixture_notification_users(fixture):
                notify_user(
                    user,
                    title=title,
                    message=message,
                    kind=Notification.Kind.FIXTURE_SCHEDULED,
                    url=reverse("tournament:fixture_detail", args=[fixture.pk]),
                )
        if fixture.match_date:
            messages.success(request, f"Fixture schedule updated for {fixture}.")
        else:
            messages.success(request, f"Fixture schedule cleared for {fixture}.")
    else:
        messages.error(request, _message_for_validation_error(form.errors))
    return redirect("tournament:fixture_detail", pk=fixture.pk)


@admin_required
@require_POST
def result_approve(request, pk):
    result = get_object_or_404(
        Result.objects.select_related(
            "fixture__tournament",
            "fixture__home_team",
            "fixture__away_team",
            "submitted_by",
        ),
        pk=pk,
    )
    if result.fixture.tournament.is_archived:
        messages.error(request, result.fixture.tournament.archive_lock_reason())
        return redirect("tournament:fixture_detail", pk=result.fixture.pk)
    try:
        score_only_fallback_used = result.approve(
            admin=request.user,
            allow_score_only_fallback=True,
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        if score_only_fallback_used:
            messages.warning(
                request,
                "Result approved with score-only fallback. Submitted player stats were not published because they did not reconcile with the score.",
            )
        else:
            messages.success(request, f"Result approved: {result.fixture}")
        notify_user(
            result.submitted_by,
            title="Result approved",
            message=f"Your result for {result.fixture.home_team.name} vs {result.fixture.away_team.name} was approved.",
            kind=Notification.Kind.RESULT_APPROVED,
            url=reverse("tournament:fixture_detail", args=[result.fixture.pk]),
        )
    referer = request.META.get("HTTP_REFERER", "/queue/")
    return redirect(referer.split("?")[0] + "?status=pending")


@admin_required
@require_POST
def result_reject(request, pk):
    result = get_object_or_404(
        Result.objects.select_related(
            "fixture__tournament",
            "fixture__home_team",
            "fixture__away_team",
            "submitted_by",
        ),
        pk=pk,
    )
    if result.fixture.tournament.is_archived:
        messages.error(request, result.fixture.tournament.archive_lock_reason())
        return redirect("tournament:fixture_detail", pk=result.fixture.pk)
    note = request.POST.get("admin_note", "")
    result.reject(admin=request.user, note=note)
    notify_user(
        result.submitted_by,
        title="Result rejected",
        message=f"Your result for {result.fixture.home_team.name} vs {result.fixture.away_team.name} was rejected.",
        kind=Notification.Kind.RESULT_REJECTED,
        url=reverse("tournament:fixture_detail", args=[result.fixture.pk]),
    )
    messages.success(request, f"Result rejected: {result.fixture}")
    referer = request.META.get("HTTP_REFERER", "/queue/")
    return redirect(referer.split("?")[0] + "?status=pending")


@admin_required
@require_POST
def result_dispute(request, pk):
    result = get_object_or_404(
        Result.objects.select_related(
            "fixture__tournament",
            "fixture__home_team",
            "fixture__away_team",
            "submitted_by",
        ),
        pk=pk,
    )
    if result.fixture.tournament.is_archived:
        messages.error(request, result.fixture.tournament.archive_lock_reason())
        return redirect("tournament:fixture_detail", pk=result.fixture.pk)
    note = request.POST.get("admin_note", "")
    result.dispute(admin=request.user, note=note)
    notify_user(
        result.submitted_by,
        title="Result marked disputed",
        message=f"Your result for {result.fixture.home_team.name} vs {result.fixture.away_team.name} was marked disputed by staff.",
        kind=Notification.Kind.RESULT_DISPUTED,
        url=reverse("tournament:fixture_detail", args=[result.fixture.pk]),
    )
    messages.success(request, f"Result marked as disputed: {result.fixture}")
    referer = request.META.get("HTTP_REFERER", "/queue/")
    return redirect(referer.split("?")[0] + "?status=disputed")


def standings_partial(request, tournament_pk):
    """
    Returns just the standings table HTML fragment.
    Called by HTMX every 30s from the home page.
    """
    from tournament.models import Tournament
    tournament = get_object_or_404(_visible_tournaments_for(request.user), pk=tournament_pk)
    fixtures = _fixtures_with_result_state(tournament)
    grouped_standings = _grouped_standings_sections(fixtures)
    standings = (
        Standing.objects
        .filter(tournament=tournament)
        .select_related("team")
        .order_by("-points", "-goal_difference", "-goals_for")
    )
    return render(request, "tournament/partials/standings_table.html", {
        "standings": standings,
        "has_grouped_standings": grouped_standings["has_grouped_standings"],
        "grouped_standings_sections": grouped_standings["grouped_standings_sections"],
    })

def announcement_list(request):
    announcements = Announcement.objects.filter(is_active=True).select_related('tournament').order_by('-is_pinned', 'sort_order', '-created_at')
    return render(request, 'tournament/announcement_list.html', {'announcements': announcements})

@login_required
def staff_announcement_list(request):
    if not request.user.is_staff:
        raise PermissionDenied
    announcements = Announcement.objects.all().select_related('tournament').order_by('-is_pinned', 'sort_order', '-created_at')
    return render(request, 'tournament/staff/announcement_list.html', {'announcements': announcements})

@login_required
def staff_announcement_create(request):
    if not request.user.is_staff:
        raise PermissionDenied
    from tournament.forms import AnnouncementForm
    if request.method == 'POST':
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Announcement created.')
            return redirect('tournament:staff_announcement_list')
    else:
        form = AnnouncementForm()
    return render(request, 'tournament/staff/announcement_form.html', {'form': form})

@login_required
def staff_announcement_edit(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied
    announcement = get_object_or_404(Announcement, pk=pk)
    from tournament.forms import AnnouncementForm
    if request.method == 'POST':
        form = AnnouncementForm(request.POST, instance=announcement)
        if form.is_valid():
            form.save()
            messages.success(request, 'Announcement updated.')
            return redirect('tournament:staff_announcement_list')
    else:
        form = AnnouncementForm(instance=announcement)
    return render(request, 'tournament/staff/announcement_form.html', {'form': form})
