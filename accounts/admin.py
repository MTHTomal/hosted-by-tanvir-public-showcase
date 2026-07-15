from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count, Q
from accounts.models import Notification, Player, Team, TeamInvitation, TeamMembership, validate_image_size


class PlayerAdminForm(forms.ModelForm):
    class Meta:
        model = Player
        fields = "__all__"

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        validate_image_size(avatar, field_name="avatar", as_field_error=True)
        return avatar


class TeamAdminForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = "__all__"

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        validate_image_size(logo, field_name="logo", as_field_error=True)
        return logo


@admin.register(Player)
class PlayerAdmin(UserAdmin):
    form = PlayerAdminForm

    list_display = [
        "username", "in_game_name", "unique_id",
        "player_type", "is_active", "email", "date_joined",
    ]
    list_filter = ["player_type", "is_active"]
    search_fields = ["username", "in_game_name", "unique_id", "email"]

    fieldsets = UserAdmin.fieldsets + (
        ("Tournament info", {
            "fields": (
                "player_type", "unique_id", "in_game_name",
                "avatar", "bio", "available_for_recruitment",
            ),
            "description": "Avatar must be a JPEG, PNG, GIF, or WebP image and 500 KB or smaller.",
        }),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Tournament info", {
            "fields": ("player_type", "unique_id", "in_game_name", "email"),
        }),
    )


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 0
    autocomplete_fields = ("player",)
    fields = ("player", "role", "is_active", "joined_at", "left_at")
    readonly_fields = ("joined_at", "left_at")
    ordering = ("is_active", "role", "player__username")
    show_change_link = True


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    form = TeamAdminForm

    list_display = [
        "name", "captain", "active_player_count",
        "is_approved", "is_recruiting", "created_at",
    ]
    list_filter = ["is_approved", "is_recruiting"]
    search_fields = ["name", "captain__username", "captain__in_game_name", "captain__unique_id"]
    autocomplete_fields = ["captain"]
    readonly_fields = ("created_at", "active_player_count")
    list_select_related = ("captain",)
    inlines = [TeamMembershipInline]
    actions = ["approve_teams", "reject_teams"]
    fieldsets = (
        (None, {"fields": ("name", "captain", "description", "logo")}),
        ("Approval", {"fields": ("is_approved", "is_recruiting")}),
        ("Team overview", {"fields": ("active_player_count", "created_at")}),
    )

    # Show pending teams first by default
    ordering = ["is_approved", "created_at"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("captain")
            .annotate(
                active_memberships_count=Count(
                    "memberships",
                    filter=Q(memberships__is_active=True),
                    distinct=True,
                )
            )
        )

    @admin.action(description="Approve selected teams")
    def approve_teams(self, request, queryset):
        updated = queryset.update(is_approved=True)
        self.message_user(
            request,
            f"{updated} team(s) approved. They can now be registered in tournaments.",
            level=messages.SUCCESS,
        )

    @admin.action(description="Reject / unapprove selected teams")
    def reject_teams(self, request, queryset):
        updated = queryset.update(is_approved=False)
        self.message_user(
            request,
            f"{updated} team(s) unapproved.",
            level=messages.WARNING,
        )

    @admin.display(description="Players", ordering="active_memberships_count")
    def active_player_count(self, obj):
        return getattr(obj, "active_memberships_count", obj.player_count)


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ["player", "team", "role", "is_active", "joined_at"]
    list_filter = ["role", "is_active", "team"]
    search_fields = ["player__username", "player__in_game_name", "player__unique_id", "team__name", "team__captain__username"]
    autocomplete_fields = ["player", "team"]
    list_select_related = ("player", "team", "team__captain")
    ordering = ("-is_active", "team__name", "role", "player__username")


@admin.register(TeamInvitation)
class TeamInvitationAdmin(admin.ModelAdmin):
    list_display = ["team", "player", "invited_by", "status", "created_at", "responded_at"]
    list_filter = ["status", "created_at", "responded_at"]
    search_fields = [
        "team__name",
        "player__username",
        "player__in_game_name",
        "player__unique_id",
        "invited_by__username",
    ]
    autocomplete_fields = ["team", "player", "invited_by", "responded_by"]
    readonly_fields = ["created_at", "updated_at"]
    list_select_related = ("team", "player", "invited_by", "responded_by")
    ordering = ["-created_at", "-pk"]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "kind", "is_read", "created_at"]
    list_filter = ["kind", "is_read", "created_at"]
    search_fields = ["title", "message", "user__username", "user__email"]
    autocomplete_fields = ["user"]
    readonly_fields = ["created_at"]
    ordering = ["-created_at"]
