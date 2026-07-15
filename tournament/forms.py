# tournament/forms.py

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import BaseFormSet, formset_factory

from accounts.models import Team, TeamMembership
from tournament.models import (
    Announcement,
    Complaint,
    Fixture,
    Result,
    ResultPlayerStat,
    Tournament,
    TournamentRegistration,
)
from tournament.player_stat_fields import (
    BASIC_PLAYER_STAT_FIELDS,
    INTEGER_PLAYER_STAT_FIELDS,
    PLAYER_STAT_COPY_FIELDS,
    PLAYER_STAT_FIELD_LABELS,
    PLAYER_STAT_FORM_GROUPS,
    PERCENTAGE_PLAYER_STAT_FIELDS,
    validate_player_stat_values,
)
from tournament.validators import RESULT_SCREENSHOT_MAX_SIZE, validate_image_upload


class ResultSubmitForm(forms.ModelForm):
    class Meta:
        model = Result
        fields = [
            "home_score",
            "away_score",
            "screenshot",
            "home_player_stats_screenshot",
            "away_player_stats_screenshot",
        ]

    def __init__(self, *args, fixture=None, player=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixture = fixture
        self.player = player

        self.fields["home_score"].widget.attrs.update({
            "min": 0, "max": 99,
            "class": "score-input",
        })
        self.fields["away_score"].widget.attrs.update({
            "min": 0, "max": 99,
            "class": "score-input",
        })
        self.fields["screenshot"].required = False
        self.fields["screenshot"].label = "Match screenshot (optional)"
        self.fields["screenshot"].help_text = (
            "Upload a JPEG, PNG, GIF, or WebP scoreboard screenshot as proof. Max size: 5 MB."
        )
        self.fields["home_player_stats_screenshot"].required = False
        self.fields["home_player_stats_screenshot"].label = "Home team player-stat screenshot (optional)"
        self.fields["home_player_stats_screenshot"].help_text = (
            "Upload a JPEG, PNG, GIF, or WebP screenshot of the home team's player stats. Max size: 5 MB."
        )
        self.fields["away_player_stats_screenshot"].required = False
        self.fields["away_player_stats_screenshot"].label = "Away team player-stat screenshot (optional)"
        self.fields["away_player_stats_screenshot"].help_text = (
            "Upload a JPEG, PNG, GIF, or WebP screenshot of the away team's player stats. Max size: 5 MB."
        )

    def _clean_result_screenshot(self, field_name):
        upload = self.cleaned_data.get(field_name)
        label = self.fields[field_name].label.replace(" (optional)", "")
        validate_image_upload(
            upload,
            field_name=field_name,
            display_name=label,
            max_size=RESULT_SCREENSHOT_MAX_SIZE,
            max_size_label="5 MB",
            as_field_error=True,
        )
        return upload

    def clean_screenshot(self):
        return self._clean_result_screenshot("screenshot")

    def clean_home_player_stats_screenshot(self):
        return self._clean_result_screenshot("home_player_stats_screenshot")

    def clean_away_player_stats_screenshot(self):
        return self._clean_result_screenshot("away_player_stats_screenshot")

    def clean(self):
        cleaned = super().clean()

        # Validate submitting player is on one of the two teams
        if self.fixture and self.player and not self.player.is_staff:
            from accounts.models import TeamMembership
            player_teams = TeamMembership.objects.filter(
                player=self.player, is_active=True
            ).values_list("team_id", flat=True)

            fixture_teams = [
                self.fixture.home_team_id,
                self.fixture.away_team_id,
            ]

            if not any(t in fixture_teams for t in player_teams):
                raise forms.ValidationError(
                    "You can only submit results for fixtures your team is playing in."
                )

        # Validate fixture doesn't already have an approved result
        if self.fixture and self.instance.pk is None:
            if self.fixture.tournament.is_archived:
                raise forms.ValidationError(
                    self.fixture.tournament.archive_lock_reason()
                )
            if Result.objects.filter(
                fixture=self.fixture, status=Result.APPROVED
            ).exists():
                raise forms.ValidationError(
                    "This fixture already has an approved result."
                )
            if (
                self.player
                and not self.player.is_staff
                and Result.objects.filter(
                    fixture=self.fixture,
                    status__in=[Result.PENDING, Result.DISPUTED],
                ).exists()
            ):
                raise forms.ValidationError(
                    "A result for this fixture is already under review."
                )

        return cleaned

    def save(self, commit=True):
        result = super().save(commit=False)
        if result.pk is None:
            result.fixture = self.fixture
            result.submitted_by = self.player
            result.status = Result.PENDING
            if self.player and not self.player.is_staff and self.fixture:
                fixture_team_ids = [self.fixture.home_team_id, self.fixture.away_team_id]
                result.submitting_team_id = (
                    TeamMembership.objects
                    .filter(
                        player=self.player,
                        is_active=True,
                        team_id__in=fixture_team_ids,
                    )
                    .values_list("team_id", flat=True)
                    .first()
                )
        if commit:
            result.save()
        return result


class OpponentResultResponseForm(forms.Form):
    opponent_home_score = forms.IntegerField(
        min_value=0,
        max_value=99,
        label="Fixture home team score",
        widget=forms.NumberInput(
            attrs={
                "min": 0,
                "max": 99,
                "class": "score-input",
            }
        ),
    )
    opponent_away_score = forms.IntegerField(
        min_value=0,
        max_value=99,
        label="Fixture away team score",
        widget=forms.NumberInput(
            attrs={
                "min": 0,
                "max": 99,
                "class": "score-input",
            }
        ),
    )
    action = forms.ChoiceField(
        choices=[
            (Result.OPPONENT_RESPONSE_CONFIRMED, "Confirm"),
            (Result.OPPONENT_RESPONSE_DISPUTED, "Dispute"),
        ],
        widget=forms.HiddenInput,
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": (
                    "block w-full rounded-2xl border border-slate-300 px-3 py-2 text-sm text-slate-900 "
                    "focus:border-emerald-500 focus:outline-none"
                ),
                "placeholder": "Add a short note if the submitted result is wrong or incomplete.",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get("action")
        note = (cleaned.get("note") or "").strip()
        opponent_home_score = cleaned.get("opponent_home_score")
        opponent_away_score = cleaned.get("opponent_away_score")

        if opponent_home_score is None or opponent_away_score is None:
            raise forms.ValidationError(
                "Enter the fixture home team score and away team score."
            )

        if action == Result.OPPONENT_RESPONSE_DISPUTED and not note:
            raise forms.ValidationError("Add a note when disputing a result.")

        cleaned["note"] = note
        return cleaned


MAX_RESULT_PLAYER_STAT_ROWS = 4


class ResultPlayerStatForm(forms.Form):
    player_id = forms.CharField(
        label="Player",
        required=False,
        widget=forms.Select(choices=()),
    )

    def __init__(self, *args, allowed_players=None, player_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_players = allowed_players or {}
        self.fields["player_id"].widget.choices = [("", "Choose player")] + list(player_choices or [])
        self.fields["player_id"].widget.attrs.update({
            "class": "form-input text-sm",
        })

        for field_name in INTEGER_PLAYER_STAT_FIELDS:
            self.fields[field_name] = forms.IntegerField(
                label=PLAYER_STAT_FIELD_LABELS[field_name],
                min_value=0,
                required=False,
                initial=0,
                widget=forms.NumberInput,
            )
            self.fields[field_name].widget.attrs.update({
                "min": 0,
                "inputmode": "numeric",
                "class": "form-input text-center text-sm tabular-nums",
            })

        for field_name in PERCENTAGE_PLAYER_STAT_FIELDS:
            self.fields[field_name] = forms.DecimalField(
                label=PLAYER_STAT_FIELD_LABELS[field_name],
                min_value=0,
                max_value=100,
                max_digits=5,
                decimal_places=2,
                required=False,
                initial=0,
                widget=forms.NumberInput,
            )
            self.fields[field_name].widget.attrs.update({
                "min": 0,
                "max": 100,
                "step": "0.01",
                "inputmode": "decimal",
                "class": "form-input text-center text-sm tabular-nums",
            })

        self.field_groups = [
            {
                "title": title,
                "fields": [self[field_name] for field_name in field_names],
            }
            for title, field_names in PLAYER_STAT_FORM_GROUPS
        ]

    def _selected_player_id(self):
        raw_value = self["player_id"].value()
        if raw_value in (None, ""):
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None

    def selected_player_row(self):
        player_id = self._selected_player_id()
        if player_id is None:
            return None
        return self.allowed_players.get(player_id)

    def decorate_for_template(self, entry_number):
        self.entry_number = entry_number
        selected = self.selected_player_row()
        self.selected_player_obj = selected["player"] if selected else None
        self.team_obj = selected["team"] if selected else None
        self.side_label = selected["side_label"] if selected else "Home or away team"
        self.summary_team_label = (
            f"{selected['side_label']} - {selected['team'].name}"
            if selected
            else "Choose a fixture player"
        )

        quick_bits = []
        quick_summary_fields = (
            ("goals", "G"),
            ("own_goals", "OG"),
            ("assists", "A"),
            ("yellow_cards", "YC"),
            ("red_cards", "RC"),
        )
        for field_name, short_label in quick_summary_fields:
            raw_value = self[field_name].value()
            try:
                value = int(raw_value or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                quick_bits.append(f"{short_label} {value}")
        self.summary_quick_stats = ", ".join(quick_bits) if quick_bits else "No scoring/cards yet"

    def clean(self):
        cleaned = super().clean()
        raw_player_id = cleaned.get("player_id")

        for field_name in PLAYER_STAT_COPY_FIELDS:
            cleaned[field_name] = cleaned.get(field_name) or 0

        has_entered_stats = any(cleaned[field_name] > 0 for field_name in PLAYER_STAT_COPY_FIELDS)
        if raw_player_id in (None, ""):
            if has_entered_stats:
                raise forms.ValidationError("Choose a player for this stat row.")
            cleaned["player"] = None
            cleaned["team"] = None
            return cleaned

        try:
            player_id = int(raw_player_id)
        except (TypeError, ValueError):
            raise forms.ValidationError("Invalid player stat assignment for this fixture.")

        allowed = self.allowed_players.get(player_id)
        if allowed is None:
            raise forms.ValidationError("Invalid player stat assignment for this fixture.")

        try:
            validate_player_stat_values({
                field_name: cleaned[field_name]
                for field_name in PLAYER_STAT_COPY_FIELDS
            })
        except ValidationError as error:
            if hasattr(error, "message_dict"):
                for field_name, messages in error.message_dict.items():
                    self.add_error(field_name, messages)
            else:
                raise forms.ValidationError(error.messages)

        cleaned["player_id"] = player_id
        cleaned["player"] = allowed["player"]
        cleaned["team"] = allowed["team"]
        return cleaned

    def has_selected_player(self):
        return bool(getattr(self, "cleaned_data", None) and self.cleaned_data.get("player"))


class BaseResultPlayerStatFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        seen_player_ids = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            player = form.cleaned_data.get("player")
            if player is None:
                continue
            if player.pk in seen_player_ids:
                raise forms.ValidationError(
                    "Each player can only have one stat row for this result."
                )
            seen_player_ids.add(player.pk)


ResultPlayerStatFormSet = formset_factory(
    ResultPlayerStatForm,
    formset=BaseResultPlayerStatFormSet,
    extra=0,
    max_num=MAX_RESULT_PLAYER_STAT_ROWS,
    validate_max=True,
)


def _goal_total_reconciliation_errors(*, fixture, home_score, away_score, stat_rows):
    goal_rows = [row for row in stat_rows if row["goals"] > 0 or row["own_goals"] > 0]
    if not goal_rows:
        return []

    home_goals = sum(row["goals"] for row in stat_rows if row["team"].pk == fixture.home_team_id)
    away_goals = sum(row["goals"] for row in stat_rows if row["team"].pk == fixture.away_team_id)
    home_own_goals = sum(row["own_goals"] for row in stat_rows if row["team"].pk == fixture.home_team_id)
    away_own_goals = sum(row["own_goals"] for row in stat_rows if row["team"].pk == fixture.away_team_id)
    computed_home_total = home_goals + away_own_goals
    computed_away_total = away_goals + home_own_goals
    errors = []

    if computed_home_total != home_score:
        errors.append(
            f"{fixture.home_team.name} total from goals and opponent own goals must equal the submitted score ({home_score})."
        )
    if computed_away_total != away_score:
        errors.append(
            f"{fixture.away_team.name} total from goals and opponent own goals must equal the submitted score ({away_score})."
        )
    return errors


def cleaned_result_player_stat_rows(formset):
    rows = []
    if formset is None:
        return rows

    for form in formset.forms:
        if not hasattr(form, "cleaned_data") or not form.cleaned_data:
            continue
        if form.cleaned_data.get("DELETE"):
            continue
        if not form.cleaned_data.get("player"):
            continue
        stat_values = {
            field_name: form.cleaned_data[field_name]
            for field_name in PLAYER_STAT_COPY_FIELDS
        }
        rows.append(
            {
                "player": form.cleaned_data["player"],
                "team": form.cleaned_data["team"],
                **stat_values,
            }
        )
    return rows


def validate_result_goal_totals(*, fixture, home_score, away_score, stat_formset=None, result=None):
    if stat_formset is not None:
        stat_rows = cleaned_result_player_stat_rows(stat_formset)
    elif result is not None:
        stat_rows = [
            {
                "player": stat.player,
                "team": stat.team,
                **{
                    field_name: getattr(stat, field_name)
                    for field_name in PLAYER_STAT_COPY_FIELDS
                },
            }
            for stat in result.submitted_player_stats.select_related("player", "team")
        ]
    else:
        stat_rows = []

    errors = _goal_total_reconciliation_errors(
        fixture=fixture,
        home_score=home_score,
        away_score=away_score,
        stat_rows=stat_rows,
    )
    if errors:
        raise ValidationError(errors)


def _eligible_player_stat_rows(fixture, result=None):
    rows = {}
    fixture_teams = (
        ("Home team", fixture.home_team),
        ("Away team", fixture.away_team),
    )
    for side_label, team in fixture_teams:
        if team is None:
            continue
        memberships = (
            TeamMembership.objects
            .filter(team=team, is_active=True)
            .select_related("player", "team")
            .order_by("role", "player__username")
        )
        for membership in memberships:
            player_label = membership.player.display_name
            rows[membership.player_id] = {
                "player": membership.player,
                "team": membership.team,
                "side_label": side_label,
                "choice_label": f"{player_label} - {side_label}: {membership.team.name}",
            }

    if result is not None:
        existing_stats = result.submitted_player_stats.select_related("player", "team")
        for submitted_stat in existing_stats:
            if submitted_stat.team_id == fixture.home_team_id:
                side_label = "Home team"
            elif submitted_stat.team_id == fixture.away_team_id:
                side_label = "Away team"
            else:
                side_label = "Fixture team"
            player_label = submitted_stat.player.display_name
            rows.setdefault(
                submitted_stat.player_id,
                {
                    "player": submitted_stat.player,
                    "team": submitted_stat.team,
                    "side_label": side_label,
                    "choice_label": f"{player_label} - {side_label}: {submitted_stat.team.name}",
                },
            )

    return list(rows.values())


def build_result_player_stat_formset(*, fixture, data=None, result=None, prefix="player_stats"):
    existing_stats = []
    if result is not None:
        existing_stats = list(
            result.submitted_player_stats
            .select_related("player", "team")
            .order_by("team__name", "player__username")
        )

    rows = _eligible_player_stat_rows(fixture, result=result)
    allowed_players = {
        row["player"].pk: row
        for row in rows
    }
    player_choices = [
        (row["player"].pk, row["choice_label"])
        for row in rows
    ]
    initial = []
    for submitted_stat in existing_stats[:MAX_RESULT_PLAYER_STAT_ROWS]:
        initial.append({
            "player_id": submitted_stat.player_id,
            **{
                field_name: getattr(submitted_stat, field_name)
                for field_name in PLAYER_STAT_COPY_FIELDS
            },
        })
    blank_rows = max(0, MAX_RESULT_PLAYER_STAT_ROWS - len(initial))
    initial.extend({} for _ in range(blank_rows))

    formset = ResultPlayerStatFormSet(
        data=data,
        initial=initial,
        prefix=prefix,
        form_kwargs={
            "allowed_players": allowed_players,
            "player_choices": player_choices,
        },
    )

    for index, form in enumerate(formset.forms, start=1):
        form.decorate_for_template(index)

    return formset


def save_result_player_stats(*, result, formset):
    result.submitted_player_stats.all().delete()

    submitted_rows = []
    for form in formset:
        if not form.has_selected_player():
            continue
        stat_values = {
            field_name: form.cleaned_data[field_name]
            for field_name in PLAYER_STAT_COPY_FIELDS
        }
        submitted_stat = ResultPlayerStat(
            result=result,
            player=form.cleaned_data["player"],
            team=form.cleaned_data["team"],
            **stat_values,
        )
        submitted_stat.full_clean()
        submitted_rows.append(submitted_stat)

    if submitted_rows:
        ResultPlayerStat.objects.bulk_create(submitted_rows)


class StaffFixtureScheduleForm(forms.Form):
    match_date = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    submission_deadline = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        if self.instance is not None and not self.is_bound:
            self.initial["match_date"] = self._as_local_input_value(self.instance.match_date)
            self.initial["submission_deadline"] = self._as_local_input_value(self.instance.submission_deadline)

        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "block w-full rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-900 "
                "focus:border-indigo-500 focus:outline-none"
            )

        self.fields["match_date"].help_text = "Optional. Leave blank if the fixture is not scheduled yet."
        self.fields["submission_deadline"].help_text = "Optional. Leave blank to auto-set it to 24 hours after the match date."

    def _as_local_input_value(self, value):
        if value is None:
            return ""
        return value.strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned = super().clean()
        if self.instance and self.instance.tournament.is_archived:
            raise forms.ValidationError(self.instance.tournament.archive_lock_reason())
        match_date = cleaned.get("match_date")
        submission_deadline = cleaned.get("submission_deadline")

        if submission_deadline and not match_date:
            raise forms.ValidationError("Set a match date before setting a manual submission deadline.")
        if match_date and submission_deadline and submission_deadline <= match_date:
            raise forms.ValidationError("Submission deadline must be after the match date.")

        return cleaned

    def save(self):
        if self.instance is None:
            raise ValueError("StaffFixtureScheduleForm requires a fixture instance.")
        self.instance.match_date = self.cleaned_data["match_date"]
        self.instance.submission_deadline = self.cleaned_data["submission_deadline"]
        if self.instance.match_date is None:
            self.instance.submission_deadline = None
        self.instance.save(update_fields=["match_date", "submission_deadline", "is_bye"])
        return self.instance


class PlayerComplaintForm(forms.ModelForm):
    class Meta:
        model = Complaint
        fields = ["complaint_type", "subject", "description", "fixture", "result"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, player=None, **kwargs):
        self.player = player
        super().__init__(*args, **kwargs)

        self.fields["complaint_type"].label = "Type"
        self.fields["subject"].label = "Subject"
        self.fields["fixture"].required = False
        self.fields["fixture"].empty_label = "No fixture link"
        self.fields["result"].required = False
        self.fields["result"].empty_label = "No result link"

        for field in self.fields.values():
            css = "form-input"
            if isinstance(field.widget, forms.Textarea):
                css += " resize-y"
            field.widget.attrs["class"] = css

        self.fields["fixture"].queryset = self._fixture_queryset()
        self.fields["result"].queryset = self._result_queryset()

    def _active_team_ids(self):
        if not self.player or not getattr(self.player, "is_authenticated", False):
            return []
        return list(
            TeamMembership.objects.filter(
                player=self.player,
                is_active=True,
            ).values_list("team_id", flat=True)
        )

    def _fixture_queryset(self):
        fixtures = (
            Fixture.objects
            .filter(is_bye=False)
            .exclude(tournament__status=Tournament.DRAFT)
            .select_related("tournament", "home_team", "away_team")
            .order_by("-match_date", "-created_at", "tournament__name")
        )
        if self.player and getattr(self.player, "is_staff", False):
            return fixtures

        active_team_ids = self._active_team_ids()
        if not active_team_ids:
            return Fixture.objects.none()
        return fixtures.filter(
            Q(home_team_id__in=active_team_ids)
            | Q(away_team_id__in=active_team_ids)
        )

    def _result_queryset(self):
        results = (
            Result.objects
            .exclude(fixture__tournament__status=Tournament.DRAFT)
            .select_related(
                "fixture__tournament",
                "fixture__home_team",
                "fixture__away_team",
                "submitted_by",
                "submitting_team",
            )
            .order_by("-submitted_at", "-pk")
        )
        if self.player and getattr(self.player, "is_staff", False):
            return results

        active_team_ids = self._active_team_ids()
        filters = Q(submitted_by=self.player)
        if active_team_ids:
            filters |= Q(submitting_team_id__in=active_team_ids)
        return results.filter(filters)

    def clean_subject(self):
        return (self.cleaned_data["subject"] or "").strip()

    def clean_description(self):
        return (self.cleaned_data["description"] or "").strip()

    def clean(self):
        cleaned = super().clean()
        fixture = cleaned.get("fixture")
        result = cleaned.get("result")
        if result and fixture and result.fixture_id != fixture.pk:
            self.add_error("result", "Linked result must belong to the selected fixture.")
        if result and not fixture:
            cleaned["fixture"] = result.fixture
        return cleaned

    def save(self, commit=True):
        complaint = super().save(commit=False)
        complaint.player = self.player
        if complaint.result_id and not complaint.fixture_id:
            complaint.fixture = complaint.result.fixture
        if commit:
            complaint.save()
        return complaint


class StaffComplaintUpdateForm(forms.ModelForm):
    class Meta:
        model = Complaint
        fields = ["status", "staff_response"]
        widgets = {
            "staff_response": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "form-input"
            if isinstance(field.widget, forms.Textarea):
                css += " resize-y"
            field.widget.attrs["class"] = css

    def clean_staff_response(self):
        return (self.cleaned_data["staff_response"] or "").strip()


class TournamentStaffForm(forms.ModelForm):
    class Meta:
        model = Tournament
        fields = [
            "name",
            "tournament_type",
            "format",
            "hybrid_qualifiers_per_group",
            "status",
            "max_teams",
            "registration_deadline",
            "start_date",
            "end_date",
            "description",
            "tiebreaker_rules",
        ]
        widgets = {
            "registration_deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 4}),
            "tiebreaker_rules": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            css = "block w-full rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none"
            if isinstance(field.widget, forms.Textarea):
                css += " resize-y"
            field.widget.attrs["class"] = css
        self.fields["hybrid_qualifiers_per_group"].required = False
        self.fields["hybrid_qualifiers_per_group"].help_text = (
            "Hybrid tournaments only: choose whether top 2 or top 4 teams from each group "
            "advance to knockout."
        )

        selected_format = self._selected_format_value()
        self.show_hybrid_qualifier_field = selected_format == Tournament.HYBRID
        lock_reason = self._hybrid_qualifier_lock_reason()
        self.hybrid_qualifier_locked = bool(lock_reason)
        self.hybrid_qualifier_lock_reason = lock_reason or ""
        if self.hybrid_qualifier_locked:
            self.fields["hybrid_qualifiers_per_group"].widget.attrs["disabled"] = "disabled"

        self.fields["description"].help_text = "Public summary shown on tournament pages."
        self.fields["tiebreaker_rules"].help_text = 'JSON list, for example ["goal_difference", "head_to_head"].'

    def _selected_format_value(self):
        if self.is_bound:
            return (self.data.get(self.add_prefix("format")) or "").strip()
        if self.initial.get("format"):
            return self.initial["format"]
        if self.instance is not None and self.instance.pk:
            return self.instance.format
        return Tournament.ROUND_ROBIN

    def _hybrid_qualifier_lock_reason(self):
        if self.instance is None or not self.instance.pk:
            return None
        return self.instance.hybrid_qualifier_lock_reason()

    def clean(self):
        cleaned = super().clean()
        if self.instance is not None and self.instance.pk and self.instance.is_archived:
            raise forms.ValidationError(self.instance.archive_lock_reason())
        selected_format = (cleaned.get("format") or "").strip()
        current_value = (
            self.instance.hybrid_qualifiers_per_group
            if self.instance is not None and self.instance.pk
            else Tournament.HYBRID_QUALIFIERS_TOP_2
        )
        proposed_value = cleaned.get("hybrid_qualifiers_per_group")

        if selected_format != Tournament.HYBRID:
            cleaned["hybrid_qualifiers_per_group"] = current_value
            return cleaned

        if proposed_value in [None, ""]:
            proposed_value = current_value
        cleaned["hybrid_qualifiers_per_group"] = proposed_value

        lock_reason = self._hybrid_qualifier_lock_reason()
        if lock_reason and proposed_value != current_value:
            self.add_error("hybrid_qualifiers_per_group", lock_reason)

        return cleaned


class TournamentRegistrationStaffForm(forms.Form):
    seed = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"min": 1, "placeholder": "Auto"}),
    )
    is_active = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        if self.instance is not None and not self.is_bound:
            self.fields["seed"].initial = self.instance.seed
            self.fields["is_active"].initial = self.instance.is_active
        self.fields["seed"].widget.attrs["class"] = (
            "w-24 rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-900 "
            "focus:border-indigo-500 focus:outline-none"
        )
        self.fields["is_active"].widget.attrs["class"] = (
            "h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
        )

    def clean(self):
        cleaned = super().clean()
        if self.instance is not None and self.instance.tournament.is_archived:
            raise forms.ValidationError(self.instance.tournament.archive_lock_reason())
        return cleaned

    def save(self):
        if self.instance is None:
            raise ValueError("TournamentRegistrationStaffForm requires an instance to save.")
        self.instance.seed = self.cleaned_data["seed"]
        self.instance.is_active = self.cleaned_data["is_active"]
        self.instance.full_clean()
        self.instance.save(update_fields=["seed", "is_active", "group_label"])
        return self.instance


class TournamentGroupAssignmentForm(forms.Form):
    group_label = forms.CharField(
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "A"}),
    )

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)
        if self.instance is not None and not self.is_bound:
            self.fields["group_label"].initial = self.instance.group_label
        self.fields["group_label"].widget.attrs["class"] = (
            "w-24 rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-900 "
            "focus:border-indigo-500 focus:outline-none"
        )

    def clean_group_label(self):
        return (self.cleaned_data["group_label"] or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        if self.instance is None:
            raise forms.ValidationError("TournamentGroupAssignmentForm requires an instance to save.")

        group_label = cleaned.get("group_label", "")
        tournament = self.instance.tournament

        if tournament.tournament_type != Tournament.TEAM:
            raise forms.ValidationError("Group assignment is only available for team tournaments.")
        if not self.instance.team_id:
            raise forms.ValidationError("Only team entrants can be assigned to groups.")
        if not self.instance.is_active and group_label:
            raise forms.ValidationError("Inactive entrants cannot be assigned to a group.")
        if tournament.entrant_change_lock_reason():
            raise forms.ValidationError(tournament.entrant_change_lock_reason())

        return cleaned

    def save(self):
        if self.instance is None:
            raise ValueError("TournamentGroupAssignmentForm requires an instance to save.")
        self.instance.group_label = self.cleaned_data["group_label"]
        self.instance.full_clean()
        self.instance.save(update_fields=["group_label"])
        return self.instance


class TournamentManualTeamEntryForm(forms.Form):
    team = forms.ModelChoiceField(
        queryset=Team.objects.none(),
        empty_label="Select a team",
    )

    def __init__(self, *args, tournament=None, **kwargs):
        self.tournament = tournament
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = self._team_queryset()
        self.fields["team"].widget.attrs["class"] = (
            "min-w-[16rem] rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-900 "
            "focus:border-indigo-500 focus:outline-none"
        )

    def _team_queryset(self):
        if self.tournament is None or self.tournament.tournament_type != Tournament.TEAM:
            return Team.objects.none()
        return (
            Team.objects
            .filter(is_approved=True)
            .exclude(tournament_entries__tournament=self.tournament, tournament_entries__is_active=True)
            .order_by("name")
        )

    def clean_team(self):
        team = self.cleaned_data["team"]
        roster_eligibility = Tournament.team_roster_eligibility_for_team(team)
        if not roster_eligibility["roster_eligibility_ok"]:
            raise forms.ValidationError(
                "Teams must have between "
                f"{Tournament.TEAM_ROSTER_MIN_PLAYERS} and {Tournament.TEAM_ROSTER_MAX_PLAYERS} "
                "active players to register."
            )
        return team

class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['title', 'body', 'is_active', 'is_pinned', 'tournament', 'sort_order']
