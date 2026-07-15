import random
import string

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models
from django.db.models import Q
from django.utils import timezone
from cloudinary.models import CloudinaryField
from tournament.validators import PROFILE_IMAGE_MAX_SIZE, validate_image_upload

MAX_IMAGE_UPLOAD_SIZE = PROFILE_IMAGE_MAX_SIZE


def validate_image_size(
    uploaded_file,
    field_name="Image",
    max_size=MAX_IMAGE_UPLOAD_SIZE,
    *,
    as_field_error=False,
):
    validate_image_upload(
        uploaded_file,
        field_name=field_name,
        max_size=max_size,
        max_size_label="500 KB",
        as_field_error=as_field_error,
    )


class Player(AbstractUser):
    SELF_REGISTERED = "self"
    ADMIN_CREATED = "admin"
    PLAYER_TYPE_CHOICES = [
        (SELF_REGISTERED, "Self-registered"),
        (ADMIN_CREATED, "Admin-created"),
    ]

    player_type = models.CharField(
        max_length=10,
        choices=PLAYER_TYPE_CHOICES,
        default=SELF_REGISTERED,
    )
    unique_id = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        help_text="Short public player ID. Auto-generated on signup, editable later.",
    )
    in_game_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Your in-game username (PSN / Xbox / EA tag). Set from your dashboard.",
    )
    avatar = CloudinaryField("avatar", blank=True, null=True)
    bio = models.TextField(blank=True)
    available_for_recruitment = models.BooleanField(
        default=False,
        help_text="Player is listed in the marketplace as available.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Player"
        verbose_name_plural = "Players"
        ordering = ["username"]

    @staticmethod
    def _generate_unique_id():
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

    def __str__(self):
        return f"{self.username} ({self.unique_id or 'no ID'})"

    def clean(self):
        super().clean()
        validate_image_size(self.avatar, field_name="avatar")

    def save(self, *args, **kwargs):
        # Handle blank passwords for admin/demo players by setting unusable password
        if not self.password:
            self.set_unusable_password()

        if self.pk or (self.unique_id and self.unique_id.strip()):
            self.full_clean()
            return super().save(*args, **kwargs)

        last_integrity_error = None
        last_validation_error = None

        for _ in range(10):
            self.unique_id = self._generate_unique_id()
            try:
                self.full_clean()
                return super().save(*args, **kwargs)
            except ValidationError as exc:
                if "unique_id" not in getattr(exc, "message_dict", {}):
                    self.unique_id = ""
                    raise
                last_validation_error = exc
            except IntegrityError as exc:
                if "unique_id" not in str(exc).lower():
                    self.unique_id = ""
                    raise
                last_integrity_error = exc

        self.unique_id = ""
        if last_validation_error:
            raise last_validation_error
        raise last_integrity_error

    @property
    def is_admin_created(self):
        return self.player_type == self.ADMIN_CREATED

    @property
    def can_login(self):
        return bool(self.email) and self.has_usable_password()

    @property
    def display_name(self):
        return self.in_game_name or self.username


class Notification(models.Model):
    class Kind(models.TextChoices):
        REGISTRATION_SUBMITTED = "registration_submitted", "Registration submitted"
        REGISTRATION_APPROVED = "registration_approved", "Registration approved"
        REGISTRATION_REJECTED = "registration_rejected", "Registration rejected"
        TOURNAMENT_REGISTRATION_SUBMITTED = (
            "tournament_registration_submitted",
            "Tournament registration submitted",
        )
        TOURNAMENT_REGISTRATION_APPROVED = (
            "tournament_registration_approved",
            "Tournament registration approved",
        )
        TOURNAMENT_REGISTRATION_REJECTED = (
            "tournament_registration_rejected",
            "Tournament registration rejected",
        )
        RESULT_SUBMITTED = "result_submitted", "Result submitted"
        RESULT_APPROVED = "result_approved", "Result approved"
        RESULT_REJECTED = "result_rejected", "Result rejected"
        RESULT_DISPUTED = "result_disputed", "Result disputed"
        OPPONENT_RESPONSE = "opponent_response", "Opponent response"
        OPPONENT_SCORE_CONFLICT = "opponent_score_conflict", "Opponent score conflict"
        FIXTURE_SCHEDULED = "fixture_scheduled", "Fixture scheduled"
        COMPLAINT_RESPONSE = "complaint_response", "Complaint response"
        MARKETPLACE_INVITE = "marketplace_invite", "Marketplace invite"
        MARKETPLACE_INVITE_ACCEPTED = (
            "marketplace_invite_accepted",
            "Marketplace invite accepted",
        )
        MARKETPLACE_INVITE_REJECTED = (
            "marketplace_invite_rejected",
            "Marketplace invite rejected",
        )
        MARKETPLACE_ASSIGNMENT = "marketplace_assignment", "Marketplace assignment"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=160)
    message = models.TextField()
    kind = models.CharField(max_length=64, choices=Kind.choices)
    url = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["user", "is_read", "-created_at"]),
            models.Index(fields=["kind", "url"]),
        ]

    def __str__(self):
        return f"{self.user}: {self.title}"


class Team(models.Model):
    name = models.CharField(max_length=100, unique=True)
    logo = CloudinaryField("logo", blank=True, null=True)
    captain = models.ForeignKey(
        Player,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captained_teams",
    )
    description = models.TextField(blank=True)
    is_recruiting = models.BooleanField(
        default=False,
        help_text="Team is actively recruiting via the marketplace.",
    )
    is_approved = models.BooleanField(
        default=False,
        help_text="Admin must approve a team before it can join a tournament.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} {'✓' if self.is_approved else '(pending)'}"

    def clean(self):
        super().clean()
        validate_image_size(self.logo, field_name="logo")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def player_count(self):
        return self.memberships.filter(is_active=True).count()


class TeamMembership(models.Model):
    CAPTAIN = "captain"
    PLAYER = "player"
    SUBSTITUTE = "substitute"
    ROLE_CHOICES = [
        (CAPTAIN, "Captain"),
        (PLAYER, "Player"),
        (SUBSTITUTE, "Substitute"),
    ]

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="memberships")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=15, choices=ROLE_CHOICES, default=PLAYER)
    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["team", "role"]
        constraints = [
            models.UniqueConstraint(
                fields=["player", "team"],
                condition=Q(is_active=True),
                name="unique_active_membership_per_player_team",
            ),
            models.UniqueConstraint(
                fields=["player"],
                condition=Q(is_active=True),
                name="unique_active_membership_per_player",
            ),
        ]

    def __str__(self):
        return f"{self.player.username} → {self.team.name} ({self.role})"

    def deactivate(self):
        self.is_active = False
        self.left_at = timezone.now()
        self.save()


class TeamInvitation(models.Model):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (ACCEPTED, "Accepted"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    ]

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="marketplace_invitations",
    )
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="marketplace_invitations",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_marketplace_invitations",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responded_marketplace_invitations",
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "player"],
                condition=Q(status="pending"),
                name="unique_pending_marketplace_invite_per_team_player",
            ),
        ]
        indexes = [
            models.Index(fields=["player", "status", "-created_at"]),
            models.Index(fields=["team", "status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.team.name} -> {self.player.display_name} ({self.get_status_display()})"

    @property
    def is_pending(self):
        return self.status == self.PENDING
