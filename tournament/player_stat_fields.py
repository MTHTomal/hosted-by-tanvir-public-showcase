from decimal import Decimal

from django.core.exceptions import ValidationError


BASIC_PLAYER_STAT_FIELDS = (
    "goals",
    "own_goals",
    "assists",
    "yellow_cards",
    "red_cards",
)

ADVANCED_PLAYER_STAT_FIELDS = (
    "total_points",
    "offensive_positioning",
    "shooting",
    "dueling",
    "defensive_positioning",
    "passing",
    "dribbling",
)

DETAILED_INTEGER_PLAYER_STAT_FIELDS = (
    "shots",
    "shots_on_target",
    "key_passes",
    "passes",
    "successful_passes",
    "instrumental_passes",
    "dribbles",
    "successful_dribbles",
    "instrumental_dribbles",
    "receiving",
    "good_receives",
    "overlaps",
    "runs_out_wide",
    "forward_runs",
    "offensive_receives",
    "intercepts",
    "tackles",
    "impactful_steals",
    "frontal_presses",
    "presses_from_behind",
    "double_marks",
    "passes_obstructed",
    "players_marked",
)

PERCENTAGE_PLAYER_STAT_FIELDS = ("good_positioning_pct",)

INTEGER_PLAYER_STAT_FIELDS = (
    BASIC_PLAYER_STAT_FIELDS
    + ADVANCED_PLAYER_STAT_FIELDS
    + DETAILED_INTEGER_PLAYER_STAT_FIELDS
)

PLAYER_STAT_COPY_FIELDS = INTEGER_PLAYER_STAT_FIELDS + PERCENTAGE_PLAYER_STAT_FIELDS

PLAYER_STAT_FIELD_LABELS = {
    "goals": "Goals",
    "own_goals": "Own goals",
    "assists": "Assists",
    "yellow_cards": "Yellow cards",
    "red_cards": "Red cards",
    "total_points": "Total points",
    "offensive_positioning": "Offensive positioning",
    "shooting": "Shooting",
    "dueling": "Dueling",
    "defensive_positioning": "Defensive positioning",
    "passing": "Passing",
    "dribbling": "Dribbling",
    "shots": "Shots",
    "shots_on_target": "Shots on target",
    "key_passes": "Key passes",
    "passes": "Passes",
    "successful_passes": "Successful passes",
    "instrumental_passes": "Instrumental passes",
    "dribbles": "Dribbles",
    "successful_dribbles": "Successful dribbles",
    "instrumental_dribbles": "Instrumental dribbles",
    "receiving": "Receiving",
    "good_receives": "Good receives",
    "overlaps": "Overlaps",
    "runs_out_wide": "Runs out wide",
    "forward_runs": "Forward runs",
    "offensive_receives": "Offensive receives",
    "intercepts": "Intercepts",
    "tackles": "Tackles",
    "impactful_steals": "Impactful steals",
    "frontal_presses": "Frontal presses",
    "presses_from_behind": "Presses from behind",
    "good_positioning_pct": "Good positioning %",
    "double_marks": "Double marks",
    "passes_obstructed": "Passes obstructed",
    "players_marked": "Players marked",
}

PLAYER_STAT_FORM_GROUPS = (
    ("Basic", BASIC_PLAYER_STAT_FIELDS),
    ("Advanced", ADVANCED_PLAYER_STAT_FIELDS),
    (
        "Detailed",
        (
            "shots",
            "shots_on_target",
            "key_passes",
            "passes",
            "successful_passes",
            "instrumental_passes",
            "dribbles",
            "successful_dribbles",
            "instrumental_dribbles",
            "receiving",
            "good_receives",
            "overlaps",
            "runs_out_wide",
            "forward_runs",
            "offensive_receives",
            "intercepts",
            "tackles",
            "impactful_steals",
            "frontal_presses",
            "presses_from_behind",
            "good_positioning_pct",
            "double_marks",
            "passes_obstructed",
            "players_marked",
        ),
    ),
)

COMPOUND_PLAYER_STAT_LIMITS = (
    ("shots_on_target", "shots", "Shots on target cannot exceed shots."),
    ("successful_passes", "passes", "Successful passes cannot exceed passes."),
    ("successful_dribbles", "dribbles", "Successful dribbles cannot exceed dribbles."),
)


def normalize_player_stat_value(value):
    if value in (None, ""):
        return 0
    return value


def validate_player_stat_values(values):
    errors = {}
    for field_name in INTEGER_PLAYER_STAT_FIELDS:
        value = normalize_player_stat_value(values.get(field_name))
        if value < 0:
            errors[field_name] = "Enter a non-negative value."

    good_positioning_pct = normalize_player_stat_value(values.get("good_positioning_pct"))
    if good_positioning_pct < 0 or good_positioning_pct > Decimal("100"):
        errors["good_positioning_pct"] = "Good positioning percentage must be between 0 and 100."

    for child_field, parent_field, message in COMPOUND_PLAYER_STAT_LIMITS:
        child_value = normalize_player_stat_value(values.get(child_field))
        parent_value = normalize_player_stat_value(values.get(parent_field))
        if child_value > parent_value:
            errors[child_field] = message

    if errors:
        raise ValidationError(errors)
