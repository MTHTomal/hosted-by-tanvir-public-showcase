from django import forms
from django.contrib.auth.forms import UserCreationForm
from accounts.marketplace import (
    active_membership_for_player,
    captained_teams_for_user,
    team_has_capacity,
)
from accounts.models import Player, Team, validate_image_size


class PlayerRegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    role = forms.ChoiceField(
        choices=[("player", "I am a player"), ("owner", "I am a team owner")],
        widget=forms.RadioSelect,
        initial="player",
    )
    team_name = forms.CharField(
        max_length=100,
        required=False,
        help_text="Your team name. You can change this later.",
    )

    class Meta:
        model = Player
        fields = ["username", "email", "password1", "password2"]

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        team_name = cleaned.get("team_name", "").strip()

        if role == "owner" and not team_name:
            self.add_error("team_name", "Please enter your team name.")

        if role == "owner" and team_name:
            if Team.objects.filter(name__iexact=team_name).exists():
                self.add_error("team_name", "A team with this name already exists.")

        return cleaned

    def save(self, commit=True):
        player = super().save(commit=False)
        player.email = self.cleaned_data["email"]
        player.player_type = Player.SELF_REGISTERED
        player.is_active = True   # players can log in immediately
        player.in_game_name = ""

        if commit:
            player.save()
            if self.cleaned_data.get("role") == "owner":
                from accounts.models import TeamMembership
                # Team created with is_approved=False — needs admin approval
                team = Team.objects.create(
                    name=self.cleaned_data["team_name"].strip(),
                    captain=player,
                    is_approved=False,
                )
                TeamMembership.objects.create(
                    player=player,
                    team=team,
                    role=TeamMembership.CAPTAIN,
                )

        return player


class PlayerProfileForm(forms.ModelForm):
    class Meta:
        model = Player
        fields = ["in_game_name", "unique_id", "bio", "avatar"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["in_game_name"].widget.attrs.update({
            "class": "block w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:border-gray-900",
            "placeholder": "e.g. XxSniper99xX, FC_Tanvir",
        })
        self.fields["unique_id"].widget.attrs.update({
            "class": "block w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:border-gray-900",
            "placeholder": "Short player ID shown on your profile",
        })
        self.fields["bio"].widget.attrs.update({
            "class": "block w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:border-gray-900",
            "rows": 3,
            "placeholder": "A short bio (optional)",
        })
        self.fields["avatar"].required = False
        self.fields["avatar"].help_text = "JPEG, PNG, GIF, or WebP. Max size: 500 KB."
        self.fields["avatar"].widget.attrs.update({
            "class": "form-input block w-full text-sm text-slate-600 file:mr-3 file:rounded-xl file:border file:border-emerald-200 file:bg-white file:px-4 file:py-2 file:text-sm hover:file:bg-emerald-50",
            "accept": "image/*",
        })
        self.fields["in_game_name"].required = False
        self.fields["bio"].required = False

    def clean_unique_id(self):
        uid = self.cleaned_data.get("unique_id", "").strip()
        if not uid:
            raise forms.ValidationError("Player ID cannot be empty.")
        qs = Player.objects.filter(unique_id=uid).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("This Player ID is already taken.")
        return uid

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        validate_image_size(avatar, field_name="avatar", as_field_error=True)
        return avatar


class TeamIdentityForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name", "logo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({
            "class": "form-input !rounded-xl !px-3 !py-2",
            "placeholder": "Team name",
        })
        self.fields["logo"].required = False
        self.fields["logo"].help_text = "JPEG, PNG, GIF, or WebP. Max size: 500 KB."
        self.fields["logo"].widget.attrs.update({
            "class": "form-input block w-full text-sm text-slate-600 file:mr-3 file:rounded-xl file:border file:border-emerald-200 file:bg-white file:px-4 file:py-2 file:text-sm hover:file:bg-emerald-50",
            "accept": "image/*",
        })

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Team name cannot be empty.")
        qs = Team.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A team with this name already exists.")
        return name

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        validate_image_size(logo, field_name="logo", as_field_error=True)
        return logo


class StaffPlayerIdentityForm(forms.ModelForm):
    class Meta:
        model = Player
        fields = ["in_game_name", "avatar"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["in_game_name"].required = False
        self.fields["in_game_name"].widget.attrs.update({
            "class": "form-input !rounded-xl !px-3 !py-2",
            "placeholder": "Display name",
        })
        self.fields["avatar"].required = False
        self.fields["avatar"].help_text = "JPEG, PNG, GIF, or WebP. Max size: 500 KB."
        self.fields["avatar"].widget.attrs.update({
            "class": "form-input block w-full text-sm text-slate-600 file:mr-3 file:rounded-xl file:border file:border-emerald-200 file:bg-white file:px-4 file:py-2 file:text-sm hover:file:bg-emerald-50",
            "accept": "image/*",
        })

    def clean_in_game_name(self):
        return self.cleaned_data.get("in_game_name", "").strip()

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        validate_image_size(avatar, field_name="avatar", as_field_error=True)
        return avatar


class PlayerAvailabilityForm(forms.ModelForm):
    class Meta:
        model = Player
        fields = ["available_for_recruitment"]
        labels = {
            "available_for_recruitment": "List me as available in the marketplace",
        }

    def clean_available_for_recruitment(self):
        available = self.cleaned_data.get("available_for_recruitment")
        if available and active_membership_for_player(self.instance):
            raise forms.ValidationError("Rostered players cannot be listed as available.")
        return available


class TeamRecruitingForm(forms.Form):
    team = forms.ModelChoiceField(queryset=Team.objects.none())
    is_recruiting = forms.BooleanField(
        required=False,
        label="List this team as recruiting in the marketplace",
    )

    def __init__(self, *args, captain=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = captained_teams_for_user(captain)
        self.fields["team"].widget.attrs.update({
            "class": "block w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:border-gray-900",
        })

    def clean(self):
        cleaned = super().clean()
        team = cleaned.get("team")
        is_recruiting = cleaned.get("is_recruiting")
        if team and is_recruiting and not team_has_capacity(team):
            self.add_error("is_recruiting", "Full teams cannot be listed as recruiting.")
        return cleaned


class CaptainInviteForm(forms.Form):
    team = forms.ModelChoiceField(queryset=Team.objects.none())
    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=1000,
    )

    def __init__(self, *args, captain=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["team"].queryset = captained_teams_for_user(captain)
        self.fields["team"].widget.attrs.update({
            "class": "block w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:border-gray-900",
        })
        self.fields["message"].widget.attrs.update({
            "class": "block w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:border-gray-900",
            "placeholder": "Optional short note",
        })


class StaffAssignmentForm(forms.Form):
    player = forms.ModelChoiceField(queryset=Player.objects.none())
    team = forms.ModelChoiceField(queryset=Team.objects.none())
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=1000,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["player"].queryset = (
            Player.objects
            .filter(is_active=True, is_staff=False)
            .order_by("username")
        )
        self.fields["team"].queryset = Team.objects.select_related("captain").order_by("name")
        for field_name in ["player", "team"]:
            self.fields[field_name].widget.attrs.update({
                "class": "block w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:border-gray-900",
            })
        self.fields["note"].widget.attrs.update({
            "class": "block w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:border-gray-900",
            "placeholder": "Optional staff note",
        })
