# standings/models.py
# Handles: Standing, PlayerStat
#
# PHASE DESIGN NOTE (read before touching this file):
# ─────────────────────────────────────────────────────
# PlayerStat is intentionally linked to Fixture, NOT to Tournament.
# This is what allows Phase 3 career stats to aggregate across tournaments
# with a single GROUP BY player query, without a schema change.
#
# If you later need "goals in tournament X", filter by fixture__tournament=X.
# If you need "career goals", filter by player=P across all fixtures.
#
# Do NOT add a direct ForeignKey to Tournament on PlayerStat. Ever.

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Prefetch, Q, Sum
from tournament.player_stat_fields import PLAYER_STAT_COPY_FIELDS, validate_player_stat_values


class Standing(models.Model):
    """
    League table row for a team in a tournament.
    Recalculated automatically when a Result is approved (via Django signal).
    Never edited directly — always recalculated from approved Results.
    """

    tournament = models.ForeignKey(
        "tournament.Tournament",
        on_delete=models.CASCADE,
        related_name="standings",
    )
    team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.CASCADE,
        related_name="standings",
    )

    played = models.PositiveSmallIntegerField(default=0)
    wins = models.PositiveSmallIntegerField(default=0)
    draws = models.PositiveSmallIntegerField(default=0)
    losses = models.PositiveSmallIntegerField(default=0)
    goals_for = models.PositiveSmallIntegerField(default=0)
    goals_against = models.PositiveSmallIntegerField(default=0)
    points = models.PositiveSmallIntegerField(default=0)

    # Cached for fast ordering — recalculated on every update
    goal_difference = models.SmallIntegerField(default=0)

    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tournament", "team")
        ordering = [
            "-points",
            "-goal_difference",
            "-goals_for",
        ]

    def __str__(self):
        return f"{self.team.name} in {self.tournament.name}: {self.points}pts"

    def recalculate(self):
        """
        Recalculate all fields from approved results.
        Called by the post_save signal on Result approval.
        """
        from tournament.models import Fixture, Result

        fixtures = (
            Fixture.objects
            .filter(
                tournament=self.tournament,
                is_bye=False,
            )
            .filter(Q(home_team=self.team) | Q(away_team=self.team))
            .prefetch_related(
                Prefetch(
                    "results",
                    queryset=(
                        Result.objects
                        .filter(status=Result.APPROVED)
                        .only(
                            "fixture_id",
                            "home_score",
                            "away_score",
                            "reviewed_at",
                            "submitted_at",
                            "pk",
                        )
                        .order_by("-reviewed_at", "-submitted_at", "-pk")
                    ),
                )
            )
        )

        wins = draws = losses = gf = ga = 0

        for fixture in fixtures:
            approved_results = list(fixture.results.all())
            if not approved_results:
                continue
            result = approved_results[0]

            if fixture.home_team_id == self.team_id:
                gf += result.home_score
                ga += result.away_score
                if result.home_score > result.away_score:
                    wins += 1
                elif result.home_score == result.away_score:
                    draws += 1
                else:
                    losses += 1
            else:
                gf += result.away_score
                ga += result.home_score
                if result.away_score > result.home_score:
                    wins += 1
                elif result.away_score == result.home_score:
                    draws += 1
                else:
                    losses += 1

        self.played = wins + draws + losses
        self.wins = wins
        self.draws = draws
        self.losses = losses
        self.goals_for = gf
        self.goals_against = ga
        self.goal_difference = gf - ga
        self.points = (wins * 3) + draws
        if self.played == 0 and self.pk:
            self.delete()
            return
        self.save()


class PlayerStat(models.Model):
    """
    Per-player, per-fixture stat record.

    Linked to Fixture — NOT Tournament — so career aggregation works
    across all tournaments without schema changes. See module docstring.

    One row per player per fixture. Created when a result is approved
    and admin fills in per-player stats (goals, cards).
    Players who didn't play in a fixture have no row here.
    """

    player = models.ForeignKey(
        "accounts.Player",
        on_delete=models.CASCADE,
        related_name="stats",
    )
    fixture = models.ForeignKey(
        "tournament.Fixture",
        on_delete=models.CASCADE,
        related_name="player_stats",
    )
    team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.CASCADE,
        related_name="player_stats",
        help_text="Which team the player was representing in this fixture.",
    )

    # Core stats
    goals = models.PositiveSmallIntegerField(default=0)
    own_goals = models.PositiveSmallIntegerField(default=0)
    assists = models.PositiveSmallIntegerField(default=0)
    yellow_cards = models.PositiveSmallIntegerField(default=0)
    red_cards = models.PositiveSmallIntegerField(default=0)
    total_points = models.PositiveSmallIntegerField(default=0)
    offensive_positioning = models.PositiveSmallIntegerField(default=0)
    shooting = models.PositiveSmallIntegerField(default=0)
    dueling = models.PositiveSmallIntegerField(default=0)
    defensive_positioning = models.PositiveSmallIntegerField(default=0)
    passing = models.PositiveSmallIntegerField(default=0)
    dribbling = models.PositiveSmallIntegerField(default=0)
    shots = models.PositiveSmallIntegerField(default=0)
    shots_on_target = models.PositiveSmallIntegerField(default=0)
    key_passes = models.PositiveSmallIntegerField(default=0)
    passes = models.PositiveSmallIntegerField(default=0)
    successful_passes = models.PositiveSmallIntegerField(default=0)
    instrumental_passes = models.PositiveSmallIntegerField(default=0)
    dribbles = models.PositiveSmallIntegerField(default=0)
    successful_dribbles = models.PositiveSmallIntegerField(default=0)
    instrumental_dribbles = models.PositiveSmallIntegerField(default=0)
    receiving = models.PositiveSmallIntegerField(default=0)
    good_receives = models.PositiveSmallIntegerField(default=0)
    overlaps = models.PositiveSmallIntegerField(default=0)
    runs_out_wide = models.PositiveSmallIntegerField(default=0)
    forward_runs = models.PositiveSmallIntegerField(default=0)
    offensive_receives = models.PositiveSmallIntegerField(default=0)
    intercepts = models.PositiveSmallIntegerField(default=0)
    tackles = models.PositiveSmallIntegerField(default=0)
    impactful_steals = models.PositiveSmallIntegerField(default=0)
    frontal_presses = models.PositiveSmallIntegerField(default=0)
    presses_from_behind = models.PositiveSmallIntegerField(default=0)
    good_positioning_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    double_marks = models.PositiveSmallIntegerField(default=0)
    passes_obstructed = models.PositiveSmallIntegerField(default=0)
    players_marked = models.PositiveSmallIntegerField(default=0)

    # Optional: used for Man of the Match (Phase 3)
    man_of_the_match = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("player", "fixture")
        ordering = ["-fixture__match_date"]

    def __str__(self):
        return (
            f"{self.player.username} | {self.fixture} | "
            f"{self.goals}G {self.assists}A"
        )

    def clean(self):
        super().clean()
        errors = {}

        if self.fixture_id:
            fixture_team_ids = {self.fixture.home_team_id, self.fixture.away_team_id}
            if self.team_id not in fixture_team_ids:
                errors["team"] = "Official stat team must be one of the fixture participants."

            if self.player_id and self.team_id and self.team_id in fixture_team_ids:
                from accounts.models import TeamMembership

                active_member = TeamMembership.objects.filter(
                    player_id=self.player_id,
                    team_id=self.team_id,
                    is_active=True,
                ).exists()
                existing_same_row = bool(
                    self.pk
                    and type(self).objects.filter(
                        pk=self.pk,
                        player_id=self.player_id,
                        team_id=self.team_id,
                    ).exists()
                )
                if not active_member and not existing_same_row:
                    errors["player"] = "Selected player must belong to the chosen fixture team."

        try:
            validate_player_stat_values({
                field_name: getattr(self, field_name)
                for field_name in PLAYER_STAT_COPY_FIELDS
            })
        except ValidationError as error:
            if hasattr(error, "message_dict"):
                errors.update(error.message_dict)
            else:
                errors["__all__"] = error.messages

        if errors:
            raise ValidationError(errors)

    # ── Career stat helpers ──────────────────────────────────────────────

    @classmethod
    def career_goals(cls, player):
        result = cls.objects.filter(player=player).aggregate(total=Sum("goals"))
        return result["total"] or 0

    @classmethod
    def career_assists(cls, player):
        result = cls.objects.filter(player=player).aggregate(total=Sum("assists"))
        return result["total"] or 0

    @classmethod
    def tournament_goals(cls, player, tournament):
        result = cls.objects.filter(
            player=player, fixture__tournament=tournament
        ).aggregate(total=Sum("goals"))
        return result["total"] or 0

    @classmethod
    def top_scorers(cls, tournament, limit=10):
        """Returns queryset of players ordered by goals in a tournament."""
        return (
            cls.objects.filter(fixture__tournament=tournament)
            .values("player__username", "player__in_game_name", "player__id")
            .annotate(total_goals=Sum("goals"))
            .order_by("-total_goals")[:limit]
        )

    @classmethod
    def top_assists(cls, tournament, limit=10):
        """Returns queryset of players ordered by assists in a tournament."""
        return (
            cls.objects.filter(fixture__tournament=tournament)
            .values("player__username", "player__in_game_name", "player__id")
            .annotate(total_assists=Sum("assists"))
            .order_by("-total_assists")[:limit]
        )
