# tournament/admin.py

from django.contrib import admin
from django.db.models import Count, Q, Case, When, Value, IntegerField
from django.core.exceptions import ValidationError
from .models import (
    Announcement,
    Complaint,
    Fixture,
    Result,
    ResultPlayerStat,
    Tournament,
    TournamentRegistration,
)
from tournament.player_stat_fields import PLAYER_STAT_COPY_FIELDS


class ResultPlayerStatInline(admin.TabularInline):
    model = ResultPlayerStat
    extra = 0
    fields = ("player", "team") + PLAYER_STAT_COPY_FIELDS
    autocomplete_fields = ("player", "team")


class ResultInline(admin.TabularInline):
    model = Result
    extra = 0
    readonly_fields = (
        "submitted_by",
        "home_score",
        "away_score",
        "match_screenshot",
        "home_player_stats_screenshot_evidence",
        "away_player_stats_screenshot_evidence",
        "submitted_at",
        "status",
    )
    fields = (
        "submitted_by",
        "home_score",
        "away_score",
        "status",
        "admin_note",
        "match_screenshot",
        "home_player_stats_screenshot_evidence",
        "away_player_stats_screenshot_evidence",
        "submitted_at",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Match screenshot")
    def match_screenshot(self, obj):
        return obj.screenshot

    @admin.display(description="Home team player-stat screenshot")
    def home_player_stats_screenshot_evidence(self, obj):
        return obj.home_player_stats_screenshot

    @admin.display(description="Away team player-stat screenshot")
    def away_player_stats_screenshot_evidence(self, obj):
        return obj.away_player_stats_screenshot


class TournamentRegistrationInline(admin.TabularInline):
    model = TournamentRegistration
    extra = 0
    can_delete = False
    fields = ("team", "player", "seed", "group_label", "is_active", "registered_at")
    readonly_fields = ("registered_at",)
    autocomplete_fields = ("team", "player")


class FixtureResultStateFilter(admin.SimpleListFilter):
    title = "result state"
    parameter_name = "result_state"

    def lookups(self, request, model_admin):
        return (
            ("approved", "Approved result"),
            ("pending", "Pending review"),
            ("disputed", "Disputed"),
            ("rejected", "Rejected only"),
            ("none", "No submissions"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "approved":
            return queryset.filter(results__status=Result.APPROVED).distinct()
        if value == "pending":
            return queryset.filter(results__status=Result.PENDING).distinct()
        if value == "disputed":
            return queryset.filter(results__status=Result.DISPUTED).distinct()
        if value == "rejected":
            return queryset.filter(results__status=Result.REJECTED).exclude(
                results__status__in=[Result.PENDING, Result.APPROVED, Result.DISPUTED]
            ).distinct()
        if value == "none":
            return queryset.filter(results__isnull=True)
        return queryset


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = (
        "subject",
        "player",
        "complaint_type",
        "status",
        "created_at",
        "updated_at",
    )
    list_filter = ("complaint_type", "status", "created_at")
    search_fields = (
        "subject",
        "description",
        "staff_response",
        "player__username",
        "player__in_game_name",
        "player__unique_id",
    )
    readonly_fields = ("created_at", "updated_at", "responded_at")
    list_select_related = ("player", "fixture", "result", "responded_by")
    autocomplete_fields = ("player", "fixture", "result", "responded_by")
    ordering = ("status", "-created_at")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "is_pinned", "is_active", "tournament", "sort_order", "created_at", "updated_at")
    list_filter = ("is_pinned", "is_active", "tournament", "created_at", "updated_at")
    search_fields = ("title", "body")
    ordering = ("-is_pinned", "sort_order", "-created_at", "title")


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "tournament_type",
        "format",
        "status",
        "registration_open",
        "active_registration_count",
        "max_teams",
        "is_full",
        "has_fixtures",
        "start_date",
        "end_date",
    )
    list_filter = ("tournament_type", "status", "format")
    search_fields = ("name",)
    readonly_fields = (
        "registration_open",
        "active_registration_count",
        "has_fixtures",
        "created_at",
        "updated_at",
    )
    inlines = [TournamentRegistrationInline]
    actions = ["generate_fixtures_action"]
    ordering = ("status", "-start_date", "name")
    date_hierarchy = "start_date"
    fieldsets = (
        (None, {"fields": ("name", "tournament_type", "format", "status", "description")}),
        ("Scheduling", {"fields": ("registration_deadline", "start_date", "end_date")}),
        ("Capacity", {"fields": ("max_teams", "tiebreaker_rules")}),
        ("Admin overview", {"fields": ("registration_open", "active_registration_count", "has_fixtures", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            active_registrations_count=Count(
                "registrations",
                filter=Q(registrations__is_active=True),
                distinct=True,
            ),
            fixtures_count=Count("fixtures", distinct=True),
        )

    @admin.action(description="Generate fixtures for selected tournaments")
    def generate_fixtures_action(self, request, queryset):
        from .fixtures import generate_fixtures_for_tournament
        for tournament in queryset:
            count, error = generate_fixtures_for_tournament(tournament)
            if error:
                self.message_user(request, f"Error for {tournament.name}: {error}", level="error")
            else:
                self.message_user(request, f"{count} fixtures generated for {tournament.name}.")
                if getattr(tournament, "fixture_generation_notice", ""):
                    self.message_user(request, tournament.fixture_generation_notice, level="warning")

    @admin.display(boolean=True, description="Registration open?")
    def registration_open(self, obj):
        return obj.is_registration_open

    @admin.display(description="Registrations", ordering="active_registrations_count")
    def active_registration_count(self, obj):
        return getattr(obj, "active_registrations_count", obj.registration_count)

    @admin.display(boolean=True, description="Full?")
    def is_full(self, obj):
        return self.active_registration_count(obj) >= obj.max_teams

    @admin.display(boolean=True, description="Fixtures?")
    def has_fixtures(self, obj):
        return getattr(obj, "fixtures_count", 0) > 0


@admin.register(TournamentRegistration)
class TournamentRegistrationAdmin(admin.ModelAdmin):
    list_display = (
        "entrant",
        "tournament",
        "tournament_type",
        "seed",
        "group_label",
        "is_active",
        "registered_at",
    )
    list_filter = ("tournament", "is_active", "registered_at", "tournament__tournament_type")
    search_fields = (
        "tournament__name",
        "team__name",
        "player__username",
        "player__in_game_name",
        "player__unique_id",
    )
    autocomplete_fields = ("tournament", "team", "player")
    list_select_related = ("tournament", "team", "player")
    ordering = ("tournament__name", "-is_active", "seed", "-registered_at")
    date_hierarchy = "registered_at"

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Entrant")
    def entrant(self, obj):
        return obj.team or obj.player

    @admin.display(description="Type", ordering="tournament__tournament_type")
    def tournament_type(self, obj):
        return obj.tournament.get_tournament_type_display()


@admin.register(Fixture)
class FixtureAdmin(admin.ModelAdmin):
    list_display = (
        "teams",
        "tournament",
        "stage",
        "group_label",
        "round_number",
        "match_date",
        "submission_deadline",
        "result_state",
        "has_result",
        "is_bye",
    )
    list_filter = ("tournament", "stage", "round_number", "is_bye", FixtureResultStateFilter)
    search_fields = ("home_team__name", "away_team__name", "tournament__name")
    readonly_fields = ("created_at", "is_bye")
    list_select_related = ("tournament", "home_team", "away_team")
    ordering = ("tournament__name", "stage", "round_number", "match_date")
    date_hierarchy = "match_date"
    inlines = [ResultInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("tournament", "home_team", "away_team")
            .annotate(
                approved_results_count=Count("results", filter=Q(results__status=Result.APPROVED), distinct=True),
                pending_results_count=Count("results", filter=Q(results__status=Result.PENDING), distinct=True),
                disputed_results_count=Count("results", filter=Q(results__status=Result.DISPUTED), distinct=True),
                rejected_results_count=Count("results", filter=Q(results__status=Result.REJECTED), distinct=True),
            )
        )

    @admin.display(description="Teams")
    def teams(self, obj):
        opponent = obj.away_team.name if obj.away_team else "BYE"
        return f"{obj.home_team.name} vs {opponent}"

    @admin.display(description="Result state")
    def result_state(self, obj):
        if getattr(obj, "approved_results_count", 0):
            return "Approved"
        if getattr(obj, "pending_results_count", 0):
            return "Pending review"
        if getattr(obj, "disputed_results_count", 0):
            return "Disputed"
        if getattr(obj, "rejected_results_count", 0):
            return "Rejected"
        return "No submissions"

    def has_result(self, obj):
        return obj.has_result
    has_result.boolean = True
    has_result.short_description = "Result?"


@admin.register(Result)
class ResultAdmin(admin.ModelAdmin):
    list_display = (
        "fixture",
        "tournament",
        "scoreline",
        "status",
        "submitted_by",
        "submitted_at",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("status", "fixture__tournament", "reviewed_by", "submitted_at")
    search_fields = (
        "fixture__tournament__name",
        "fixture__home_team__name",
        "fixture__away_team__name",
        "submitted_by__username",
        "submitted_by__in_game_name",
    )
    readonly_fields = (
        "submitted_at",
        "submitted_by",
        "match_screenshot",
        "home_player_stats_screenshot_evidence",
        "away_player_stats_screenshot_evidence",
    )
    actions = ["approve_results", "reject_results"]
    inlines = [ResultPlayerStatInline]
    list_select_related = (
        "fixture",
        "fixture__tournament",
        "fixture__home_team",
        "fixture__away_team",
        "submitted_by",
        "reviewed_by",
    )
    date_hierarchy = "submitted_at"

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "fixture",
                "fixture__tournament",
                "fixture__home_team",
                "fixture__away_team",
                "submitted_by",
                "reviewed_by",
            )
            .annotate(
                review_priority=Case(
                    When(status=Result.PENDING, then=Value(0)),
                    When(status=Result.DISPUTED, then=Value(1)),
                    When(status=Result.REJECTED, then=Value(2)),
                    When(status=Result.APPROVED, then=Value(3)),
                    default=Value(4),
                    output_field=IntegerField(),
                )
            )
            .order_by("review_priority", "-submitted_at")
        )

    @admin.display(description="Tournament", ordering="fixture__tournament__name")
    def tournament(self, obj):
        return obj.fixture.tournament

    @admin.display(description="Score")
    def scoreline(self, obj):
        return f"{obj.home_score} - {obj.away_score}"

    @admin.display(description="Match screenshot")
    def match_screenshot(self, obj):
        return obj.screenshot

    @admin.display(description="Home team player-stat screenshot")
    def home_player_stats_screenshot_evidence(self, obj):
        return obj.home_player_stats_screenshot

    @admin.display(description="Away team player-stat screenshot")
    def away_player_stats_screenshot_evidence(self, obj):
        return obj.away_player_stats_screenshot

    @admin.action(description="Approve selected results")
    def approve_results(self, request, queryset):
        pending_results = queryset.filter(status=Result.PENDING)
        pending_count = pending_results.count()
        skipped_count = queryset.count() - pending_count
        processed_count = 0
        fallback_count = 0
        failed_count = 0

        for result in pending_results:
            try:
                score_only_fallback_used = result.approve(
                    admin=request.user,
                    allow_score_only_fallback=True,
                )
            except ValidationError:
                failed_count += 1
                continue
            processed_count += 1
            if score_only_fallback_used:
                fallback_count += 1

        message = f"{processed_count} result(s) approved."
        if skipped_count > 0:
            message += f" {skipped_count} skipped because they were not pending."
        if fallback_count > 0:
            message += (
                f" {fallback_count} approved with score-only fallback (submitted player stats not published)."
            )
        if failed_count > 0:
            message += f" {failed_count} failed due to validation errors."
        self.message_user(request, message)

    @admin.action(description="Reject selected results")
    def reject_results(self, request, queryset):
        pending_results = queryset.filter(status=Result.PENDING)
        processed_count = pending_results.count()
        skipped_count = queryset.count() - processed_count

        for result in pending_results:
            result.reject(admin=request.user, note="Rejected via bulk action.")

        message = f"{processed_count} result(s) rejected."
        if skipped_count > 0:
            message += f" {skipped_count} skipped because they were not pending."
        self.message_user(request, message)
