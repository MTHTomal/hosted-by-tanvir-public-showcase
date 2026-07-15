# tournament/models.py
# Handles: Tournament, Fixture, Result, ResultScreenshot, Complaint

from django.db import models, transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone
from cloudinary.models import CloudinaryField
from tournament.player_stat_fields import PLAYER_STAT_COPY_FIELDS, validate_player_stat_values


class Announcement(models.Model):
    title = models.CharField(max_length=160)
    body = models.TextField()
    is_active = models.BooleanField(default=True)
    is_pinned = models.BooleanField(default=False)
    tournament = models.ForeignKey(
        "Tournament",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="announcements",
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "sort_order", "-created_at", "title"]

    def __str__(self):
        return self.title


class Tournament(models.Model):
    TEAM_ROSTER_MIN_PLAYERS = 2
    TEAM_ROSTER_MAX_PLAYERS = 3

    TEAM = "team"
    SINGLE = "single"
    TOURNAMENT_TYPE_CHOICES = [
        (TEAM, "Team"),
        (SINGLE, "Single-player"),
    ]

    ROUND_ROBIN = "round_robin"
    KNOCKOUT = "knockout"
    HYBRID = "hybrid"
    FORMAT_CHOICES = [
        (ROUND_ROBIN, "Round Robin"),
        (KNOCKOUT, "Knockout"),
        (HYBRID, "Hybrid (Group stage + Knockout)"),
    ]
    HYBRID_QUALIFIERS_TOP_2 = 2
    HYBRID_QUALIFIERS_TOP_4 = 4
    HYBRID_QUALIFIER_CHOICES = [
        (HYBRID_QUALIFIERS_TOP_2, "Top 2"),
        (HYBRID_QUALIFIERS_TOP_4, "Top 4"),
    ]

    DRAFT = "draft"
    REGISTRATION = "registration"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (REGISTRATION, "Registration open"),
        (ACTIVE, "Active"),
        (COMPLETED, "Completed"),
        (ARCHIVED, "Archived"),
    ]

    TEAM_COUNT_CHOICES = [(4, "4"), (8, "8"), (16, "16"), (32, "32")]

    name = models.CharField(max_length=150, unique=True)
    tournament_type = models.CharField(
        max_length=10,
        choices=TOURNAMENT_TYPE_CHOICES,
        default=TEAM,
    )
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default=ROUND_ROBIN)
    max_teams = models.IntegerField(choices=TEAM_COUNT_CHOICES, default=8)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)
    registration_deadline = models.DateTimeField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    hybrid_qualifiers_per_group = models.PositiveSmallIntegerField(
        choices=HYBRID_QUALIFIER_CHOICES,
        default=HYBRID_QUALIFIERS_TOP_2,
        help_text="How many teams per group qualify to knockout in hybrid tournaments.",
    )

    # Tiebreaker rules (stored as ordered list in JSON for flexibility)
    tiebreaker_rules = models.JSONField(
        default=list,
        help_text='Ordered list e.g. ["goal_difference", "head_to_head", "cards"]',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    @property
    def is_registration_open(self):
        if self.status != self.REGISTRATION:
            return False
        if self.registration_deadline and timezone.now() > self.registration_deadline:
            return False
        return True

    @property
    def is_archived(self):
        return self.status == self.ARCHIVED

    def archive_lock_reason(self):
        if self.is_archived:
            return "Archived tournaments are read-only."
        return None

    @property
    def registration_count(self):
        return self.registrations.filter(is_active=True).count()

    @property
    def team_count(self):
        return self.registration_count

    @property
    def participant_label(self):
        return "players" if self.tournament_type == self.SINGLE else "teams"

    @property
    def is_team_tournament(self):
        return self.tournament_type == self.TEAM

    @property
    def is_single_tournament(self):
        return self.tournament_type == self.SINGLE

    @property
    def has_fixtures(self):
        return self.fixtures.exists()

    @property
    def has_results(self):
        return Result.objects.filter(fixture__tournament=self).exists()

    def entrant_change_lock_reason(self):
        archive_lock_reason = self.archive_lock_reason()
        if archive_lock_reason:
            return archive_lock_reason
        if self.has_results:
            return "Entrants cannot be changed after results have been submitted."
        if self.has_fixtures:
            return "Entrants cannot be changed after fixtures have been generated."
        return None

    @property
    def entrant_changes_allowed(self):
        return self.entrant_change_lock_reason() is None

    @property
    def entrant_lock_state(self):
        if self.has_results:
            return "results"
        if self.has_fixtures:
            return "fixtures"
        return "open"

    def hybrid_qualifier_lock_reason(self):
        has_elimination_fixtures = self.fixtures.filter(
            stage__in=[Fixture.KNOCKOUT, Fixture.FINAL]
        ).exists()
        if has_elimination_fixtures:
            return (
                "Hybrid qualifier setting cannot be changed after knockout fixtures "
                "have been generated."
            )
        return None

    @classmethod
    def team_roster_eligibility_for_count(cls, active_player_count):
        if active_player_count < cls.TEAM_ROSTER_MIN_PLAYERS:
            return {
                "roster_eligibility_ok": False,
                "roster_eligibility_state": "below_minimum",
                "roster_eligibility_label": "Below Minimum",
                "roster_eligibility_message": (
                    f"Needs at least {cls.TEAM_ROSTER_MIN_PLAYERS} active players for tournament eligibility."
                ),
                "roster_min_players": cls.TEAM_ROSTER_MIN_PLAYERS,
                "roster_max_players": cls.TEAM_ROSTER_MAX_PLAYERS,
                "active_player_count": active_player_count,
            }
        if active_player_count > cls.TEAM_ROSTER_MAX_PLAYERS:
            return {
                "roster_eligibility_ok": False,
                "roster_eligibility_state": "above_maximum",
                "roster_eligibility_label": "Above Maximum",
                "roster_eligibility_message": (
                    f"Has more than {cls.TEAM_ROSTER_MAX_PLAYERS} active players allowed for tournament eligibility."
                ),
                "roster_min_players": cls.TEAM_ROSTER_MIN_PLAYERS,
                "roster_max_players": cls.TEAM_ROSTER_MAX_PLAYERS,
                "active_player_count": active_player_count,
            }
        return {
            "roster_eligibility_ok": True,
            "roster_eligibility_state": "eligible",
            "roster_eligibility_label": "Eligible",
            "roster_eligibility_message": (
                f"Meets the current tournament roster rule of {cls.TEAM_ROSTER_MIN_PLAYERS} to "
                f"{cls.TEAM_ROSTER_MAX_PLAYERS} active players."
            ),
            "roster_min_players": cls.TEAM_ROSTER_MIN_PLAYERS,
            "roster_max_players": cls.TEAM_ROSTER_MAX_PLAYERS,
            "active_player_count": active_player_count,
        }

    @classmethod
    def team_roster_eligibility_for_team(cls, team):
        return cls.team_roster_eligibility_for_count(team.player_count)


class TournamentRegistration(models.Model):
    """
    Which teams are entered in which tournament.
    Separate from TeamMembership — a team can exist without being in a tournament.
    """
    from accounts.models import Team  # local import to avoid circular

    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="registrations"
    )
    team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.CASCADE,
        related_name="tournament_entries",
        null=True,
        blank=True,
    )
    player = models.ForeignKey(
        "accounts.Player",
        on_delete=models.CASCADE,
        related_name="tournament_entries",
        null=True,
        blank=True,
    )
    seed = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Bracket seed assigned by admin."
    )
    group_label = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Optional manual group assignment for team tournaments.",
    )
    is_active = models.BooleanField(default=True)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["seed", "registered_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "team"],
                condition=Q(team__isnull=False),
                name="unique_team_registration_per_tournament",
            ),
            models.UniqueConstraint(
                fields=["tournament", "player"],
                condition=Q(player__isnull=False),
                name="unique_player_registration_per_tournament",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(team__isnull=False) & Q(player__isnull=True)) |
                    (Q(team__isnull=True) & Q(player__isnull=False))
                ),
                name="exactly_one_registration_target",
            ),
        ]

    def __str__(self):
        entrant_name = self.team.name if self.team_id else self.player.display_name
        return f"{entrant_name} in {self.tournament.name}"

    def clean(self):
        super().clean()

        errors = {}
        if self.team_id and self.player_id:
            raise ValidationError("A registration cannot belong to both a team and a player.")
        if not self.team_id and not self.player_id:
            raise ValidationError("A registration must belong to either a team or a player.")

        if not self.tournament_id:
            if errors:
                raise ValidationError(errors)
            return

        self.group_label = (self.group_label or "").strip().upper()

        if self.tournament.tournament_type == Tournament.TEAM:
            if not self.team_id:
                errors["team"] = "Team tournaments require a team registration."
            if self.player_id:
                errors["player"] = "Team tournaments cannot be registered by an individual player."
            if self.group_label and not self.team_id:
                errors["group_label"] = "Only team entrants can be assigned to a group."
            if self.team_id:
                if not self.team.is_approved:
                    errors["team"] = "Only approved teams can be entered into tournaments."
                roster_eligibility = Tournament.team_roster_eligibility_for_team(self.team)
                if not roster_eligibility["roster_eligibility_ok"]:
                    errors["team"] = (
                        "Teams must have between "
                        f"{Tournament.TEAM_ROSTER_MIN_PLAYERS} and {Tournament.TEAM_ROSTER_MAX_PLAYERS} "
                        f"active players to register. This team currently has "
                        f"{roster_eligibility['active_player_count']} active player(s)."
                    )

        if self.tournament.tournament_type == Tournament.SINGLE:
            if not self.player_id:
                errors["player"] = "Single-player tournaments require a player registration."
            if self.team_id:
                errors["team"] = "Single-player tournaments do not accept team registrations."
            if self.group_label:
                errors["group_label"] = "Group assignment is only available for team tournaments."

        existing = None
        if self.pk:
            existing = type(self).objects.filter(pk=self.pk).first()

        if self.is_active:
            active_registration_count = (
                type(self)
                .objects
                .filter(tournament=self.tournament, is_active=True)
                .exclude(pk=self.pk)
                .count()
            )
            if active_registration_count >= self.tournament.max_teams:
                target_field = "player" if self.tournament.tournament_type == Tournament.SINGLE else "team"
                errors[target_field] = (
                    f"This tournament already has the maximum of {self.tournament.max_teams} "
                    f"active {self.tournament.participant_label}."
                )

        entrant_lock_reason = self.tournament.entrant_change_lock_reason()
        entrant_membership_changed = existing is None or (
            existing.team_id != self.team_id
            or existing.player_id != self.player_id
            or existing.is_active != self.is_active
        )
        group_assignment_changed = existing is not None and existing.group_label != self.group_label
        if entrant_lock_reason and entrant_membership_changed:
            errors["__all__"] = entrant_lock_reason
        if entrant_lock_reason and group_assignment_changed:
            errors["__all__"] = entrant_lock_reason

        if errors:
            raise ValidationError(errors)


class Fixture(models.Model):
    """
    A single scheduled match within a tournament.

    Phase design note: round and stage fields are stored here intentionally.
    Phase 5 adds hybrid format (group stage → knockout). If you store only
    round numbers without a stage, the hybrid refactor is painful.
    Store stage now even though Phase 1 only uses round_robin / knockout.
    """

    GROUP = "group"
    KNOCKOUT = "knockout"
    FINAL = "final"
    STAGE_CHOICES = [
        (GROUP, "Group stage"),
        (KNOCKOUT, "Knockout"),
        (FINAL, "Final"),
    ]

    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="fixtures"
    )
    home_team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="home_fixtures"
    )
    away_team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.CASCADE,
        related_name="away_fixtures",
        null=True,
        blank=True,
    )
    round_number = models.PositiveSmallIntegerField(
        help_text="Round within the tournament (1-indexed)."
    )
    stage = models.CharField(
        max_length=20,
        choices=STAGE_CHOICES,
        default=GROUP,
        help_text="Stage of the tournament this fixture belongs to.",
    )
    group_label = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Optional stored group context for grouped stage fixtures.",
    )
    match_date = models.DateTimeField(null=True, blank=True)
    submission_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Auto-set to 24h after match_date. Admin alerted if no result by this time.",
    )
    is_bye = models.BooleanField(
        default=False,
        help_text="True for bye fixtures generated in uneven brackets.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("tournament", "home_team", "away_team", "round_number")
        ordering = ["round_number", "match_date"]

    def __str__(self):
        opponent = self.away_team.name if self.away_team else "BYE"
        return (
            f"{self.tournament.name} | R{self.round_number} | "
            f"{self.home_team.name} vs {opponent}"
        )

    def clean(self):
        super().clean()

        self.group_label = (self.group_label or "").strip().upper()

        if self.away_team_id and self.home_team_id == self.away_team_id:
            raise ValidationError({
                "away_team": "A fixture cannot have the same team on both sides.",
            })

    def save(self, *args, **kwargs):
        self.group_label = (self.group_label or "").strip().upper()
        # Keep the legacy flag in sync while the app still references it.
        self.is_bye = self.away_team is None
        # Auto-set submission deadline to 24h after match_date
        if self.match_date and not self.submission_deadline:
            from datetime import timedelta
            self.submission_deadline = self.match_date + timedelta(hours=24)
        super().save(*args, **kwargs)

    @property
    def result(self):
        """Returns the approved result for this fixture, or None."""
        return Result.latest_approved_for_fixture(self)

    @property
    def has_result(self):
        return self.results.filter(status=Result.APPROVED).exists()


class Result(models.Model):
    """
    A result submission for a fixture.
    Multiple submissions can exist (resubmit after rejection).
    Only one APPROVED result should exist per fixture — enforced in save().
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPUTED = "disputed"
    OPPONENT_RESPONSE_PENDING = "pending"
    OPPONENT_RESPONSE_CONFIRMED = "confirmed"
    OPPONENT_RESPONSE_DISPUTED = "disputed"
    OPPONENT_SCORE_AWAITING = "awaiting_opponent"
    OPPONENT_SCORE_MATCHING = "matching"
    OPPONENT_SCORE_CONFLICT = "score_conflict"
    OPPONENT_SCORE_NOT_APPLICABLE = "not_applicable"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (DISPUTED, "Disputed"),
    ]
    OPPONENT_RESPONSE_CHOICES = [
        (OPPONENT_RESPONSE_PENDING, "Pending"),
        (OPPONENT_RESPONSE_CONFIRMED, "Confirmed"),
        (OPPONENT_RESPONSE_DISPUTED, "Disputed"),
    ]
    OPPONENT_SCORE_STATE_CHOICES = [
        (OPPONENT_SCORE_AWAITING, "Awaiting opponent score"),
        (OPPONENT_SCORE_MATCHING, "Scores match"),
        (OPPONENT_SCORE_CONFLICT, "Score conflict"),
        (OPPONENT_SCORE_NOT_APPLICABLE, "Not applicable"),
    ]

    fixture = models.ForeignKey(Fixture, on_delete=models.CASCADE, related_name="results")
    submitted_by = models.ForeignKey(
        "accounts.Player",
        on_delete=models.SET_NULL,
        null=True,
        related_name="submitted_results",
    )
    submitting_team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_fixture_results",
    )
    home_score = models.PositiveSmallIntegerField()
    away_score = models.PositiveSmallIntegerField()
    screenshot = CloudinaryField(
        "result_screenshot",
        help_text="Proof screenshot uploaded by the submitting team.",
        blank=True,
        null=True,
    )
    home_player_stats_screenshot = CloudinaryField(
        "home_player_stats_screenshot",
        help_text="Optional screenshot showing the home team's submitted player stats.",
        blank=True,
        null=True,
    )
    away_player_stats_screenshot = CloudinaryField(
        "away_player_stats_screenshot",
        help_text="Optional screenshot showing the away team's submitted player stats.",
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=PENDING)
    admin_note = models.TextField(
        blank=True,
        help_text="Admin note shown to submitter when result is rejected.",
    )
    opponent_response_status = models.CharField(
        max_length=15,
        choices=OPPONENT_RESPONSE_CHOICES,
        default=OPPONENT_RESPONSE_PENDING,
        help_text="Whether the opposing team confirmed or disputed this submitted result.",
    )
    opponent_response_note = models.TextField(
        blank=True,
        help_text="Optional note from the opposing team when disputing the submitted result.",
    )
    opponent_home_score = models.PositiveSmallIntegerField(null=True, blank=True)
    opponent_away_score = models.PositiveSmallIntegerField(null=True, blank=True)
    opponent_score_state = models.CharField(
        max_length=25,
        choices=OPPONENT_SCORE_STATE_CHOICES,
        default=OPPONENT_SCORE_AWAITING,
        help_text="Advisory comparison between submitted score and opponent-entered fixture home/away score.",
    )
    opponent_responded_at = models.DateTimeField(null=True, blank=True)
    opponent_responded_by = models.ForeignKey(
        "accounts.Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opponent_result_responses",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_results",
    )

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["fixture"],
                condition=Q(status="approved"),
                name="unique_approved_result_per_fixture",
            ),
        ]

    def __str__(self):
        return (
            f"{self.fixture} | {self.home_score}–{self.away_score} ({self.get_status_display()})"
        )

    @property
    def opponent_team_id(self):
        if not self.submitting_team_id or not self.fixture_id:
            return None
        if self.submitting_team_id == self.fixture.home_team_id:
            return self.fixture.away_team_id
        if self.submitting_team_id == self.fixture.away_team_id:
            return self.fixture.home_team_id
        return None

    @property
    def opponent_response_open(self):
        return (
            self.status in [self.PENDING, self.DISPUTED]
            and self.fixture.away_team_id is not None
            and self.submitting_team_id is not None
            and self.opponent_team_id is not None
        )

    @property
    def has_opponent_score(self):
        return self.opponent_home_score is not None and self.opponent_away_score is not None

    @property
    def has_partial_opponent_score(self):
        return (self.opponent_home_score is None) != (self.opponent_away_score is None)

    @property
    def scores_match_opponent(self):
        return (
            self.has_opponent_score
            and self.home_score == self.opponent_home_score
            and self.away_score == self.opponent_away_score
        )

    @property
    def has_score_conflict(self):
        return self.opponent_score_state == self.OPPONENT_SCORE_CONFLICT

    @property
    def opponent_score_state_label(self):
        return self.get_opponent_score_state_display()

    def refresh_opponent_score_state(self):
        if self.has_opponent_score:
            self.opponent_score_state = (
                self.OPPONENT_SCORE_MATCHING
                if self.scores_match_opponent
                else self.OPPONENT_SCORE_CONFLICT
            )
        elif self.fixture_id and not self.fixture.away_team_id:
            self.opponent_score_state = self.OPPONENT_SCORE_NOT_APPLICABLE
        else:
            self.opponent_score_state = self.OPPONENT_SCORE_AWAITING

    def clean(self):
        super().clean()
        if not self.fixture_id:
            return
        if self.has_partial_opponent_score:
            raise ValidationError({
                "opponent_home_score": "Enter both opponent fixture home and away scores.",
            })
        self.refresh_opponent_score_state()
        if self.status == self.APPROVED:
            approved_qs = type(self).objects.filter(
                fixture_id=self.fixture_id,
                status=self.APPROVED,
            )
            if self.pk:
                approved_qs = approved_qs.exclude(pk=self.pk)
            if approved_qs.exists():
                raise ValidationError({
                    "status": "Only one approved result can exist for a fixture.",
                })
        if self.submitting_team_id and self.submitting_team_id not in {
            self.fixture.home_team_id,
            self.fixture.away_team_id,
        }:
            raise ValidationError({
                "submitting_team": "Submitting team must be one of the fixture participants.",
            })
        if (
            self.opponent_response_status == self.OPPONENT_RESPONSE_DISPUTED
            and not self.opponent_response_note.strip()
        ):
            raise ValidationError({
                "opponent_response_note": "Add a note when the opposing team disputes a result.",
            })
        if self.opponent_response_status == self.OPPONENT_RESPONSE_CONFIRMED:
            self.opponent_response_note = ""

    def record_opponent_response(self, *, player, status, home_score, away_score, note=""):
        note = (note or "").strip()
        if status not in {
            self.OPPONENT_RESPONSE_CONFIRMED,
            self.OPPONENT_RESPONSE_DISPUTED,
        }:
            raise ValidationError({"opponent_response_status": "Invalid opponent response."})
        if home_score is None or away_score is None:
            raise ValidationError({
                "opponent_home_score": "Enter both opponent fixture home and away scores.",
            })
        self.opponent_response_status = status
        self.opponent_response_note = note if status == self.OPPONENT_RESPONSE_DISPUTED else ""
        self.opponent_home_score = home_score
        self.opponent_away_score = away_score
        self.refresh_opponent_score_state()
        self.opponent_responded_by = player
        self.opponent_responded_at = timezone.now()
        self.full_clean()
        self.save(
            update_fields=[
                "opponent_response_status",
                "opponent_response_note",
                "opponent_home_score",
                "opponent_away_score",
                "opponent_score_state",
                "opponent_responded_by",
                "opponent_responded_at",
            ]
        )

    @classmethod
    def latest_approved_for_fixture(cls, fixture):
        return (
            cls.objects
            .filter(fixture=fixture, status=cls.APPROVED)
            .order_by("-reviewed_at", "-submitted_at", "-pk")
            .first()
        )

    @classmethod
    def latest_actionable_for_fixture(cls, fixture):
        return (
            cls.objects
            .filter(fixture=fixture, status__in=[cls.PENDING, cls.DISPUTED, cls.APPROVED])
            .order_by(
                models.Case(
                    models.When(status=cls.APPROVED, then=models.Value(0)),
                    models.When(status=cls.DISPUTED, then=models.Value(1)),
                    models.When(status=cls.PENDING, then=models.Value(2)),
                    default=models.Value(3),
                    output_field=models.IntegerField(),
                ),
                "-reviewed_at",
                "-submitted_at",
                "-pk",
            )
            .first()
        )

    def sync_official_player_stats(self):
        from standings.models import PlayerStat

        PlayerStat.objects.filter(fixture=self.fixture).delete()

        official_rows = []
        for submitted_stat in self.submitted_player_stats.select_related("player", "team").all():
            stat_values = {
                field_name: getattr(submitted_stat, field_name)
                for field_name in PLAYER_STAT_COPY_FIELDS
            }
            official_rows.append(
                PlayerStat(
                    player=submitted_stat.player,
                    fixture=self.fixture,
                    team=submitted_stat.team,
                    **stat_values,
                )
            )

        if official_rows:
            PlayerStat.objects.bulk_create(official_rows)

    def clear_official_player_stats(self):
        from standings.models import PlayerStat

        PlayerStat.objects.filter(fixture=self.fixture).delete()

    def validate_goal_totals(self):
        from tournament.forms import validate_result_goal_totals

        validate_result_goal_totals(
            fixture=self.fixture,
            home_score=self.home_score,
            away_score=self.away_score,
            result=self,
        )

    def approve(self, admin, *, allow_score_only_fallback=False):
        """Approve this result and safely supersede any older approved row."""
        timestamp = timezone.now()
        score_only_fallback_used = False
        fallback_note = ""
        with transaction.atomic():
            fixture_results = type(self).objects.select_for_update().filter(fixture=self.fixture)
            try:
                self.validate_goal_totals()
            except ValidationError as error:
                if not allow_score_only_fallback:
                    raise
                score_only_fallback_used = True
                fallback_note = (
                    "Approved with score-only fallback: submitted player stats were not "
                    "published because stat totals did not match the final score."
                )
                if error.messages:
                    fallback_note = f"{fallback_note} {' '.join(error.messages)}"
            fixture_results.filter(
                status=self.APPROVED,
            ).exclude(pk=self.pk).update(
                status=self.REJECTED,
                reviewed_by=admin,
                reviewed_at=timestamp,
                admin_note="Superseded by a newer approved correction.",
            )
            self.status = self.APPROVED
            self.reviewed_by = admin
            self.reviewed_at = timestamp
            self.admin_note = fallback_note if score_only_fallback_used else ""
            self.full_clean()
            self.save()
            if score_only_fallback_used:
                self.clear_official_player_stats()
            else:
                self.sync_official_player_stats()
        return score_only_fallback_used

    def reject(self, admin, note=""):
        was_approved = self.status == self.APPROVED
        self.status = self.REJECTED
        self.reviewed_by = admin
        self.reviewed_at = timezone.now()
        self.admin_note = note
        self.save()
        if was_approved:
            self.clear_official_player_stats()

    def dispute(self, admin, note=""):
        was_approved = self.status == self.APPROVED
        self.status = self.DISPUTED
        self.reviewed_by = admin
        self.reviewed_at = timezone.now()
        self.admin_note = note
        self.save()
        if was_approved:
            self.clear_official_player_stats()


class Complaint(models.Model):
    RESULT_ISSUE = "result_issue"
    SCHEDULE_ISSUE = "schedule_issue"
    TEAM_PLAYER_ISSUE = "team_player_issue"
    GENERAL_REQUEST = "general_request"
    COMPLAINT_TYPE_CHOICES = [
        (RESULT_ISSUE, "Result issue"),
        (SCHEDULE_ISSUE, "Schedule issue"),
        (TEAM_PLAYER_ISSUE, "Team/player issue"),
        (GENERAL_REQUEST, "General request"),
    ]

    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    STATUS_CHOICES = [
        (OPEN, "Open"),
        (UNDER_REVIEW, "Under review"),
        (RESOLVED, "Resolved"),
        (REJECTED, "Rejected"),
    ]

    player = models.ForeignKey(
        "accounts.Player",
        on_delete=models.CASCADE,
        related_name="complaints",
    )
    complaint_type = models.CharField(max_length=25, choices=COMPLAINT_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=OPEN)
    subject = models.CharField(max_length=160)
    description = models.TextField()
    staff_response = models.TextField(blank=True)
    fixture = models.ForeignKey(
        Fixture,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints",
    )
    result = models.ForeignKey(
        Result,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints",
    )
    responded_by = models.ForeignKey(
        "accounts.Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responded_complaints",
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["player", "status", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.subject} - {self.player}"

    def clean(self):
        super().clean()
        if self.fixture_id and self.result_id and self.result.fixture_id != self.fixture_id:
            raise ValidationError({
                "result": "Linked result must belong to the selected fixture.",
            })


class ResultPlayerStat(models.Model):
    result = models.ForeignKey(
        Result,
        on_delete=models.CASCADE,
        related_name="submitted_player_stats",
    )
    player = models.ForeignKey(
        "accounts.Player",
        on_delete=models.CASCADE,
        related_name="submitted_match_stats",
    )
    team = models.ForeignKey(
        "accounts.Team",
        on_delete=models.CASCADE,
        related_name="submitted_match_stats",
    )
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

    class Meta:
        ordering = ["team__name", "player__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["result", "player"],
                name="unique_player_stat_per_submitted_result",
            ),
        ]

    def __str__(self):
        return f"{self.player.username} | {self.result.fixture}"

    def clean(self):
        super().clean()
        if not self.result_id:
            return

        errors = {}
        fixture = self.result.fixture
        fixture_team_ids = {fixture.home_team_id, fixture.away_team_id}

        if self.team_id not in fixture_team_ids:
            errors["team"] = "Submitted stat team must be one of the fixture participants."

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
