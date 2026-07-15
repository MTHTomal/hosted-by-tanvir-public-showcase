import secrets
from datetime import timedelta

from django.utils import timezone


DEMO_INTERNAL_TAG = "DEMO_VALIDATION_T1"
DEMO_USER_PREFIX = "demo_val_"
DEMO_EMAIL_DOMAIN = "demo-validation.local"
DEMO_RESULT_SCREENSHOT_PREFIX = "demo-validation/"

DEMO_STAFF = {
    "username": f"{DEMO_USER_PREFIX}staff",
    "email": f"{DEMO_USER_PREFIX}staff@{DEMO_EMAIL_DOMAIN}",
    "unique_id": "DMOSTAFF",
    "in_game_name": "Validation Staff",
}

DEMO_TOURNAMENTS = {
    "non_grouped": "Validation Cup",
    "grouped": "Validation Groups Cup",
}

DEMO_TEAM_SPECS = [
    {
        "name": "Atlas FC",
        "approved": True,
        "members": [
            {"username": f"{DEMO_USER_PREFIX}atlas_cap", "email": f"{DEMO_USER_PREFIX}atlas_cap@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMATLCAP", "in_game_name": "Atlas Captain", "role": "captain"},
            {"username": f"{DEMO_USER_PREFIX}atlas_p1", "email": f"{DEMO_USER_PREFIX}atlas_p1@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMATLP1", "in_game_name": "Atlas Player 1", "role": "player"},
            {"username": f"{DEMO_USER_PREFIX}atlas_p2", "email": f"{DEMO_USER_PREFIX}atlas_p2@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMATLP2", "in_game_name": "Atlas Player 2", "role": "player"},
        ],
    },
    {
        "name": "Blaze FC",
        "approved": True,
        "members": [
            {"username": f"{DEMO_USER_PREFIX}blaze_cap", "email": f"{DEMO_USER_PREFIX}blaze_cap@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMBLZCAP", "in_game_name": "Blaze Captain", "role": "captain"},
            {"username": f"{DEMO_USER_PREFIX}blaze_p1", "email": f"{DEMO_USER_PREFIX}blaze_p1@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMBLZP1", "in_game_name": "Blaze Player 1", "role": "player"},
            {"username": f"{DEMO_USER_PREFIX}blaze_p2", "email": f"{DEMO_USER_PREFIX}blaze_p2@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMBLZP2", "in_game_name": "Blaze Player 2", "role": "player"},
        ],
    },
    {
        "name": "Comets FC",
        "approved": True,
        "members": [
            {"username": f"{DEMO_USER_PREFIX}comets_cap", "email": f"{DEMO_USER_PREFIX}comets_cap@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMCMTCAP", "in_game_name": "Comets Captain", "role": "captain"},
            {"username": f"{DEMO_USER_PREFIX}comets_p1", "email": f"{DEMO_USER_PREFIX}comets_p1@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMCMTP1", "in_game_name": "Comets Player 1", "role": "player"},
            {"username": f"{DEMO_USER_PREFIX}comets_p2", "email": f"{DEMO_USER_PREFIX}comets_p2@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMCMTP2", "in_game_name": "Comets Player 2", "role": "player"},
        ],
    },
    {
        "name": "Dragons FC",
        "approved": True,
        "members": [
            {"username": f"{DEMO_USER_PREFIX}dragons_cap", "email": f"{DEMO_USER_PREFIX}dragons_cap@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMDRGCAP", "in_game_name": "Dragons Captain", "role": "captain"},
            {"username": f"{DEMO_USER_PREFIX}dragons_p1", "email": f"{DEMO_USER_PREFIX}dragons_p1@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMDRGP1", "in_game_name": "Dragons Player 1", "role": "player"},
            {"username": f"{DEMO_USER_PREFIX}dragons_p2", "email": f"{DEMO_USER_PREFIX}dragons_p2@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMDRGP2", "in_game_name": "Dragons Player 2", "role": "player"},
        ],
    },
    {
        "name": "Eclipse FC",
        "approved": True,
        "members": [
            {"username": f"{DEMO_USER_PREFIX}eclipse_cap", "email": f"{DEMO_USER_PREFIX}eclipse_cap@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMECLCAP", "in_game_name": "Eclipse Captain", "role": "captain"},
            {"username": f"{DEMO_USER_PREFIX}eclipse_p1", "email": f"{DEMO_USER_PREFIX}eclipse_p1@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMECLP1", "in_game_name": "Eclipse Player 1", "role": "player"},
            {"username": f"{DEMO_USER_PREFIX}eclipse_p2", "email": f"{DEMO_USER_PREFIX}eclipse_p2@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMECLP2", "in_game_name": "Eclipse Player 2", "role": "player"},
        ],
    },
    {
        "name": "Falcons FC",
        "approved": True,
        "members": [
            {"username": f"{DEMO_USER_PREFIX}falcons_cap", "email": f"{DEMO_USER_PREFIX}falcons_cap@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMFALCAP", "in_game_name": "Falcons Captain", "role": "captain"},
            {"username": f"{DEMO_USER_PREFIX}falcons_p1", "email": f"{DEMO_USER_PREFIX}falcons_p1@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMFALP1", "in_game_name": "Falcons Player 1", "role": "player"},
            {"username": f"{DEMO_USER_PREFIX}falcons_p2", "email": f"{DEMO_USER_PREFIX}falcons_p2@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMFALP2", "in_game_name": "Falcons Player 2", "role": "player"},
        ],
    },
    {
        "name": "Short-Handed FC",
        "approved": True,
        "members": [
            {"username": f"{DEMO_USER_PREFIX}short_cap", "email": f"{DEMO_USER_PREFIX}short_cap@{DEMO_EMAIL_DOMAIN}", "unique_id": "DMSHRCAP", "in_game_name": "Short-Handed Captain", "role": "captain"},
        ],
    },
]

DEMO_TEAM_NAMES = [spec["name"] for spec in DEMO_TEAM_SPECS]
DEMO_TEAM_NAME_SET = set(DEMO_TEAM_NAMES)
DEMO_TOURNAMENT_NAMES = list(DEMO_TOURNAMENTS.values())
DEMO_TOURNAMENT_NAME_SET = set(DEMO_TOURNAMENT_NAMES)
DEMO_USERNAMES = [
    DEMO_STAFF["username"],
    *[member["username"] for spec in DEMO_TEAM_SPECS for member in spec["members"]],
]
DEMO_USERNAME_SET = set(DEMO_USERNAMES)


def generate_demo_password():
    """Generate a strong password for one local seed-command execution."""
    return secrets.token_urlsafe(32)


def demo_entity_note(label):
    return f"[{DEMO_INTERNAL_TAG}] {label}"


def demo_deadline():
    return timezone.now() + timedelta(days=30)


def demo_start_date():
    return timezone.localdate() + timedelta(days=7)


def demo_end_date():
    return timezone.localdate() + timedelta(days=21)
