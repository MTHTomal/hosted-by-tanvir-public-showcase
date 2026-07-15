from django.contrib import admin
from .models import Standing, PlayerStat


@admin.register(Standing)
class StandingAdmin(admin.ModelAdmin):
    list_display = ("team", "tournament", "played", "wins", "draws", "losses", "goals_for", "goals_against", "goal_difference", "points")
    list_filter = ("tournament",)
    search_fields = ("team__name", "tournament__name")
    ordering = ("tournament", "-points", "-goal_difference")
    readonly_fields = ("last_updated",)
    list_select_related = ("team", "tournament")


@admin.register(PlayerStat)
class PlayerStatAdmin(admin.ModelAdmin):
    list_display = ("player", "fixture", "team", "goals", "own_goals", "assists", "yellow_cards", "red_cards", "man_of_the_match")
    list_filter = ("fixture__tournament", "team")
    search_fields = ("player__username", "player__in_game_name", "team__name", "fixture__tournament__name")
    autocomplete_fields = ("player", "team")
    list_select_related = ("player", "fixture", "fixture__tournament", "team")
    ordering = ("-fixture__match_date", "player__username")
