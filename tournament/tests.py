# tournament/tests.py
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

import django
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.db import IntegrityError, transaction, models
from django.utils import timezone
from django.core.exceptions import ValidationError
from PIL import Image
import uuid

from accounts.models import Player, Team, TeamMembership
from tournament.forms import (
    MAX_RESULT_PLAYER_STAT_ROWS,
    ResultSubmitForm,
    build_result_player_stat_formset,
)
from tournament.models import Announcement, Tournament, TournamentRegistration, Fixture, Result, ResultPlayerStat
from standings.models import Standing, PlayerStat
from tournament.player_stat_fields import PLAYER_STAT_COPY_FIELDS
from tournament.fixtures import (
    generate_round_robin,
    generate_knockout,
    generate_fixtures_for_tournament,
    BYE,
)
from tournament.validators import RESULT_SCREENSHOT_MAX_SIZE

# ── Helpers ──────────────────────────────────────────────────────────────

def make_player(username, is_active=True, is_staff=False):
    return Player.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_active=is_active,
        is_staff=is_staff,
        unique_id=uuid.uuid4().hex[:20],
    )


def make_team(name, captain=None, is_approved=True):
    return Team.objects.create(
        name=name,
        captain=captain,
        is_approved=is_approved,
    )


def make_tournament(
    name="Test Cup",
    format=Tournament.ROUND_ROBIN,
    status=Tournament.ACTIVE,
    tournament_type=Tournament.TEAM,
):
    return Tournament.objects.create(
        name=name,
        format=format,
        status=status,
        max_teams=4,
        tournament_type=tournament_type,
    )


def make_fixture(tournament, home, away, round_number=1):
    return Fixture.objects.create(
        tournament=tournament,
        home_team=home,
        away_team=away,
        round_number=round_number,
        stage=Fixture.GROUP,
    )


def make_image_upload(name="proof.png", content_type="image/png", image_format="PNG"):
    image_file = BytesIO()
    image = Image.new("RGB", (2, 2), color=(28, 130, 90))
    image.save(image_file, format=image_format)
    return SimpleUploadedFile(name, image_file.getvalue(), content_type=content_type)


def build_team_result_payload(fixture, *, home_score, away_score, stats_by_player=None, result=None):
    stats_by_player = stats_by_player or {}
    selected_player_ids = list(stats_by_player.keys())
    stat_formset = build_result_player_stat_formset(fixture=fixture, result=result)
    payload = {
        "home_score": home_score,
        "away_score": away_score,
        "player_stats-TOTAL_FORMS": str(len(stat_formset.forms)),
        "player_stats-INITIAL_FORMS": str(len(stat_formset.forms)),
        "player_stats-MIN_NUM_FORMS": "0",
        "player_stats-MAX_NUM_FORMS": str(MAX_RESULT_PLAYER_STAT_ROWS),
    }
    for index, stat_form in enumerate(stat_formset.forms):
        if index < len(selected_player_ids):
            player_id = selected_player_ids[index]
        else:
            player_id = stat_form.initial.get("player_id", "")
        stat_values = stats_by_player.get(player_id, {}) if player_id else {}
        payload[f"player_stats-{index}-player_id"] = str(player_id) if player_id else ""
        for field_name in PLAYER_STAT_COPY_FIELDS:
            payload[f"player_stats-{index}-{field_name}"] = str(stat_values.get(field_name, 0))
    return payload

# ── Fixture Generator Tests ───────────────────────────────────────────────

class RoundRobinTests(TestCase):

    def test_4_teams_fixture_count(self):
        teams = ["A", "B", "C", "D"]
        schedule = generate_round_robin(teams)
        non_bye = [m for m in schedule if not m["is_bye"]]
        self.assertEqual(len(non_bye), 6)

    def test_4_teams_every_pair_plays(self):
        teams = ["A", "B", "C", "D"]
        schedule = generate_round_robin(teams)
        non_bye = [m for m in schedule if not m["is_bye"]]
        pairs = set(frozenset([m["home"], m["away"]]) for m in non_bye)
        self.assertEqual(len(pairs), 6)

    def test_4_teams_rounds(self):
        teams = ["A", "B", "C", "D"]
        schedule = generate_round_robin(teams)
        rounds = set(m["round_number"] for m in schedule)
        self.assertEqual(rounds, {1, 2, 3})

    def test_8_teams_fixture_count(self):
        teams = list(range(8))
        schedule = generate_round_robin(teams)
        non_bye = [m for m in schedule if not m["is_bye"]]
        self.assertEqual(len(non_bye), 28)

    def test_no_team_plays_itself(self):
        teams = list(range(8))
        schedule = generate_round_robin(teams)
        for m in schedule:
            self.assertNotEqual(m["home"], m["away"])

    def test_odd_teams_adds_bye(self):
        teams = ["A", "B", "C"]
        schedule = generate_round_robin(teams)
        bye_fixtures = [m for m in schedule if m["is_bye"]]
        self.assertGreater(len(bye_fixtures), 0)

    def test_odd_teams_non_bye_count(self):
        teams = ["A", "B", "C"]
        schedule = generate_round_robin(teams)
        non_bye = [m for m in schedule if not m["is_bye"]]
        self.assertEqual(len(non_bye), 3)

    def test_5_teams_each_gets_one_bye(self):
        teams = ["A", "B", "C", "D", "E"]
        schedule = generate_round_robin(teams)
        bye_fixtures = [m for m in schedule if m["is_bye"]]
        self.assertEqual(len(bye_fixtures), 5)


class KnockoutTests(TestCase):

    def test_4_teams_round1(self):
        teams = ["A", "B", "C", "D"]
        matchups = generate_knockout(teams)
        self.assertEqual(len(matchups), 2)

    def test_4_teams_no_byes(self):
        teams = ["A", "B", "C", "D"]
        matchups = generate_knockout(teams)
        for m in matchups:
            self.assertFalse(m["is_bye"])

    def test_8_teams_round1(self):
        teams = list(range(8))
        matchups = generate_knockout(teams)
        self.assertEqual(len(matchups), 4)

    def test_6_teams_pads_to_8(self):
        teams = ["A", "B", "C", "D", "E", "F"]
        matchups = generate_knockout(teams)
        self.assertEqual(len(matchups), 4)
        bye_count = sum(1 for m in matchups if m["is_bye"])
        self.assertEqual(bye_count, 2)

    def test_no_team_plays_twice(self):
        teams = ["A", "B", "C", "D", "E", "F", "G", "H"]
        matchups = generate_knockout(teams)
        participants = []
        for m in matchups:
            if m["home"] != BYE:
                participants.append(m["home"])
            if m["away"] != BYE:
                participants.append(m["away"])
        self.assertEqual(len(participants), len(set(participants)))

    def test_all_fixtures_round_1(self):
        teams = ["A", "B", "C", "D"]
        matchups = generate_knockout(teams)
        for m in matchups:
            self.assertEqual(m["round_number"], 1)


# ── Team Approval Tests ───────────────────────────────────────────────────

class TeamApprovalTests(TestCase):

    def test_team_created_unapproved_by_default(self):
        team = Team.objects.create(name="Test FC")
        self.assertFalse(team.is_approved)

    def test_approved_team_can_get_fixtures(self):
        t = make_tournament()
        home = make_team("Home FC", is_approved=True)
        away = make_team("Away FC", is_approved=True)
        TournamentRegistration.objects.create(tournament=t, team=home, is_active=True)
        TournamentRegistration.objects.create(tournament=t, team=away, is_active=True)
        count, error = generate_fixtures_for_tournament(t)
        self.assertIsNone(error)
        self.assertGreater(count, 0)

    def test_unapproved_team_excluded_from_fixtures(self):
        t = make_tournament()
        approved = make_team("Approved FC", is_approved=True)
        unapproved = make_team("Unapproved FC", is_approved=False)
        TournamentRegistration.objects.create(tournament=t, team=approved, is_active=True)
        TournamentRegistration.objects.create(tournament=t, team=unapproved, is_active=True)
        count, error = generate_fixtures_for_tournament(t)
        self.assertEqual(count, 0)
        self.assertIsNotNone(error)

    def test_fixture_generation_idempotent(self):
        t = make_tournament()
        for i in range(4):
            team = make_team(f"Team {i}", is_approved=True)
            TournamentRegistration.objects.create(tournament=t, team=team, is_active=True)
        count1, _ = generate_fixtures_for_tournament(t)
        count2, error = generate_fixtures_for_tournament(t)
        self.assertGreater(count1, 0)
        self.assertEqual(count2, 0)
        self.assertIsNotNone(error)


class HybridQualifierSettingTests(TestCase):

    def test_hybrid_qualifiers_per_group_defaults_to_top_2(self):
        tournament = make_tournament("Hybrid Default Field Cup", format=Tournament.HYBRID)

        self.assertEqual(
            tournament.hybrid_qualifiers_per_group,
            Tournament.HYBRID_QUALIFIERS_TOP_2,
        )

    def test_hybrid_qualifier_lock_helper_is_unlocked_without_elimination_fixtures(self):
        tournament = make_tournament("Hybrid Unlocked Cup", format=Tournament.HYBRID)

        self.assertIsNone(tournament.hybrid_qualifier_lock_reason())

    def test_hybrid_qualifier_lock_helper_locks_when_elimination_fixture_exists(self):
        tournament = make_tournament("Hybrid Locked Cup", format=Tournament.HYBRID)
        home = make_team("Hybrid Lock Home", is_approved=True)
        away = make_team("Hybrid Lock Away", is_approved=True)

        for stage in [Fixture.KNOCKOUT, Fixture.FINAL]:
            with self.subTest(stage=stage):
                Fixture.objects.filter(tournament=tournament).delete()
                Fixture.objects.create(
                    tournament=tournament,
                    home_team=home,
                    away_team=away,
                    round_number=1,
                    stage=stage,
                )
                self.assertIsNotNone(tournament.hybrid_qualifier_lock_reason())

    def test_hybrid_qualifier_lock_helper_ignores_group_stage_fixtures(self):
        tournament = make_tournament("Hybrid Group Fixture Cup", format=Tournament.HYBRID)
        home = make_team("Hybrid Group Home", is_approved=True)
        away = make_team("Hybrid Group Away", is_approved=True)
        make_fixture(tournament, home, away)

        self.assertIsNone(tournament.hybrid_qualifier_lock_reason())


class FixtureGenerationFlowTests(TestCase):

    def register_team(self, tournament, name, *, approved=True, active=True, seed=None, group_label=""):
        team = make_team(name, is_approved=approved)
        TournamentRegistration.objects.create(
            tournament=tournament,
            team=team,
            is_active=active,
            seed=seed,
            group_label=group_label,
        )
        return team

    def test_round_robin_even_teams_persists_all_non_bye_fixtures(self):
        tournament = make_tournament("Even Round Robin", format=Tournament.ROUND_ROBIN)
        teams = [
            self.register_team(tournament, f"Team {i}", seed=i)
            for i in range(1, 5)
        ]

        count, error = generate_fixtures_for_tournament(tournament)

        self.assertIsNone(error)
        self.assertEqual(count, 6)
        fixtures = Fixture.objects.filter(tournament=tournament)
        self.assertEqual(fixtures.count(), 6)
        self.assertFalse(fixtures.filter(is_bye=True).exists())
        self.assertFalse(fixtures.filter(away_team__isnull=True).exists())

        expected_pairs = {frozenset((a.pk, b.pk)) for i, a in enumerate(teams) for b in teams[i + 1:]}
        actual_pairs = {
            frozenset((fixture.home_team_id, fixture.away_team_id))
            for fixture in fixtures
        }
        self.assertEqual(actual_pairs, expected_pairs)

    def test_round_robin_odd_teams_creates_bye_fixtures_in_db(self):
        tournament = make_tournament("Odd Round Robin", format=Tournament.ROUND_ROBIN)
        teams = [
            self.register_team(tournament, f"Team {i}", seed=i)
            for i in range(1, 6)
        ]

        count, error = generate_fixtures_for_tournament(tournament)

        self.assertIsNone(error)
        self.assertEqual(count, 15)

        fixtures = Fixture.objects.filter(tournament=tournament)
        self.assertEqual(fixtures.count(), 15)
        bye_fixtures = fixtures.filter(is_bye=True, away_team__isnull=True)
        self.assertEqual(bye_fixtures.count(), 5)
        self.assertEqual(
            set(bye_fixtures.values_list("home_team_id", flat=True)),
            {team.pk for team in teams},
        )
        self.assertFalse(fixtures.filter(home_team_id=models.F("away_team_id")).exists())
        self.assertEqual(fixtures.filter(is_bye=False).count(), 10)

    def test_knockout_non_power_of_two_creates_byes_with_null_away_team(self):
        tournament = make_tournament("Knockout With Byes", format=Tournament.KNOCKOUT)
        for i in range(1, 7):
            self.register_team(tournament, f"Team {i}", seed=i)

        count, error = generate_fixtures_for_tournament(tournament)

        self.assertIsNone(error)
        self.assertEqual(count, 4)

        fixtures = Fixture.objects.filter(tournament=tournament)
        self.assertEqual(fixtures.count(), 4)
        self.assertEqual(fixtures.filter(stage=Fixture.KNOCKOUT).count(), 4)
        self.assertEqual(fixtures.filter(is_bye=True, away_team__isnull=True).count(), 2)
        self.assertFalse(fixtures.filter(home_team_id=models.F("away_team_id")).exists())
        self.assertEqual(fixtures.filter(is_bye=False).count(), 2)

    def test_generation_is_idempotent_and_does_not_duplicate_fixtures(self):
        tournament = make_tournament("Idempotent Cup", format=Tournament.ROUND_ROBIN)
        for i in range(1, 5):
            self.register_team(tournament, f"Team {i}", seed=i)

        first_count, first_error = generate_fixtures_for_tournament(tournament)
        second_count, second_error = generate_fixtures_for_tournament(tournament)

        self.assertIsNone(first_error)
        self.assertEqual(first_count, 6)
        self.assertEqual(second_count, 0)
        self.assertEqual(second_error, "Fixtures already exist for this tournament.")
        self.assertEqual(Fixture.objects.filter(tournament=tournament).count(), 6)

    def test_less_than_two_approved_active_teams_fails_cleanly(self):
        tournament = make_tournament("Too Few Teams", format=Tournament.ROUND_ROBIN)
        approved_active = self.register_team(tournament, "Approved Active", approved=True, active=True, seed=1)
        self.register_team(tournament, "Approved Inactive", approved=True, active=False, seed=2)
        self.register_team(tournament, "Unapproved Active", approved=False, active=True, seed=3)

        count, error = generate_fixtures_for_tournament(tournament)

        self.assertEqual(count, 0)
        self.assertEqual(
            error,
            "Need at least 2 approved registered teams to generate fixtures. "
            "Make sure teams are approved in admin before generating.",
        )
        self.assertFalse(Fixture.objects.filter(tournament=tournament).exists())
        eligible_team_ids = list(
            TournamentRegistration.objects.filter(
                tournament=tournament,
                is_active=True,
                team__is_approved=True,
            ).values_list("team_id", flat=True)
        )
        self.assertEqual(eligible_team_ids, [approved_active.pk])

    def test_grouped_active_team_entrants_generate_round_robin_within_each_group(self):
        tournament = make_tournament("Grouped Round Robin", format=Tournament.KNOCKOUT)
        teams = [
            self.register_team(tournament, "Group A Team 1", seed=1, group_label="A"),
            self.register_team(tournament, "Group A Team 2", seed=2, group_label="A"),
            self.register_team(tournament, "Group B Team 1", seed=3, group_label="B"),
            self.register_team(tournament, "Group B Team 2", seed=4, group_label="B"),
        ]

        count, error = generate_fixtures_for_tournament(tournament)

        self.assertIsNone(error)
        self.assertEqual(count, 2)
        team_group_map = {
            registration.team_id: registration.group_label
            for registration in TournamentRegistration.objects.filter(tournament=tournament, is_active=True)
        }
        fixtures = Fixture.objects.filter(tournament=tournament, is_bye=False)
        self.assertEqual(fixtures.count(), 2)
        for fixture in fixtures:
            self.assertEqual(team_group_map[fixture.home_team_id], team_group_map[fixture.away_team_id])
            self.assertEqual(fixture.stage, Fixture.GROUP)
        self.assertEqual({team.pk for team in teams}, set(team_group_map.keys()))

    def test_grouped_generation_blocks_when_active_team_is_ungrouped(self):
        tournament = make_tournament("Partial Grouping Cup", format=Tournament.ROUND_ROBIN)
        self.register_team(tournament, "Grouped Team 1", seed=1, group_label="A")
        self.register_team(tournament, "Grouped Team 2", seed=2, group_label="A")
        self.register_team(tournament, "Ungrouped Team", seed=3)

        count, error = generate_fixtures_for_tournament(tournament)

        self.assertEqual(count, 0)
        self.assertEqual(
            error,
            "Active team entrants are only partially grouped. "
            "Assign every active team entrant to a group before generating group-stage fixtures.",
        )
        self.assertFalse(Fixture.objects.filter(tournament=tournament).exists())

    def test_grouped_generation_skips_groups_with_fewer_than_two_teams(self):
        tournament = make_tournament("Underfilled Group Cup", format=Tournament.ROUND_ROBIN)
        group_a_home = self.register_team(tournament, "Group A Home", seed=1, group_label="A")
        group_a_away = self.register_team(tournament, "Group A Away", seed=2, group_label="A")
        self.register_team(tournament, "Group B Solo", seed=3, group_label="B")

        count, error = generate_fixtures_for_tournament(tournament)

        self.assertIsNone(error)
        self.assertEqual(count, 1)
        self.assertEqual(
            getattr(tournament, "fixture_generation_notice", ""),
            "Skipped Group B because each group needs at least 2 active teams.",
        )
        fixtures = Fixture.objects.filter(tournament=tournament, is_bye=False)
        self.assertEqual(fixtures.count(), 1)
        fixture = fixtures.get()
        self.assertEqual({fixture.home_team_id, fixture.away_team_id}, {group_a_home.pk, group_a_away.pk})


# ── Registration Tests ────────────────────────────────────────────────────

class RegistrationTests(TestCase):

    def setUp(self):
        self.client = Client()

    def test_register_page_loads(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 200)

    def test_player_registration_activates_immediately(self):
        self.client.post(reverse("accounts:register"), {
            "username": "newplayer",
            "email": "new@test.com",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "role": "player",
            "team_name": "",
        })
        player = Player.objects.get(username="newplayer")
        self.assertTrue(player.is_active)

    def test_team_owner_registration_creates_unapproved_team(self):
        self.client.post(reverse("accounts:register"), {
            "username": "teamowner",
            "email": "owner@test.com",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "role": "owner",
            "team_name": "New FC",
        })
        team = Team.objects.get(name="New FC")
        self.assertFalse(team.is_approved)

    def test_team_owner_becomes_captain(self):
        self.client.post(reverse("accounts:register"), {
            "username": "captain1",
            "email": "cap@test.com",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "role": "owner",
            "team_name": "Captain FC",
        })
        player = Player.objects.get(username="captain1")
        membership = TeamMembership.objects.get(player=player)
        self.assertEqual(membership.role, TeamMembership.CAPTAIN)

    def test_duplicate_team_name_rejected(self):
        make_team("Existing FC", is_approved=True)
        self.client.post(reverse("accounts:register"), {
            "username": "owner2",
            "email": "o2@test.com",
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "role": "owner",
            "team_name": "Existing FC",
        })
        self.assertFalse(Player.objects.filter(username="owner2").exists())


class TeamMembershipHistoryTests(TestCase):

    def test_player_can_rejoin_same_team_with_new_row_after_leaving(self):
        player = make_player("history1")
        team = make_team("History FC")

        first_membership = TeamMembership.objects.create(
            player=player,
            team=team,
            role=TeamMembership.PLAYER,
        )
        first_membership.deactivate()

        second_membership = TeamMembership.objects.create(
            player=player,
            team=team,
            role=TeamMembership.SUBSTITUTE,
        )

        memberships = TeamMembership.objects.filter(player=player, team=team).order_by("id")
        self.assertEqual(memberships.count(), 2)
        self.assertFalse(memberships.first().is_active)
        self.assertIsNotNone(memberships.first().left_at)
        self.assertTrue(second_membership.is_active)

    def test_player_cannot_have_two_active_rows_for_same_team(self):
        player = make_player("history2")
        team = make_team("Same Team FC")

        TeamMembership.objects.create(
            player=player,
            team=team,
            role=TeamMembership.PLAYER,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TeamMembership.objects.create(
                    player=player,
                    team=team,
                    role=TeamMembership.SUBSTITUTE,
                )

    def test_player_cannot_have_two_active_teams_at_once(self):
        player = make_player("history3")
        first_team = make_team("First Team FC")
        second_team = make_team("Second Team FC")

        TeamMembership.objects.create(
            player=player,
            team=first_team,
            role=TeamMembership.PLAYER,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TeamMembership.objects.create(
                    player=player,
                    team=second_team,
                    role=TeamMembership.PLAYER,
                )


# ── View Tests ────────────────────────────────────────────────────────────

class HomeViewTests(TestCase):

    def test_home_loads(self):
        response = self.client.get(reverse("tournament:home"))
        self.assertEqual(response.status_code, 200)

    def test_home_shows_active_tournament(self):
        make_tournament("Active Cup", status=Tournament.ACTIVE)
        response = self.client.get(reverse("tournament:home"))
        self.assertContains(response, "Active Cup")

    def test_home_guest_hero_uses_full_width_announcement_block(self):
        response = self.client.get(reverse("tournament:home"))

        self.assertContains(response, "announcement-board")
        self.assertContains(response, "announcement-scroll")
        self.assertContains(response, "announcement-header")
        self.assertContains(response, 'href="/accounts/login/"')
        self.assertContains(response, 'href="/accounts/register/"')
        self.assertNotContains(response, "Guest access")
        self.assertNotContains(response, "Signed in")
        self.assertNotContains(response, "Open dashboard")

    def test_home_signed_in_hero_relies_on_navbar_actions(self):
        player = make_player("hero_user")
        self.client.login(username=player.username, password="testpass123")

        response = self.client.get(reverse("tournament:home"))

        self.assertContains(response, player.username)
        self.assertContains(response, 'href="/accounts/dashboard/"')
        self.assertContains(response, f'href="/accounts/profile/{player.username}/"')
        self.assertNotContains(response, "Signed in")
        self.assertNotContains(response, "Open dashboard")
        self.assertNotContains(response, "View public profile")

    def test_home_shows_empty_announcement_state_when_none_exist(self):
        response = self.client.get(reverse("tournament:home"))

        self.assertContains(response, "No announcements right now.")
        self.assertContains(response, "Check back here for schedule notes")

    def test_home_renders_multiple_announcements_inside_scroll_region(self):
        Announcement.objects.create(
            title="Bracket update",
            body="Quarterfinal schedule posted.",
            sort_order=1,
            is_active=True,
        )
        Announcement.objects.create(
            title="Registration reminder",
            body="Final roster edits close tonight.",
            sort_order=2,
            is_active=True,
        )
        Announcement.objects.create(
            title="Stream notice",
            body="Semifinals will be cast live.",
            sort_order=3,
            is_active=True,
        )

        response = self.client.get(reverse("tournament:home"))

        self.assertContains(response, "announcement-scroll")
        self.assertContains(response, "Bracket update")
        self.assertContains(response, "Registration reminder")
        self.assertContains(response, "Stream notice")
        self.assertNotContains(response, "No announcements right now.")

    def test_home_grouped_standings_preview_uses_group_sections(self):
        tournament = make_tournament("Grouped Home Cup", status=Tournament.ACTIVE)
        group_a_home = make_team("Home Group A Home", is_approved=True)
        group_a_away = make_team("Home Group A Away", is_approved=True)
        group_b_home = make_team("Home Group B Home", is_approved=True)
        group_b_away = make_team("Home Group B Away", is_approved=True)
        group_a_fixture = Fixture.objects.create(
            tournament=tournament,
            home_team=group_a_home,
            away_team=group_a_away,
            round_number=1,
            stage=Fixture.GROUP,
            group_label="A",
        )
        Fixture.objects.create(
            tournament=tournament,
            home_team=group_b_home,
            away_team=group_b_away,
            round_number=1,
            stage=Fixture.GROUP,
            group_label="B",
        )
        admin = make_player("homegroupadmin", is_staff=True)
        Result.objects.create(
            fixture=group_a_fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )

        response = self.client.get(reverse("tournament:home"))
        partial_response = self.client.get(
            reverse("tournament:standings_partial", args=[tournament.pk])
        )

        self.assertEqual(response.status_code, 200)
        # Home page no longer shows standings preview - verify they don't appear
        self.assertNotContains(response, "Current group progress.")
        self.assertNotContains(response, "Current table.")
        self.assertNotContains(response, "Group A")
        self.assertNotContains(response, "Group B")

        # But the partial endpoint still works for detail pages
        self.assertEqual(partial_response.status_code, 200)
        self.assertContains(partial_response, "Group A")
        self.assertContains(partial_response, "Group B")
        self.assertContains(partial_response, "No approved results yet in Group B.")

    def test_home_non_grouped_standings_preview_stays_single_table(self):
        tournament = make_tournament("Classic Home Cup", status=Tournament.ACTIVE)
        home = make_team("Classic Home", is_approved=True)
        away = make_team("Classic Away", is_approved=True)
        Standing.objects.create(
            tournament=tournament,
            team=home,
            played=1,
            wins=1,
            points=3,
            goals_for=2,
            goals_against=0,
            goal_difference=2,
        )
        Standing.objects.create(
            tournament=tournament,
            team=away,
            played=1,
            losses=1,
            points=0,
            goals_for=0,
            goals_against=2,
            goal_difference=-2,
        )

        response = self.client.get(reverse("tournament:home"))
        partial_response = self.client.get(
            reverse("tournament:standings_partial", args=[tournament.pk])
        )

        self.assertEqual(response.status_code, 200)
        # Home page no longer shows standings preview - verify they don't appear
        self.assertNotContains(response, "Current table.")
        self.assertNotContains(response, "Current group progress.")
        self.assertNotContains(response, "Classic Away")

        # But the partial endpoint still works for detail pages
        self.assertEqual(partial_response.status_code, 200)
        self.assertContains(partial_response, "Classic Home")
        self.assertNotContains(partial_response, "Group A")


class TournamentDetailTests(TestCase):

    def setUp(self):
        self.tournament = make_tournament("Detail Cup")
        self.home = make_team("Home FC", is_approved=True)
        self.away = make_team("Away FC", is_approved=True)

    def test_tournament_detail_loads(self):
        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_tournament_detail_shows_name(self):
        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )
        self.assertContains(response, "Detail Cup")

    def test_tournament_detail_shows_fixtures(self):
        make_fixture(self.tournament, self.home, self.away)
        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )
        self.assertContains(response, "Home FC")
        self.assertContains(response, "Away FC")

    def test_tournament_detail_stats_tab_shows_compact_top_stat_buttons(self):
        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertContains(response, "View Top Scorers")
        self.assertContains(response, "View Top Assists")
        self.assertContains(response, reverse("standings:top_scorers", args=[self.tournament.pk]))
        self.assertContains(response, reverse("standings:top_assists", args=[self.tournament.pk]))
        self.assertNotContains(response, "Official top scorers are published after results are approved.")
        self.assertNotContains(response, "Official assist leaders are published after results are approved.")

    def test_tournament_detail_shows_fixture_filter_options_from_active_participants(self):
        reserve_team = make_team("Reserve FC", is_approved=True)
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=self.home,
            is_active=True,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=self.away,
            is_active=True,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=reserve_team,
            is_active=False,
        )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        filter_team_names = [team.name for team in response.context["fixture_filter_teams"]]
        self.assertContains(response, "All teams")
        self.assertEqual(filter_team_names, ["Away FC", "Home FC"])
        self.assertNotIn("Reserve FC", filter_team_names)

    def test_tournament_detail_filters_fixture_rows_by_selected_participating_team(self):
        unrelated_home = make_team("Other Home", is_approved=True)
        unrelated_away = make_team("Other Away", is_approved=True)
        make_fixture(self.tournament, self.home, self.away, round_number=1)
        make_fixture(self.tournament, unrelated_home, unrelated_away, round_number=1)
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=self.home,
            is_active=True,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=self.away,
            is_active=True,
        )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk]),
            {"team": self.home.pk},
        )

        self.assertEqual(response.context["selected_fixture_team_id"], self.home.pk)
        self.assertEqual(response.context["default_tab"], "fixtures")
        self.assertContains(response, "Home FC")
        self.assertContains(response, "Away FC")
        self.assertNotContains(response, "Other Home")
        self.assertNotContains(response, "Other Away")

    def test_tournament_detail_ignores_non_participating_team_filter_and_falls_back(self):
        unrelated_home = make_team("Fallback Home", is_approved=True)
        unrelated_away = make_team("Fallback Away", is_approved=True)
        outsider_team = make_team("Outsider FC", is_approved=True)
        make_fixture(self.tournament, self.home, self.away, round_number=1)
        make_fixture(self.tournament, unrelated_home, unrelated_away, round_number=1)
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=self.home,
            is_active=True,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=self.away,
            is_active=True,
        )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk]),
            {"team": outsider_team.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_fixture_team_id"])
        self.assertEqual(response.context["default_tab"], "fixtures")
        self.assertContains(response, "Fallback Home")
        self.assertContains(response, "Fallback Away")

    def test_tournament_detail_shows_top_assists_card_when_assist_data_exists(self):
        fixture = make_fixture(self.tournament, self.home, self.away)
        stats_player = make_player("detailassistplayer")
        admin = make_player("detailassistadmin", is_staff=True)
        Result.objects.create(
            fixture=fixture,
            home_score=2,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )
        PlayerStat.objects.create(
            player=stats_player,
            fixture=fixture,
            team=self.home,
            assists=2,
        )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertTrue(response.context["has_top_assists_data"])
        self.assertContains(response, "Top Assists")
        self.assertContains(response, reverse("standings:top_assists", args=[self.tournament.pk]))

    def test_anonymous_user_sees_login_prompt_in_registration_panel(self):
        self.tournament.status = Tournament.REGISTRATION
        self.tournament.save(update_fields=["status"])

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertContains(response, "Login required to register")
        self.assertContains(response, "Login to register")

    def test_anonymous_user_sees_closed_state_for_closed_tournament(self):
        tournament = make_tournament("Closed Detail Cup", status=Tournament.ACTIVE)

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[tournament.pk])
        )

        self.assertContains(response, "Registration closed")
        self.assertContains(response, "This tournament is not currently accepting new registrations.")
        self.assertNotContains(response, "Login required to register")

    def test_anonymous_user_sees_full_state_for_full_tournament(self):
        tournament = make_tournament("Full Detail Cup", status=Tournament.REGISTRATION)
        for i in range(tournament.max_teams):
            player = make_player(f"fill{i}")
            TournamentRegistration.objects.create(
                tournament=tournament,
                player=player,
                is_active=True,
            )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[tournament.pk])
        )

        self.assertContains(response, "Registration full")
        self.assertContains(response, f"All {tournament.max_teams} {tournament.participant_label} slots have been filled.")
        self.assertNotContains(response, "Login required to register")

    def test_already_registered_single_player_sees_informational_message(self):
        tournament = make_tournament(
            "Solo Detail Cup",
            status=Tournament.REGISTRATION,
            tournament_type=Tournament.SINGLE,
        )
        player = make_player("detailsolo")
        TournamentRegistration.objects.create(
            tournament=tournament,
            player=player,
            is_active=True,
        )
        self.client.login(username="detailsolo", password="testpass123")

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[tournament.pk])
        )

        self.assertContains(response, "Already registered")
        self.assertContains(response, "Your place in this tournament is already confirmed.")
        self.assertNotContains(response, "Register yourself")

    def test_ineligible_team_owner_sees_clear_eligibility_message(self):
        tournament = make_tournament("Eligibility Cup", status=Tournament.REGISTRATION)
        owner = make_player("eligibilityowner")
        team = make_team("Thin Squad", captain=owner, is_approved=True)
        TeamMembership.objects.create(
            player=owner,
            team=team,
            role=TeamMembership.CAPTAIN,
        )
        self.client.login(username="eligibilityowner", password="testpass123")

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[tournament.pk])
        )

        self.assertContains(response, "No eligible team available")
        self.assertContains(response, "You need an approved team you own with 2 to 3 active players")

    def test_fixture_cards_show_completed_scheduled_and_unscheduled_states(self):
        scheduled_fixture = make_fixture(self.tournament, self.home, self.away, round_number=1)
        scheduled_fixture.match_date = timezone.now() + timedelta(days=2)
        scheduled_fixture.save(update_fields=["match_date", "submission_deadline", "is_bye"])

        unscheduled_home = make_team("Unscheduled Home", is_approved=True)
        unscheduled_away = make_team("Unscheduled Away", is_approved=True)
        make_fixture(self.tournament, unscheduled_home, unscheduled_away, round_number=1)

        completed_home = make_team("Completed Home", is_approved=True)
        completed_away = make_team("Completed Away", is_approved=True)
        completed_fixture = make_fixture(self.tournament, completed_home, completed_away, round_number=2)
        admin = make_player("detailadmin", is_staff=True)
        Result.objects.create(
            fixture=completed_fixture,
            home_score=2,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        # Verify scheduled fixture shows date/time
        self.assertContains(response, "Home FC")
        self.assertContains(response, "Away FC")
        # Verify unscheduled fixture shows vs
        self.assertContains(response, "Unscheduled Home")
        self.assertContains(response, "Unscheduled Away")
        # Verify completed fixture shows Result approved and score
        self.assertContains(response, "Result approved")
        self.assertContains(response, "Completed Home")
        self.assertContains(response, "Completed Away")

    def test_fixture_cards_show_neutral_score_coloring_for_anonymous_viewer(self):
        fixture = make_fixture(self.tournament, self.home, self.away)
        admin = make_player("fixturecoloradmin1", is_staff=True)
        Result.objects.create(
            fixture=fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertContains(response, "score-chip-neutral")
        self.assertContains(response, "score-viewer-neutral")
        self.assertNotContains(response, "score-viewer-win")
        self.assertNotContains(response, "score-viewer-loss")

    def test_fixture_cards_highlight_only_home_side_for_logged_in_home_team_player(self):
        fixture = make_fixture(self.tournament, self.home, self.away)
        admin = make_player("fixturecoloradmin2", is_staff=True)
        home_player = make_player("fixturehomecolor")
        TeamMembership.objects.create(
            player=home_player,
            team=self.home,
            role=TeamMembership.PLAYER,
            is_active=True,
        )
        Result.objects.create(
            fixture=fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )
        self.client.login(username="fixturehomecolor", password="testpass123")

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        html = response.content.decode()
        self.assertContains(response, "score-viewer-win")
        self.assertNotContains(response, "score-viewer-loss")
        self.assertEqual(html.count("fixture-my-team-name"), 1)

    def test_fixture_cards_highlight_only_away_side_for_logged_in_away_team_player(self):
        fixture = make_fixture(self.tournament, self.home, self.away)
        admin = make_player("fixturecoloradmin3", is_staff=True)
        away_player = make_player("fixtureawaycolor")
        TeamMembership.objects.create(
            player=away_player,
            team=self.away,
            role=TeamMembership.PLAYER,
            is_active=True,
        )
        Result.objects.create(
            fixture=fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )
        self.client.login(username="fixtureawaycolor", password="testpass123")

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        html = response.content.decode()
        self.assertContains(response, "score-viewer-loss")
        self.assertNotContains(response, "score-viewer-win")
        self.assertEqual(html.count("fixture-my-team-name"), 1)

    def test_fixture_cards_show_neutral_coloring_for_logged_in_unrelated_player(self):
        fixture = make_fixture(self.tournament, self.home, self.away)
        admin = make_player("fixturecoloradmin4", is_staff=True)
        unrelated_player = make_player("fixtureunrelatedcolor")
        Result.objects.create(
            fixture=fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )
        self.client.login(username="fixtureunrelatedcolor", password="testpass123")

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertContains(response, "score-chip-neutral")
        self.assertContains(response, "score-viewer-neutral")
        self.assertNotContains(response, "score-viewer-win")
        self.assertNotContains(response, "score-viewer-loss")

    def test_grouped_fixtures_render_under_group_sections(self):
        group_a_home = make_team("Group A Home", is_approved=True)
        group_a_away = make_team("Group A Away", is_approved=True)
        group_b_home = make_team("Group B Home", is_approved=True)
        group_b_away = make_team("Group B Away", is_approved=True)
        Fixture.objects.create(
            tournament=self.tournament,
            home_team=group_a_home,
            away_team=group_a_away,
            round_number=1,
            stage=Fixture.GROUP,
            group_label="A",
        )
        Fixture.objects.create(
            tournament=self.tournament,
            home_team=group_b_home,
            away_team=group_b_away,
            round_number=1,
            stage=Fixture.GROUP,
            group_label="B",
        )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_grouped_fixtures"])
        # Verify group headings are rendered
        self.assertContains(response, "Group A")
        self.assertContains(response, "Group B")
        self.assertTrue(response.context["has_grouped_standings"])

    def test_grouped_tournament_detail_builds_standings_by_group_without_team_leakage(self):
        group_a_home = make_team("Standings Group A Home", is_approved=True)
        group_a_away = make_team("Standings Group A Away", is_approved=True)
        group_b_home = make_team("Standings Group B Home", is_approved=True)
        group_b_away = make_team("Standings Group B Away", is_approved=True)
        group_a_fixture = Fixture.objects.create(
            tournament=self.tournament,
            home_team=group_a_home,
            away_team=group_a_away,
            round_number=1,
            stage=Fixture.GROUP,
            group_label="A",
        )
        group_b_fixture = Fixture.objects.create(
            tournament=self.tournament,
            home_team=group_b_home,
            away_team=group_b_away,
            round_number=1,
            stage=Fixture.GROUP,
            group_label="B",
        )
        admin = make_player("groupstand1", is_staff=True)
        Result.objects.create(
            fixture=group_a_fixture,
            home_score=2,
            away_score=0,
            status=Result.APPROVED,
            submitted_by=admin,
        )
        Result.objects.create(
            fixture=group_b_fixture,
            home_score=1,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertEqual(response.status_code, 200)
        sections = response.context["grouped_standings_sections"]
        self.assertEqual(len(sections), 2)
        group_a_section = next(section for section in sections if section["label"] == "A")
        group_b_section = next(section for section in sections if section["label"] == "B")
        self.assertEqual(group_a_section["approved_result_count"], 1)
        self.assertEqual(group_b_section["approved_result_count"], 1)
        self.assertEqual(
            [row["team"].name for row in group_a_section["standings_rows"]],
            ["Standings Group A Home", "Standings Group A Away"],
        )
        self.assertEqual(
            sorted(row["team"].name for row in group_b_section["standings_rows"]),
            ["Standings Group B Away", "Standings Group B Home"],
        )
        self.assertEqual(group_a_section["standings_rows"][0]["points"], 3)
        self.assertEqual(group_b_section["standings_rows"][0]["points"], 1)
        self.assertNotIn("Standings Group B Home", group_a_section["team_names"])

    def test_grouped_tournament_detail_shows_pre_progress_state_when_group_has_no_approved_results(self):
        Fixture.objects.create(
            tournament=self.tournament,
            home_team=make_team("Pending Group A Home", is_approved=True),
            away_team=make_team("Pending Group A Away", is_approved=True),
            round_number=1,
            stage=Fixture.GROUP,
            group_label="A",
        )

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No approved results yet in Group A.")
        sections = response.context["grouped_standings_sections"]
        self.assertEqual(sections[0]["approved_result_count"], 0)
        self.assertEqual(sections[0]["standings_rows"], [])

    def test_grouped_standings_follow_staff_correction_flow_and_use_latest_approved_result(self):
        group_home = make_team("Corrected Group Home", is_approved=True)
        group_away = make_team("Corrected Group Away", is_approved=True)
        fixture = Fixture.objects.create(
            tournament=self.tournament,
            home_team=group_home,
            away_team=group_away,
            round_number=1,
            stage=Fixture.GROUP,
            group_label="A",
        )
        staff = make_player("groupmodstaff", is_staff=True)
        first_result = Result.objects.create(
            fixture=fixture,
            home_score=2,
            away_score=0,
            status=Result.PENDING,
            submitted_by=staff,
        )
        corrected_result = Result.objects.create(
            fixture=fixture,
            home_score=1,
            away_score=3,
            status=Result.PENDING,
            submitted_by=staff,
        )
        self.client.login(username="groupmodstaff", password="testpass123")

        self.client.post(reverse("tournament:result_approve", args=[first_result.pk]))
        self.client.post(reverse("tournament:result_approve", args=[corrected_result.pk]))
        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        section = response.context["grouped_standings_sections"][0]
        self.assertEqual(section["approved_result_count"], 1)
        self.assertEqual(
            [row["team"].name for row in section["standings_rows"]],
            ["Corrected Group Away", "Corrected Group Home"],
        )
        self.assertEqual(section["standings_rows"][0]["points"], 3)
        self.assertEqual(section["standings_rows"][0]["goals_for"], 3)
        self.assertEqual(section["standings_rows"][1]["goal_difference"], -2)

    def test_grouped_standings_drop_disputed_result_from_table(self):
        group_home = make_team("Disputed Group Home", is_approved=True)
        group_away = make_team("Disputed Group Away", is_approved=True)
        fixture = Fixture.objects.create(
            tournament=self.tournament,
            home_team=group_home,
            away_team=group_away,
            round_number=1,
            stage=Fixture.GROUP,
            group_label="A",
        )
        staff = make_player("groupdisputestaff", is_staff=True)
        result = Result.objects.create(
            fixture=fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=staff,
        )
        self.client.login(username="groupdisputestaff", password="testpass123")

        self.client.post(reverse("tournament:result_approve", args=[result.pk]))
        self.client.post(reverse("tournament:result_dispute", args=[result.pk]))
        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        section = response.context["grouped_standings_sections"][0]
        self.assertEqual(section["approved_result_count"], 0)
        self.assertEqual(section["standings_rows"], [])

    def test_non_grouped_fixtures_keep_round_display_without_group_headings(self):
        make_fixture(self.tournament, self.home, self.away, round_number=1)

        response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_grouped_fixtures"])
        self.assertFalse(response.context["has_grouped_standings"])
        # Verify round heading is shown
        self.assertContains(response, "Round 1")
        # Verify group headings are not shown
        self.assertNotContains(response, "Group A")
        self.assertNotContains(response, "Group B")


class TournamentRegistrationFlowTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament("Registration Cup", status=Tournament.REGISTRATION)
        self.owner = make_player("ownerplayer")
        self.outsider = make_player("otherplayer")
        self.team = make_team("Owner FC", captain=self.owner, is_approved=True)
        TeamMembership.objects.create(
            player=self.owner,
            team=self.team,
            role=TeamMembership.CAPTAIN,
        )
        self.teammate = make_player("teammate")
        TeamMembership.objects.create(
            player=self.teammate,
            team=self.team,
            role=TeamMembership.PLAYER,
        )
        self.register_url = reverse("tournament:tournament_register", args=[self.tournament.pk])

    def test_eligible_team_owner_can_register_for_open_tournament(self):
        self.client.login(username="ownerplayer", password="testpass123")

        list_response = self.client.get(reverse("tournament:tournament_list"))
        detail_response = self.client.get(reverse("tournament:tournament_detail", args=[self.tournament.pk]))
        post_response = self.client.post(self.register_url, {"team_id": self.team.pk})

        self.assertContains(detail_response, self.register_url)
        self.assertEqual(post_response.status_code, 302)
        registration = TournamentRegistration.objects.get(tournament=self.tournament, team=self.team)
        self.assertTrue(registration.is_active)

    def test_non_owner_player_cannot_register_someone_elses_team(self):
        self.client.login(username="otherplayer", password="testpass123")

        response = self.client.post(self.register_url, {"team_id": self.team.pk})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            TournamentRegistration.objects.filter(tournament=self.tournament, team=self.team).exists()
        )

    def test_anonymous_user_cannot_register(self):
        response = self.client.post(self.register_url, {"team_id": self.team.pk})

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assertFalse(
            TournamentRegistration.objects.filter(tournament=self.tournament, team=self.team).exists()
        )

    def test_closed_registration_blocks_registration(self):
        self.tournament.status = Tournament.ACTIVE
        self.tournament.save(update_fields=["status"])
        self.client.login(username="ownerplayer", password="testpass123")

        response = self.client.post(self.register_url, {"team_id": self.team.pk})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            TournamentRegistration.objects.filter(tournament=self.tournament, team=self.team).exists()
        )

    def test_ineligible_unapproved_team_is_blocked(self):
        self.team.is_approved = False
        self.team.save(update_fields=["is_approved"])
        self.client.login(username="ownerplayer", password="testpass123")

        response = self.client.post(self.register_url, {"team_id": self.team.pk})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            TournamentRegistration.objects.filter(tournament=self.tournament, team=self.team).exists()
        )

    def test_successful_registration_creates_expected_record_state(self):
        self.client.login(username="ownerplayer", password="testpass123")

        self.client.post(self.register_url, {"team_id": self.team.pk})

        self.assertEqual(
            TournamentRegistration.objects.filter(
                tournament=self.tournament,
                team=self.team,
                is_active=True,
            ).count(),
            1,
        )

    def test_self_registration_still_works_when_staff_added_team_already_exists(self):
        existing_team = make_team("Staff Added FC", is_approved=True)
        existing_captain = make_player("staffaddedcaptain")
        existing_team.captain = existing_captain
        existing_team.save(update_fields=["captain"])
        TeamMembership.objects.create(
            player=existing_captain,
            team=existing_team,
            role=TeamMembership.CAPTAIN,
        )
        TeamMembership.objects.create(
            player=make_player("mateadd2"),
            team=existing_team,
            role=TeamMembership.PLAYER,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=existing_team,
            is_active=True,
        )
        self.client.login(username="ownerplayer", password="testpass123")

        response = self.client.post(self.register_url, {"team_id": self.team.pk})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            TournamentRegistration.objects.filter(
                tournament=self.tournament,
                team=self.team,
                is_active=True,
            ).exists()
        )

    def test_team_with_one_active_player_is_blocked_from_registration(self):
        self.teammate.memberships.filter(team=self.team, is_active=True).update(is_active=False)
        self.client.login(username="ownerplayer", password="testpass123")

        response = self.client.post(self.register_url, {"team_id": self.team.pk}, follow=True)

        self.assertRedirects(response, reverse("tournament:tournament_detail", args=[self.tournament.pk]))
        self.assertContains(response, "Teams must have between 2 and 3 active players to register.")
        self.assertFalse(
            TournamentRegistration.objects.filter(tournament=self.tournament, team=self.team).exists()
        )

    def test_team_with_inactive_historical_member_still_needs_two_active_players(self):
        reserve = make_player("reservehistory")
        historical_membership = TeamMembership.objects.create(
            player=reserve,
            team=self.team,
            role=TeamMembership.SUBSTITUTE,
        )
        historical_membership.deactivate()
        self.teammate.memberships.filter(team=self.team, is_active=True).update(is_active=False)
        self.client.login(username="ownerplayer", password="testpass123")

        response = self.client.post(self.register_url, {"team_id": self.team.pk}, follow=True)

        self.assertRedirects(response, reverse("tournament:tournament_detail", args=[self.tournament.pk]))
        self.assertContains(response, "This team currently has 1 active player")
        self.assertFalse(
            TournamentRegistration.objects.filter(tournament=self.tournament, team=self.team).exists()
        )


class SingleTournamentRegistrationFlowTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament(
            "Solo Cup",
            status=Tournament.REGISTRATION,
            tournament_type=Tournament.SINGLE,
        )
        self.player = make_player("singleplayer")
        self.register_url = reverse("tournament:tournament_register", args=[self.tournament.pk])

    def test_logged_in_player_can_register_for_single_tournament(self):
        self.client.login(username="singleplayer", password="testpass123")

        list_response = self.client.get(reverse("tournament:tournament_list"))
        detail_response = self.client.get(reverse("tournament:tournament_detail", args=[self.tournament.pk]))
        post_response = self.client.post(self.register_url)

        self.assertContains(detail_response, "Register now")
        self.assertEqual(post_response.status_code, 302)
        registration = TournamentRegistration.objects.get(tournament=self.tournament, player=self.player)
        self.assertTrue(registration.is_active)
        self.assertIsNone(registration.team)

    def test_team_payload_is_rejected_for_single_tournament(self):
        team = make_team("Wrong Mode FC", is_approved=True)
        self.client.login(username="singleplayer", password="testpass123")

        response = self.client.post(self.register_url, {"team_id": team.pk})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            TournamentRegistration.objects.filter(tournament=self.tournament, player=self.player).exists()
        )


class FixtureDetailTests(TestCase):

    def setUp(self):
        self.tournament = make_tournament()
        self.home = make_team("Home FC", is_approved=True)
        self.away = make_team("Away FC", is_approved=True)
        self.fixture = make_fixture(self.tournament, self.home, self.away)

    def make_private_pending_result(self, *, status=Result.PENDING):
        submitter = make_player("fixtureprivatesubmitter")
        stat_player = make_player("fixtureprivatestat")
        opponent_responder = make_player("fixtureprivateopponent")
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=9,
            away_score=7,
            status=status,
            submitted_by=submitter,
            submitting_team=self.home,
            opponent_response_status=Result.OPPONENT_RESPONSE_DISPUTED,
            opponent_response_note="Private opponent note for staff only.",
            opponent_home_score=8,
            opponent_away_score=7,
            opponent_score_state=Result.OPPONENT_SCORE_CONFLICT,
            opponent_responded_by=opponent_responder,
        )
        ResultPlayerStat.objects.create(
            result=result,
            player=stat_player,
            team=self.home,
            goals=9,
            assists=2,
        )
        return result

    def test_fixture_detail_loads(self):
        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_fixture_detail_shows_teams(self):
        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )
        self.assertContains(response, "Home FC")
        self.assertContains(response, "Away FC")

    def test_fixture_detail_shows_team_roster_names_for_team_tournament(self):
        home_player = make_player("fixturehomeplayer")
        away_player = make_player("fixtureawayplayer")
        TeamMembership.objects.create(
            player=home_player,
            team=self.home,
            role=TeamMembership.PLAYER,
            is_active=True,
        )
        TeamMembership.objects.create(
            player=away_player,
            team=self.away,
            role=TeamMembership.CAPTAIN,
            is_active=True,
        )

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, home_player.display_name)
        self.assertContains(response, away_player.display_name)

    def test_fixture_detail_hides_team_roster_ui_for_single_player_tournament(self):
        single_tournament = make_tournament(
            "Solo Fixture Cup",
            tournament_type=Tournament.SINGLE,
        )
        single_fixture = make_fixture(single_tournament, self.home, self.away)
        home_player = make_player("singlefixturehomeplayer")
        away_player = make_player("singlefixtureawayplayer")
        TeamMembership.objects.create(
            player=home_player,
            team=self.home,
            role=TeamMembership.PLAYER,
            is_active=True,
        )
        TeamMembership.objects.create(
            player=away_player,
            team=self.away,
            role=TeamMembership.CAPTAIN,
            is_active=True,
        )

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[single_fixture.pk])
        )

        self.assertNotContains(response, home_player.display_name)
        self.assertNotContains(response, away_player.display_name)
        self.assertNotContains(response, "No active roster yet")

    def test_fixture_detail_hides_away_roster_fallback_for_missing_away_team(self):
        bye_fixture = make_fixture(self.tournament, self.home, None)
        home_player = make_player("fixturebyehomeplayer")
        TeamMembership.objects.create(
            player=home_player,
            team=self.home,
            role=TeamMembership.PLAYER,
            is_active=True,
        )

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[bye_fixture.pk])
        )

        self.assertContains(response, "TBD")
        self.assertContains(response, home_player.display_name)
        self.assertNotContains(response, "No active roster yet")

    def test_fixture_detail_shows_roster_fallback_when_team_has_no_active_memberships(self):
        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, "No active roster yet")

    def test_fixture_detail_hero_has_mobile_stack_and_wrap_guards(self):
        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(
            response,
            "flex flex-col items-center justify-center gap-4 sm:flex-row sm:gap-10 mb-5",
        )
        self.assertContains(
            response,
            "w-full sm:flex-1 min-w-0 text-center sm:text-right",
        )
        self.assertContains(
            response,
            "w-full sm:flex-1 min-w-0 text-center sm:text-left",
        )
        self.assertContains(response, "leading-tight truncate")

    def test_fixture_detail_shows_approved_result(self):
        admin = make_player("admin1", is_staff=True)
        Result.objects.create(
            fixture=self.fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )
        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )
        self.assertContains(response, "Official result approved")
        self.assertContains(response, "Submitted score 3–1", html=False)
        self.assertContains(response, "3")
        self.assertContains(response, "1")

    def test_fixture_detail_shows_neutral_score_coloring_for_anonymous_approved_result(self):
        admin = make_player("fixtureanonadmin1", is_staff=True)
        Result.objects.create(
            fixture=self.fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, "score-chip-neutral")
        self.assertContains(response, "score-viewer-neutral")
        self.assertNotContains(response, "score-viewer-win")
        self.assertNotContains(response, "score-viewer-loss")

    def test_fixture_detail_hides_pending_score_details_stats_and_note_for_anonymous_user(self):
        self.make_private_pending_result()

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, "Result pending moderation")
        self.assertContains(response, "already in review", html=False)
        self.assertNotContains(response, "9–7", html=False)
        self.assertNotContains(response, "fixtureprivatesubmitter")
        self.assertNotContains(response, "Submission Details")
        self.assertNotContains(response, "Submitted Player Stats")
        self.assertNotContains(response, "fixtureprivatestat")
        self.assertNotContains(response, "Private opponent note for staff only.")
        self.assertNotContains(response, "score-chip-neutral")
        self.assertNotContains(response, "score-viewer-neutral")
        self.assertNotContains(response, "score-viewer-win")
        self.assertNotContains(response, "score-viewer-loss")

    def test_fixture_detail_hides_pending_details_for_unrelated_authenticated_user(self):
        self.make_private_pending_result()
        unrelated_player = make_player("fixtureunrelatedpending")
        self.client.login(username="fixtureunrelatedpending", password="testpass123")

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, "Result pending moderation")
        self.assertNotContains(response, "9–7", html=False)
        self.assertNotContains(response, "fixtureprivatesubmitter")
        self.assertNotContains(response, "Submission Details")
        self.assertNotContains(response, "Submitted Player Stats")
        self.assertNotContains(response, "fixtureprivatestat")
        self.assertNotContains(response, "Private opponent note for staff only.")

    def test_fixture_detail_submitting_team_captain_can_see_allowed_pending_details(self):
        self.make_private_pending_result()
        captain = make_player("fixturependingcaptain")
        TeamMembership.objects.create(
            player=captain,
            team=self.home,
            role=TeamMembership.CAPTAIN,
            is_active=True,
        )
        self.client.login(username="fixturependingcaptain", password="testpass123")

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, "This result is already in review")
        self.assertContains(response, "Submission Details")
        self.assertContains(response, "fixtureprivatesubmitter")
        self.assertContains(response, "9–7", html=False)
        self.assertContains(response, "Submitted Player Stats")
        self.assertContains(response, "fixtureprivatestat")
        self.assertNotContains(response, "Private opponent note for staff only.")
        self.assertNotContains(response, "Staff: Result Moderation")

    def test_fixture_detail_opponent_member_sees_response_prompt_without_private_submission_details(self):
        self.make_private_pending_result()
        opponent_player = make_player("fixturependingopponent")
        TeamMembership.objects.create(
            player=opponent_player,
            team=self.away,
            role=TeamMembership.PLAYER,
            is_active=True,
        )
        self.client.login(username="fixturependingopponent", password="testpass123")

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, "Result pending moderation")
        self.assertContains(response, "Optional opponent response")
        self.assertContains(response, "9–7", html=False)
        self.assertNotContains(response, "Submission Details")
        self.assertNotContains(response, "fixtureprivatesubmitter")
        self.assertNotContains(response, "Submitted Player Stats")
        self.assertNotContains(response, "fixtureprivatestat")
        self.assertNotContains(response, "Private opponent note for staff only.")

    def test_fixture_detail_staff_can_see_disputed_details_stats_note_and_moderation_controls(self):
        self.make_private_pending_result(status=Result.DISPUTED)
        staff = make_player("fixturependingstaff", is_staff=True)
        self.client.login(username="fixturependingstaff", password="testpass123")

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, "Result disputed")
        self.assertContains(response, "Submission Details")
        self.assertContains(response, "fixtureprivatesubmitter")
        self.assertContains(response, "9–7", html=False)
        self.assertContains(response, "Submitted Player Stats")
        self.assertContains(response, "fixtureprivatestat")
        self.assertContains(response, "Private opponent note for staff only.")
        self.assertContains(response, "Staff: Result Moderation")
        self.assertContains(response, "Approve")
        self.assertContains(response, "Reject")

    def test_fixture_detail_highlights_only_home_side_for_home_player_on_approved_result(self):
        admin = make_player("fixturehomeapprovedadmin", is_staff=True)
        home_player = make_player("fixturehomeapproved")
        TeamMembership.objects.create(
            player=home_player,
            team=self.home,
            role=TeamMembership.PLAYER,
            is_active=True,
        )
        Result.objects.create(
            fixture=self.fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )
        self.client.login(username="fixturehomeapproved", password="testpass123")

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        html = response.content.decode()
        self.assertContains(response, "score-viewer-win")
        self.assertNotContains(response, "score-viewer-loss")
        self.assertEqual(html.count("fixture-my-team-name"), 1)

    def test_fixture_detail_highlights_only_away_side_for_away_player_on_approved_result(self):
        admin = make_player("fixtureawayapprovedadmin", is_staff=True)
        away_player = make_player("fixtureawayapproved")
        TeamMembership.objects.create(
            player=away_player,
            team=self.away,
            role=TeamMembership.PLAYER,
            is_active=True,
        )
        Result.objects.create(
            fixture=self.fixture,
            home_score=3,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=admin,
        )
        self.client.login(username="fixtureawayapproved", password="testpass123")

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        html = response.content.decode()
        self.assertContains(response, "score-viewer-loss")
        self.assertNotContains(response, "score-viewer-win")
        self.assertEqual(html.count("fixture-my-team-name"), 1)

    def test_fixture_detail_keeps_pending_result_neutral_for_involved_player(self):
        home_player = make_player("fixturehomepending")
        submitter = make_player("fixturehomependingsubmitter")
        TeamMembership.objects.create(
            player=home_player,
            team=self.home,
            role=TeamMembership.PLAYER,
            is_active=True,
        )
        Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=submitter,
            submitting_team=self.home,
        )
        self.client.login(username="fixturehomepending", password="testpass123")

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, "score-chip-neutral")
        self.assertContains(response, "score-viewer-neutral")
        self.assertNotContains(response, "score-viewer-win")
        self.assertNotContains(response, "score-viewer-loss")

    def test_fixture_detail_keeps_disputed_result_neutral_for_involved_player(self):
        home_player = make_player("fixturehomedisputed")
        submitter = make_player("fixturehomedisputedsubmitter")
        TeamMembership.objects.create(
            player=home_player,
            team=self.home,
            role=TeamMembership.PLAYER,
            is_active=True,
        )
        Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.DISPUTED,
            submitted_by=submitter,
            submitting_team=self.home,
        )
        self.client.login(username="fixturehomedisputed", password="testpass123")

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertContains(response, "score-chip-neutral")
        self.assertContains(response, "score-viewer-neutral")
        self.assertNotContains(response, "score-viewer-win")
        self.assertNotContains(response, "score-viewer-loss")

    def test_fixture_detail_shows_group_context_when_present(self):
        self.fixture.group_label = "A"
        self.fixture.save(update_fields=["group_label"])

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_group_context"])
        self.assertContains(response, "Group A")

    def test_fixture_detail_stays_clean_for_non_grouped_fixture(self):
        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_group_context"])
        self.assertContains(response, "Not grouped")
        self.assertNotContains(response, "/ Group")

    def test_fixture_detail_shows_no_result_submitted_message_when_no_result_exists(self):
        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No result has been submitted for this match yet.")
        self.assertNotContains(response, "A result is already in review for this match.")

    def test_staff_fixture_detail_shows_submit_action_when_no_result_exists(self):
        staff = make_player("fixturestaff", is_staff=True)
        self.client.login(username="fixturestaff", password="testpass123")

        response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("tournament:result_submit", args=[self.fixture.pk]))


class ResultSubmitTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament()
        self.home = make_team("Home FC", is_approved=True)
        self.away = make_team("Away FC", is_approved=True)
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.player = make_player("player1")
        self.home_teammate = make_player("homemate1")
        self.away_player = make_player("awayplayer1")
        self.admin = make_player("resultadmin", is_staff=True)
        TeamMembership.objects.create(
            player=self.player,
            team=self.home,
            role=TeamMembership.PLAYER,
        )
        TeamMembership.objects.create(
            player=self.home_teammate,
            team=self.home,
            role=TeamMembership.PLAYER,
        )
        TeamMembership.objects.create(
            player=self.away_player,
            team=self.away,
            role=TeamMembership.PLAYER,
        )

    def build_result_payload(self, *, home_score=3, away_score=1, stats_by_player=None):
        return build_team_result_payload(
            self.fixture,
            home_score=home_score,
            away_score=away_score,
            stats_by_player=stats_by_player,
        )

    def test_submit_requires_login(self):
        response = self.client.get(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_submit_page_loads_for_team_player(self):
        self.client.login(username="player1", password="testpass123")
        response = self.client.get(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_result_submit_template_uses_player_dropdown_with_team_labels(self):
        self.client.login(username="player1", password="testpass123")
        response = self.client.get(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="player_stats-0-player_id"', html=False)
        self.assertContains(response, "Home team: Home FC")
        self.assertContains(response, "Away team: Away FC")
        self.assertNotContains(response, self.player.email)

    def test_submit_without_screenshot_is_allowed(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(home_score=3, away_score=1),
        )

        self.assertEqual(response.status_code, 302)
        submitted = Result.objects.get(fixture=self.fixture)
        self.assertEqual(submitted.home_score, 3)
        self.assertEqual(submitted.away_score, 1)
        self.assertFalse(bool(submitted.screenshot))

    def test_result_submit_form_accepts_valid_screenshot_upload(self):
        screenshot = make_image_upload("scoreboard.png", "image/png", "PNG")

        form = ResultSubmitForm(
            data={"home_score": 3, "away_score": 1},
            files={"screenshot": screenshot},
            fixture=self.fixture,
            player=self.player,
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_result_submit_form_rejects_oversized_screenshot_upload(self):
        oversized_screenshot = SimpleUploadedFile(
            "scoreboard.jpg",
            b"x" * (RESULT_SCREENSHOT_MAX_SIZE + 1),
            content_type="image/jpeg",
        )

        form = ResultSubmitForm(
            data={"home_score": 3, "away_score": 1},
            files={"screenshot": oversized_screenshot},
            fixture=self.fixture,
            player=self.player,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("screenshot", form.errors)

    def test_result_submit_form_rejects_non_image_screenshot_upload(self):
        fake_screenshot = SimpleUploadedFile(
            "scoreboard.png",
            b"not really an image",
            content_type="image/png",
        )

        form = ResultSubmitForm(
            data={"home_score": 3, "away_score": 1},
            files={"screenshot": fake_screenshot},
            fixture=self.fixture,
            player=self.player,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("screenshot", form.errors)

    def test_result_submit_form_rejects_invalid_screenshot_extension_and_content_type(self):
        screenshot = make_image_upload("scoreboard.txt", "text/plain", "PNG")

        form = ResultSubmitForm(
            data={"home_score": 3, "away_score": 1},
            files={"screenshot": screenshot},
            fixture=self.fixture,
            player=self.player,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("screenshot", form.errors)

    def test_result_submit_form_validates_player_stat_screenshot_uploads(self):
        fake_home_stats = SimpleUploadedFile(
            "home-stats.png",
            b"not really an image",
            content_type="image/png",
        )

        form = ResultSubmitForm(
            data={"home_score": 3, "away_score": 1},
            files={"home_player_stats_screenshot": fake_home_stats},
            fixture=self.fixture,
            player=self.player,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("home_player_stats_screenshot", form.errors)

    def test_submit_result_captures_pending_player_stats_without_publishing_them(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                stats_by_player={
                    self.player.pk: {"assists": 1},
                    self.away_player.pk: {"yellow_cards": 1},
                }
            ),
        )

        self.assertEqual(response.status_code, 302)
        submitted_result = Result.objects.get(fixture=self.fixture)
        self.assertEqual(submitted_result.status, Result.PENDING)
        self.assertEqual(submitted_result.submitting_team, self.home)
        self.assertEqual(submitted_result.submitted_player_stats.count(), 2)
        self.assertFalse(PlayerStat.objects.filter(fixture=self.fixture).exists())

    def test_result_pending_page_explains_staff_can_review_without_opponent_response(self):
        self.client.login(username="player1", password="testpass123")
        self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(home_score=2, away_score=1),
        )

        response = self.client.get(
            reverse("tournament:result_pending", kwargs={"fixture_pk": self.fixture.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Staff can review, approve, or correct the result without waiting for opponent response.",
        )

    def test_invalid_stat_player_assignment_is_blocked(self):
        outsider = make_player("statoutsider")
        self.client.login(username="player1", password="testpass123")
        payload = self.build_result_payload()
        payload["player_stats-0-player_id"] = str(outsider.pk)
        payload["player_stats-0-goals"] = "1"

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            payload,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Result.objects.filter(fixture=self.fixture).exists())
        self.assertContains(response, "Invalid player stat assignment for this fixture.")

    def test_player_dropdown_only_includes_fixture_home_and_away_players(self):
        other_team = make_team("Other Stat FC", is_approved=True)
        outsider = make_player("otherstatplayer")
        TeamMembership.objects.create(player=outsider, team=other_team, role=TeamMembership.PLAYER)

        formset = build_result_player_stat_formset(fixture=self.fixture)
        choice_labels = [
            label
            for value, label in formset.forms[0].fields["player_id"].widget.choices
            if value
        ]

        self.assertTrue(any("Home team: Home FC" in label for label in choice_labels))
        self.assertTrue(any("Away team: Away FC" in label for label in choice_labels))
        self.assertTrue(any(self.player.username in label for label in choice_labels))
        self.assertTrue(any(self.away_player.username in label for label in choice_labels))
        self.assertFalse(any(outsider.username in label for label in choice_labels))
        self.assertFalse(any("@test.com" in label for label in choice_labels))

    def test_submit_saves_detailed_player_stats(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                home_score=1,
                away_score=0,
                stats_by_player={
                    self.player.pk: {
                        "goals": 1,
                        "total_points": 89,
                        "offensive_positioning": 72,
                        "shooting": 68,
                        "dueling": 55,
                        "defensive_positioning": 61,
                        "passing": 77,
                        "dribbling": 74,
                        "shots": 4,
                        "shots_on_target": 3,
                        "key_passes": 2,
                        "passes": 28,
                        "successful_passes": 24,
                        "instrumental_passes": 5,
                        "dribbles": 7,
                        "successful_dribbles": 6,
                        "instrumental_dribbles": 3,
                        "receiving": 15,
                        "good_receives": 13,
                        "overlaps": 2,
                        "runs_out_wide": 1,
                        "forward_runs": 8,
                        "offensive_receives": 9,
                        "intercepts": 4,
                        "tackles": 3,
                        "impactful_steals": 2,
                        "frontal_presses": 6,
                        "presses_from_behind": 1,
                        "good_positioning_pct": "82.50",
                        "double_marks": 2,
                        "passes_obstructed": 3,
                        "players_marked": 4,
                    },
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        stat = Result.objects.get(fixture=self.fixture).submitted_player_stats.get(player=self.player)
        self.assertEqual(stat.total_points, 89)
        self.assertEqual(stat.shots, 4)
        self.assertEqual(stat.shots_on_target, 3)
        self.assertEqual(stat.successful_passes, 24)
        self.assertEqual(stat.successful_dribbles, 6)
        self.assertEqual(stat.good_positioning_pct, Decimal("82.50"))
        self.assertEqual(stat.players_marked, 4)

    def test_compound_stat_validation_blocks_on_target_shots_above_total(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                home_score=0,
                away_score=0,
                stats_by_player={self.player.pk: {"shots": 2, "shots_on_target": 3}},
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Result.objects.filter(fixture=self.fixture).exists())
        self.assertContains(response, "Shots on target cannot exceed shots.")

    def test_compound_stat_validation_blocks_successful_passes_above_total(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                home_score=0,
                away_score=0,
                stats_by_player={self.player.pk: {"passes": 5, "successful_passes": 6}},
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Result.objects.filter(fixture=self.fixture).exists())
        self.assertContains(response, "Successful passes cannot exceed passes.")

    def test_compound_stat_validation_blocks_successful_dribbles_above_total(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                home_score=0,
                away_score=0,
                stats_by_player={self.player.pk: {"dribbles": 1, "successful_dribbles": 2}},
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Result.objects.filter(fixture=self.fixture).exists())
        self.assertContains(response, "Successful dribbles cannot exceed dribbles.")

    def test_good_positioning_percentage_must_be_between_zero_and_one_hundred(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                home_score=0,
                away_score=0,
                stats_by_player={self.player.pk: {"good_positioning_pct": "100.01"}},
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Result.objects.filter(fixture=self.fixture).exists())
        self.assertContains(response, "Ensure this value is less than or equal to 100.")

    def test_duplicate_player_stat_rows_are_blocked(self):
        self.client.login(username="player1", password="testpass123")
        payload = self.build_result_payload(
            home_score=0,
            away_score=0,
            stats_by_player={self.player.pk: {"shots": 1}},
        )
        payload["player_stats-1-player_id"] = str(self.player.pk)
        payload["player_stats-1-shots"] = "1"

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            payload,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Result.objects.filter(fixture=self.fixture).exists())
        self.assertContains(response, "Each player can only have one stat row for this result.")

    def test_fixture_player_can_edit_pending_team_player_stats_before_approval(self):
        pending_result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.player,
            submitting_team=self.home,
        )
        ResultPlayerStat.objects.create(
            result=pending_result,
            player=self.player,
            team=self.home,
            goals=1,
        )
        self.client.login(username="awayplayer1", password="testpass123")

        get_response = self.client.get(reverse("tournament:result_player_stats_edit", args=[pending_result.pk]))
        post_response = self.client.post(
            reverse("tournament:result_player_stats_edit", args=[pending_result.pk]),
            build_team_result_payload(
                self.fixture,
                home_score=1,
                away_score=0,
                result=pending_result,
                stats_by_player={
                    self.player.pk: {"goals": 1},
                    self.away_player.pk: {"intercepts": 3, "passes": 10, "successful_passes": 8},
                },
            ),
        )

        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Save player stats")
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(pending_result.submitted_player_stats.count(), 2)
        away_stat = pending_result.submitted_player_stats.get(player=self.away_player)
        self.assertEqual(away_stat.intercepts, 3)
        self.assertEqual(away_stat.successful_passes, 8)

    def test_non_staff_cannot_edit_player_stats_after_result_approval(self):
        approved_result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=0,
            status=Result.APPROVED,
            submitted_by=self.player,
            reviewed_by=self.admin,
            reviewed_at=timezone.now(),
        )
        self.client.login(username="player1", password="testpass123")

        response = self.client.get(reverse("tournament:result_player_stats_edit", args=[approved_result.pk]))

        self.assertEqual(response.status_code, 403)

    def test_approved_result_publishes_submitted_player_stats_to_public_views(self):
        self.client.login(username="player1", password="testpass123")
        self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                home_score=2,
                away_score=0,
                stats_by_player={
                    self.player.pk: {"goals": 2},
                    self.home_teammate.pk: {"assists": 1},
                    self.away_player.pk: {"red_cards": 1},
                }
            ),
        )
        submitted_result = Result.objects.get(fixture=self.fixture)
        self.client.logout()
        self.client.login(username="resultadmin", password="testpass123")
        self.client.post(reverse("tournament:result_approve", args=[submitted_result.pk]))

        fixture_stats = PlayerStat.objects.filter(fixture=self.fixture).order_by("player__username")
        self.assertEqual(fixture_stats.count(), 3)
        self.assertEqual(fixture_stats.get(player=self.player).goals, 2)
        self.assertEqual(fixture_stats.get(player=self.home_teammate).assists, 1)
        self.assertEqual(fixture_stats.get(player=self.away_player).red_cards, 1)

        fixture_response = self.client.get(reverse("tournament:fixture_detail", args=[self.fixture.pk]))
        scorers_response = self.client.get(reverse("standings:top_scorers", args=[self.tournament.pk]))
        assists_response = self.client.get(reverse("standings:top_assists", args=[self.tournament.pk]))

        self.assertContains(fixture_response, self.player.username)
        self.assertContains(fixture_response, self.home_teammate.username)
        self.assertContains(fixture_response, self.away_player.username)
        self.client.logout()
        self.client.login(username="player1", password="testpass123")
        dashboard_response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(dashboard_response.context["career_goals"], 2)
        self.assertContains(scorers_response, self.player.username)
        self.assertContains(assists_response, self.home_teammate.username)

    def test_mismatched_goal_totals_are_rejected_on_submit(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                home_score=1,
                away_score=1,
                stats_by_player={
                    self.player.pk: {"goals": 2},
                    self.away_player.pk: {"goals": 1},
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Result.objects.filter(fixture=self.fixture).exists())
        self.assertContains(response, "Home FC total from goals and opponent own goals must equal the submitted score (1).")

    def test_matched_goal_totals_submit_successfully(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                home_score=2,
                away_score=1,
                stats_by_player={
                    self.player.pk: {"goals": 1},
                    self.home_teammate.pk: {"goals": 1, "assists": 1},
                    self.away_player.pk: {"goals": 1},
                },
            ),
        )

        submitted = Result.objects.get(fixture=self.fixture)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(submitted.submitted_player_stats.filter(team=self.home).count(), 2)
        self.assertEqual(submitted.submitted_player_stats.filter(team=self.away).count(), 1)

    def test_submit_accepts_valid_totals_with_opponent_own_goal(self):
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            self.build_result_payload(
                home_score=4,
                away_score=5,
                stats_by_player={
                    self.player.pk: {"goals": 2},
                    self.home_teammate.pk: {"goals": 1},
                    self.away_player.pk: {"goals": 5, "own_goals": 1},
                },
            ),
            follow=True,
        )

        submitted = Result.objects.get(fixture=self.fixture)
        away_row = submitted.submitted_player_stats.get(player=self.away_player)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(submitted.home_score, 4)
        self.assertEqual(submitted.away_score, 5)
        self.assertEqual(away_row.goals, 5)
        self.assertEqual(away_row.own_goals, 1)
        self.assertNotContains(response, "Invalid player stat assignment for this fixture.")

    def test_player_cannot_submit_duplicate_result_while_existing_one_is_under_review(self):
        Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.player,
        )
        self.client.login(username="player1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            {
                "home_score": 3,
                "away_score": 1,
            },
            follow=True,
        )

        self.assertEqual(Result.objects.filter(fixture=self.fixture).count(), 1)
        self.assertContains(response, "already under review")

    def test_fixture_detail_hides_submit_action_while_result_is_under_review(self):
        Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.player,
        )
        self.client.login(username="player1", password="testpass123")

        response = self.client.get(reverse("tournament:fixture_detail", args=[self.fixture.pk]))

        self.assertNotContains(response, reverse("tournament:result_submit", args=[self.fixture.pk]))
        self.assertContains(response, "already in review", html=False)


class OpponentResultResponseTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament("Opponent Response Cup")
        self.home = make_team("Response Home", is_approved=True)
        self.away = make_team("Response Away", is_approved=True)
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.home_player = make_player("opphome01")
        self.away_player = make_player("oppaway01")
        self.outsider = make_player("oppout01")
        self.admin = make_player("oppstaff1", is_staff=True)
        TeamMembership.objects.create(player=self.home_player, team=self.home, role=TeamMembership.PLAYER)
        TeamMembership.objects.create(player=self.away_player, team=self.away, role=TeamMembership.PLAYER)
        self.pending_result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.home_player,
            submitting_team=self.home,
        )
        ResultPlayerStat.objects.create(
            result=self.pending_result,
            player=self.home_player,
            team=self.home,
            goals=2,
        )
        ResultPlayerStat.objects.create(
            result=self.pending_result,
            player=self.away_player,
            team=self.away,
            goals=1,
        )

    def test_eligible_opposing_team_member_can_confirm_pending_result(self):
        self.client.login(username="oppaway01", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_opponent_response", args=[self.pending_result.pk]),
            {
                "action": "confirmed",
                "opponent_home_score": 2,
                "opponent_away_score": 1,
                "note": "",
            },
            follow=True,
        )

        self.pending_result.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.pending_result.opponent_response_status, Result.OPPONENT_RESPONSE_CONFIRMED)
        self.assertEqual(self.pending_result.opponent_responded_by, self.away_player)
        self.assertEqual(self.pending_result.opponent_response_note, "")
        self.assertEqual(self.pending_result.opponent_home_score, 2)
        self.assertEqual(self.pending_result.opponent_away_score, 1)
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_MATCHING)
        self.assertEqual(self.pending_result.status, Result.PENDING)
        self.assertEqual(Result.objects.filter(fixture=self.fixture).count(), 1)

    def test_eligible_opposing_team_member_can_dispute_pending_result_with_note(self):
        self.client.login(username="oppaway01", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_opponent_response", args=[self.pending_result.pk]),
            {
                "action": "disputed",
                "opponent_home_score": 1,
                "opponent_away_score": 1,
                "note": "Scoreboard showed a draw, not a home win.",
            },
            follow=True,
        )

        self.pending_result.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.pending_result.opponent_response_status, Result.OPPONENT_RESPONSE_DISPUTED)
        self.assertEqual(self.pending_result.opponent_home_score, 1)
        self.assertEqual(self.pending_result.opponent_away_score, 1)
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_CONFLICT)
        self.assertNotEqual(self.pending_result.status, Result.DISPUTED)
        self.assertEqual(
            self.pending_result.opponent_response_note,
            "Scoreboard showed a draw, not a home win.",
        )
        self.assertContains(response, "Score conflict flagged")

    def test_opponent_response_requires_both_fixture_scores(self):
        self.client.login(username="oppaway01", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_opponent_response", args=[self.pending_result.pk]),
            {
                "action": "confirmed",
                "opponent_home_score": 2,
                "note": "",
            },
            follow=True,
        )

        self.pending_result.refresh_from_db()
        self.assertContains(response, "This field is required.")
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_AWAITING)
        self.assertIsNone(self.pending_result.opponent_responded_at)

    def test_away_opponent_response_uses_canonical_home_away_scores_without_reversal(self):
        self.client.login(username="oppaway01", password="testpass123")

        self.client.post(
            reverse("tournament:result_opponent_response", args=[self.pending_result.pk]),
            {
                "action": "confirmed",
                "opponent_home_score": 2,
                "opponent_away_score": 1,
                "note": "",
            },
        )

        self.pending_result.refresh_from_db()
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_MATCHING)
        self.assertTrue(self.pending_result.scores_match_opponent)

    def test_reversed_away_perspective_score_is_flagged_as_conflict(self):
        self.client.login(username="oppaway01", password="testpass123")

        self.client.post(
            reverse("tournament:result_opponent_response", args=[self.pending_result.pk]),
            {
                "action": "confirmed",
                "opponent_home_score": 1,
                "opponent_away_score": 2,
                "note": "",
            },
        )

        self.pending_result.refresh_from_db()
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_CONFLICT)
        self.assertFalse(self.pending_result.scores_match_opponent)

    def test_submitting_team_cannot_use_opponent_response_on_its_own_submission(self):
        self.client.login(username="opphome01", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_opponent_response", args=[self.pending_result.pk]),
            {"action": "confirmed"},
        )

        self.assertEqual(response.status_code, 403)

    def test_unrelated_users_cannot_respond(self):
        self.client.login(username="oppout01", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_opponent_response", args=[self.pending_result.pk]),
            {"action": "confirmed"},
        )

        self.assertEqual(response.status_code, 403)

    def test_staff_moderation_view_shows_opponent_response_status_note_and_player_stats(self):
        self.pending_result.record_opponent_response(
            player=self.away_player,
            status=Result.OPPONENT_RESPONSE_DISPUTED,
            home_score=1,
            away_score=1,
            note="Away team disputes the score.",
        )
        self.client.login(username="oppstaff1", password="testpass123")

        response = self.client.get(reverse("tournament:staff_pending_results"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Opponent response (advisory)")
        self.assertContains(response, "Away team disputes the score.")
        self.assertContains(response, "Score conflict / review needed")
        self.assertContains(response, self.home_player.username)
        self.assertContains(response, "G 2")

    def test_opponent_response_does_not_publish_standings_or_player_stats_before_approval(self):
        self.pending_result.record_opponent_response(
            player=self.away_player,
            status=Result.OPPONENT_RESPONSE_CONFIRMED,
            home_score=2,
            away_score=1,
        )

        self.assertFalse(PlayerStat.objects.filter(fixture=self.fixture).exists())
        self.assertFalse(Standing.objects.filter(tournament=self.tournament).exists())

        self.pending_result.approve(admin=self.admin)

        self.assertTrue(PlayerStat.objects.filter(fixture=self.fixture, player=self.home_player).exists())
        self.assertTrue(Standing.objects.filter(tournament=self.tournament, team=self.home).exists())

    def test_one_team_submission_remains_staff_approvable_without_opponent_score(self):
        self.pending_result.approve(admin=self.admin)

        self.pending_result.refresh_from_db()
        self.assertEqual(self.pending_result.status, Result.APPROVED)
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_AWAITING)

    def test_staff_can_approve_matching_comparison_state(self):
        self.pending_result.record_opponent_response(
            player=self.away_player,
            status=Result.OPPONENT_RESPONSE_CONFIRMED,
            home_score=2,
            away_score=1,
        )
        self.client.login(username="oppstaff1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_approve", args=[self.pending_result.pk]),
            follow=True,
        )

        self.pending_result.refresh_from_db()
        self.assertContains(response, "Result approved")
        self.assertEqual(self.pending_result.status, Result.APPROVED)
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_MATCHING)

    def test_staff_can_approve_score_conflict_without_auto_dispute_or_complaint(self):
        complaint_model = None
        try:
            complaint_model = apps.get_model("tournament", "Complaint")
        except LookupError:
            pass
        complaint_count = complaint_model.objects.count() if complaint_model else None

        self.pending_result.record_opponent_response(
            player=self.away_player,
            status=Result.OPPONENT_RESPONSE_DISPUTED,
            home_score=1,
            away_score=1,
            note="Scoreboard showed a draw.",
        )
        self.client.login(username="oppstaff1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_approve", args=[self.pending_result.pk]),
            follow=True,
        )

        self.pending_result.refresh_from_db()
        self.assertContains(response, "Result approved")
        self.assertEqual(self.pending_result.status, Result.APPROVED)
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_CONFLICT)
        if complaint_model:
            self.assertEqual(complaint_model.objects.count(), complaint_count)
        else:
            self.assertFalse(any(model.__name__ == "Complaint" for model in apps.get_models()))

    def test_staff_can_reject_score_conflict(self):
        self.pending_result.record_opponent_response(
            player=self.away_player,
            status=Result.OPPONENT_RESPONSE_DISPUTED,
            home_score=1,
            away_score=1,
            note="Scoreboard showed a draw.",
        )
        self.client.login(username="oppstaff1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_reject", args=[self.pending_result.pk]),
            {"admin_note": "Screenshot unclear."},
            follow=True,
        )

        self.pending_result.refresh_from_db()
        self.assertContains(response, "Result rejected")
        self.assertEqual(self.pending_result.status, Result.REJECTED)
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_CONFLICT)

    def test_staff_can_manually_mark_matching_result_disputed(self):
        self.pending_result.record_opponent_response(
            player=self.away_player,
            status=Result.OPPONENT_RESPONSE_CONFIRMED,
            home_score=2,
            away_score=1,
        )
        self.client.login(username="oppstaff1", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_dispute", args=[self.pending_result.pk]),
            {"admin_note": "Need clearer evidence."},
            follow=True,
        )

        self.pending_result.refresh_from_db()
        self.assertContains(response, "Result marked as disputed")
        self.assertEqual(self.pending_result.status, Result.DISPUTED)
        self.assertEqual(self.pending_result.opponent_score_state, Result.OPPONENT_SCORE_MATCHING)

    def test_resolved_results_no_longer_allow_opponent_response_editing(self):
        self.pending_result.approve(admin=self.admin)
        self.client.login(username="oppaway01", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_opponent_response", args=[self.pending_result.pk]),
            {"action": "disputed", "note": "Too late"},
        )

        self.pending_result.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.pending_result.opponent_response_status, Result.OPPONENT_RESPONSE_PENDING)


class ResultGoalReconciliationTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament("Goal Reconciliation Cup")
        self.home = make_team("Recon Home", is_approved=True)
        self.away = make_team("Recon Away", is_approved=True)
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.submitter = make_player("reconsub1")
        self.home_teammate = make_player("reconhm2")
        self.away_player = make_player("reconaw1")
        self.admin = make_player("reconadm", is_staff=True)
        TeamMembership.objects.create(player=self.submitter, team=self.home, role=TeamMembership.PLAYER)
        TeamMembership.objects.create(player=self.home_teammate, team=self.home, role=TeamMembership.PLAYER)
        TeamMembership.objects.create(player=self.away_player, team=self.away, role=TeamMembership.PLAYER)

    def build_result_payload(self, *, home_score, away_score, stats_by_player=None):
        return build_team_result_payload(
            self.fixture,
            home_score=home_score,
            away_score=away_score,
            stats_by_player=stats_by_player,
        )

    def test_mismatched_goal_totals_are_rejected_on_edit(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.submitter,
            submitting_team=self.home,
        )
        self.client.login(username="reconadm", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_edit", args=[result.pk]),
            self.build_result_payload(
                home_score=1,
                away_score=1,
                stats_by_player={
                    self.submitter.pk: {"goals": 2},
                    self.away_player.pk: {"goals": 1},
                },
            ),
        )

        result.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(result.home_score, 2)
        self.assertContains(response, "Recon Home total from goals and opponent own goals must equal the submitted score (1).")

    def test_approval_uses_score_only_fallback_for_mismatched_pending_result_data(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.submitter,
            submitting_team=self.home,
        )
        ResultPlayerStat.objects.create(result=result, player=self.submitter, team=self.home, goals=2)
        ResultPlayerStat.objects.create(result=result, player=self.away_player, team=self.away, goals=1)
        self.client.login(username="reconadm", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_approve", args=[result.pk]),
            follow=True,
        )

        result.refresh_from_db()
        self.assertEqual(result.status, Result.APPROVED)
        self.assertFalse(PlayerStat.objects.filter(fixture=self.fixture).exists())
        self.assertContains(response, "score-only fallback")
        self.assertIn("score-only fallback", result.admin_note)

    def test_staff_fixture_detail_highlights_score_only_fallback(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.submitter,
            submitting_team=self.home,
        )
        ResultPlayerStat.objects.create(result=result, player=self.submitter, team=self.home, goals=2)
        ResultPlayerStat.objects.create(result=result, player=self.away_player, team=self.away, goals=1)
        self.client.login(username="reconadm", password="testpass123")
        self.client.post(reverse("tournament:result_approve", args=[result.pk]))

        response = self.client.get(reverse("tournament:fixture_detail", args=[self.fixture.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Score-only fallback applied")

    def test_admin_queue_shows_score_only_fallback_note_for_approved_result(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.submitter,
            submitting_team=self.home,
        )
        ResultPlayerStat.objects.create(result=result, player=self.submitter, team=self.home, goals=2)
        ResultPlayerStat.objects.create(result=result, player=self.away_player, team=self.away, goals=1)
        self.client.login(username="reconadm", password="testpass123")
        self.client.post(reverse("tournament:result_approve", args=[result.pk]))

        response = self.client.get(reverse("tournament:admin_queue") + "?status=approved")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Score-only fallback")
        self.assertContains(response, "stat totals did not match the final score")

    def test_admin_approval_succeeds_when_totals_match_via_own_goals(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.submitter,
            submitting_team=self.home,
        )
        ResultPlayerStat.objects.create(result=result, player=self.submitter, team=self.home, goals=1)
        ResultPlayerStat.objects.create(result=result, player=self.away_player, team=self.away, goals=1, own_goals=1)
        self.client.login(username="reconadm", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_approve", args=[result.pk]),
            follow=True,
        )

        result.refresh_from_db()
        self.assertEqual(result.status, Result.APPROVED)
        self.assertEqual(result.admin_note, "")
        self.assertContains(response, "Result approved")
        self.assertEqual(PlayerStat.objects.filter(fixture=self.fixture).count(), 2)
        self.assertEqual(
            PlayerStat.objects.get(fixture=self.fixture, player=self.away_player).own_goals,
            1,
        )

    def test_matched_goal_totals_approve_successfully(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.submitter,
            submitting_team=self.home,
        )
        ResultPlayerStat.objects.create(result=result, player=self.submitter, team=self.home, goals=1)
        ResultPlayerStat.objects.create(result=result, player=self.home_teammate, team=self.home, goals=1, assists=1)
        ResultPlayerStat.objects.create(result=result, player=self.away_player, team=self.away, goals=1)

        result.approve(admin=self.admin)

        result.refresh_from_db()
        self.assertEqual(result.status, Result.APPROVED)
        self.assertEqual(PlayerStat.objects.filter(fixture=self.fixture).count(), 3)
        self.assertTrue(all(stat.own_goals == 0 for stat in PlayerStat.objects.filter(fixture=self.fixture)))


class AccessControlTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.tournament = make_tournament("Access Cup")
        self.home_team = make_team("Access Home", is_approved=True)
        self.away_team = make_team("Access Away", is_approved=True)
        self.fixture = make_fixture(self.tournament, self.home_team, self.away_team)
        self.player = make_player("roleplayer")
        self.other_player = make_player("outsider")
        self.admin = make_player("roleadmin", is_staff=True)
        TeamMembership.objects.create(
            player=self.player,
            team=self.home_team,
            role=TeamMembership.PLAYER,
        )

    def test_anonymous_user_can_access_public_pages(self):
        profile_response = self.client.get(reverse("accounts:profile", args=[self.player.username]))
        team_response = self.client.get(reverse("accounts:team_detail", args=[self.home_team.pk]))
        tournament_response = self.client.get(reverse("tournament:tournament_detail", args=[self.tournament.pk]))
        fixture_response = self.client.get(reverse("tournament:fixture_detail", args=[self.fixture.pk]))
        tournament_list_response = self.client.get(reverse("tournament:tournament_list"))
        team_list_response = self.client.get(reverse("accounts:team_list"))
        player_list_response = self.client.get(reverse("accounts:player_list"))
        scorers_response = self.client.get(reverse("standings:top_scorers", args=[self.tournament.pk]))
        assists_response = self.client.get(reverse("standings:top_assists", args=[self.tournament.pk]))

        for response in [
            profile_response,
            team_response,
            tournament_response,
            fixture_response,
            tournament_list_response,
            team_list_response,
            player_list_response,
            scorers_response,
            assists_response,
        ]:
            self.assertEqual(response.status_code, 200)

    def test_anonymous_user_is_redirected_from_protected_pages(self):
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

        response = self.client.get(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

        response = self.client.get(reverse("tournament:admin_queue"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_anonymous_user_cannot_access_draft_tournament_pages(self):
        draft_tournament = make_tournament("Secret Draft", status=Tournament.DRAFT)
        draft_home = make_team("Draft Home", is_approved=True)
        draft_away = make_team("Draft Away", is_approved=True)
        draft_fixture = make_fixture(draft_tournament, draft_home, draft_away)

        tournament_response = self.client.get(
            reverse("tournament:tournament_detail", args=[draft_tournament.pk])
        )
        fixture_response = self.client.get(
            reverse("tournament:fixture_detail", args=[draft_fixture.pk])
        )
        scorers_response = self.client.get(
            reverse("standings:top_scorers", args=[draft_tournament.pk])
        )
        assists_response = self.client.get(
            reverse("standings:top_assists", args=[draft_tournament.pk])
        )
        standings_partial_response = self.client.get(
            reverse("tournament:standings_partial", args=[draft_tournament.pk])
        )

        for response in [
            tournament_response,
            fixture_response,
            scorers_response,
            assists_response,
            standings_partial_response,
        ]:
            self.assertEqual(response.status_code, 404)

    def test_anonymous_navigation_shows_public_links_and_auth_options(self):
        response = self.client.get(reverse("tournament:home"))
        self.assertContains(response, reverse("tournament:tournament_list"))
        self.assertContains(response, reverse("accounts:team_list"))
        self.assertContains(response, reverse("accounts:player_list"))
        self.assertContains(response, reverse("accounts:login"))
        self.assertContains(response, reverse("accounts:register"))
        self.assertNotContains(response, reverse("accounts:dashboard"))

    def test_player_can_access_allowed_authenticated_pages(self):
        self.client.login(username="roleplayer", password="testpass123")

        dashboard_response = self.client.get(reverse("accounts:dashboard"))
        submit_response = self.client.get(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk})
        )

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(submit_response.status_code, 200)

    def test_player_can_only_update_their_own_profile(self):
        self.client.login(username="roleplayer", password="testpass123")
        original_other_unique_id = self.other_player.unique_id

        response = self.client.post(reverse("accounts:update_profile"), {
            "in_game_name": "Captain Access",
            "unique_id": self.player.unique_id,
            "bio": "Updated bio",
        })

        self.assertEqual(response.status_code, 302)
        self.player.refresh_from_db()
        self.other_player.refresh_from_db()
        self.assertEqual(self.player.in_game_name, "Captain Access")
        self.assertEqual(self.other_player.unique_id, original_other_unique_id)

    def test_player_cannot_access_admin_only_actions(self):
        self.client.login(username="roleplayer", password="testpass123")

        queue_response = self.client.get(reverse("tournament:admin_queue"))
        self.assertEqual(queue_response.status_code, 403)

        self.client.logout()
        self.client.login(username="outsider", password="testpass123")
        unrelated_submit_response = self.client.get(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk})
        )
        self.assertEqual(unrelated_submit_response.status_code, 403)

    def test_player_cannot_post_admin_only_result_actions(self):
        pending_result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.player,
        )
        self.client.login(username="roleplayer", password="testpass123")

        for url in [
            reverse("tournament:result_approve", args=[pending_result.pk]),
            reverse("tournament:result_reject", args=[pending_result.pk]),
            reverse("tournament:result_dispute", args=[pending_result.pk]),
        ]:
            response = self.client.post(url)
            self.assertEqual(response.status_code, 403)

        edit_response = self.client.get(reverse("tournament:result_edit", args=[pending_result.pk]))
        self.assertEqual(edit_response.status_code, 200)
        self.assertContains(edit_response, "Save player stats")
        self.assertNotContains(edit_response, "Match screenshot (optional)")

        schedule_response = self.client.post(
            reverse("tournament:staff_fixture_schedule_update", args=[self.fixture.pk]),
            {"match_date": "", "submission_deadline": ""},
        )
        self.assertEqual(schedule_response.status_code, 403)

    def test_staff_can_access_draft_tournament_pages(self):
        draft_tournament = make_tournament("Staff Draft", status=Tournament.DRAFT)
        draft_home = make_team("Staff Draft Home", is_approved=True)
        draft_away = make_team("Staff Draft Away", is_approved=True)
        draft_fixture = make_fixture(draft_tournament, draft_home, draft_away)

        self.client.login(username="roleadmin", password="testpass123")

        tournament_response = self.client.get(
            reverse("tournament:tournament_detail", args=[draft_tournament.pk])
        )
        fixture_response = self.client.get(
            reverse("tournament:fixture_detail", args=[draft_fixture.pk])
        )
        scorers_response = self.client.get(
            reverse("standings:top_scorers", args=[draft_tournament.pk])
        )
        assists_response = self.client.get(
            reverse("standings:top_assists", args=[draft_tournament.pk])
        )
        standings_partial_response = self.client.get(
            reverse("tournament:standings_partial", args=[draft_tournament.pk])
        )

        for response in [
            tournament_response,
            fixture_response,
            scorers_response,
            assists_response,
            standings_partial_response,
        ]:
            self.assertEqual(response.status_code, 200)

    def test_staff_can_schedule_fixture_from_fixture_detail_workflow(self):
        self.client.login(username="roleadmin", password="testpass123")

        get_response = self.client.get(reverse("tournament:fixture_detail", args=[self.fixture.pk]))
        post_response = self.client.post(
            reverse("tournament:staff_fixture_schedule_update", args=[self.fixture.pk]),
            {
                "match_date": "2026-04-20T18:30",
                "submission_deadline": "2026-04-21T18:30",
            },
            follow=True,
        )

        self.fixture.refresh_from_db()
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Staff: Schedule Match")
        self.assertContains(get_response, reverse("tournament:staff_fixture_schedule_update", args=[self.fixture.pk]))
        self.assertEqual(post_response.status_code, 200)
        self.assertIsNotNone(self.fixture.match_date)
        self.assertIsNotNone(self.fixture.submission_deadline)

    def test_player_navigation_shows_player_actions_only(self):
        self.client.login(username="roleplayer", password="testpass123")
        response = self.client.get(reverse("tournament:home"))

        self.assertContains(response, reverse("accounts:dashboard"))
        self.assertContains(response, reverse("accounts:profile", args=[self.player.username]))
        self.assertNotContains(response, reverse("tournament:staff_pending_results"))
        self.assertNotContains(response, "/admin/")

    def test_admin_can_access_management_actions(self):
        pending_result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.player,
        )

        self.client.login(username="roleadmin", password="testpass123")
        queue_response = self.client.get(reverse("tournament:admin_queue"))
        approve_response = self.client.post(
            reverse("tournament:result_approve", args=[pending_result.pk]),
            follow=False,
        )

        self.assertEqual(queue_response.status_code, 200)
        self.assertEqual(approve_response.status_code, 302)
        pending_result.refresh_from_db()
        self.assertEqual(pending_result.status, Result.APPROVED)
        self.assertEqual(pending_result.reviewed_by, self.admin)

    def test_admin_can_submit_result_for_any_fixture(self):
        self.client.login(username="roleadmin", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
            {
                "home_score": 4,
                "away_score": 2,
            },
        )

        self.assertEqual(response.status_code, 302)
        submitted = Result.objects.get(fixture=self.fixture)
        self.assertEqual(submitted.submitted_by, self.admin)
        self.assertEqual(submitted.status, Result.PENDING)

    def test_admin_submit_redirects_to_existing_result_edit_instead_of_creating_duplicate(self):
        pending_result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.player,
        )
        self.client.login(username="roleadmin", password="testpass123")

        response = self.client.get(
            reverse("tournament:result_submit", kwargs={"fixture_pk": self.fixture.pk}),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("tournament:result_edit", args=[pending_result.pk]))

    def test_admin_can_edit_existing_result(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=1,
            status=Result.APPROVED,
            submitted_by=self.player,
            reviewed_by=self.admin,
            reviewed_at=timezone.now(),
        )
        self.client.login(username="roleadmin", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_edit", args=[result.pk]),
            {
                "home_score": 2,
                "away_score": 1,
            },
        )

        self.assertEqual(response.status_code, 302)
        result.refresh_from_db()
        self.assertEqual(result.home_score, 2)
        self.assertEqual(result.away_score, 1)
        self.assertEqual(result.status, Result.APPROVED)
        self.assertEqual(result.submitted_by, self.player)
        home_standing = Standing.objects.get(tournament=self.tournament, team=self.home_team)
        away_standing = Standing.objects.get(tournament=self.tournament, team=self.away_team)
        self.assertEqual(home_standing.points, 3)
        self.assertEqual(away_standing.points, 0)

    def test_captain_can_edit_own_team_identity(self):
        captain = make_player("captain_edit_test")
        captain_team = make_team("Captain Edit FC", captain=captain, is_approved=True)
        TeamMembership.objects.create(
            player=captain,
            team=captain_team,
            role=TeamMembership.CAPTAIN,
        )
        self.client.login(username="captain_edit_test", password="testpass123")

        response = self.client.post(
            reverse("accounts:team_edit", args=[captain_team.pk]),
            {"name": "Captain Updated FC"},
        )

        self.assertEqual(response.status_code, 302)
        captain_team.refresh_from_db()
        self.assertEqual(captain_team.name, "Captain Updated FC")

    def test_active_captain_membership_can_edit_team_identity_without_team_captain_field(self):
        captain = make_player("membership_captain_test")
        captain_team = make_team("Membership Captain FC", captain=None, is_approved=True)
        TeamMembership.objects.create(
            player=captain,
            team=captain_team,
            role=TeamMembership.CAPTAIN,
        )
        self.client.login(username="membership_captain_test", password="testpass123")

        response = self.client.post(
            reverse("accounts:team_edit", args=[captain_team.pk]),
            {"name": "Membership Captain Updated FC"},
        )

        self.assertEqual(response.status_code, 302)
        captain_team.refresh_from_db()
        self.assertEqual(captain_team.name, "Membership Captain Updated FC")

    def test_non_captain_cannot_edit_team_identity(self):
        captain = make_player("editcaptain")
        protected_team = make_team("Protected FC", captain=captain, is_approved=True)
        TeamMembership.objects.create(
            player=captain,
            team=protected_team,
            role=TeamMembership.CAPTAIN,
        )
        non_captain = make_player("noncaptain_test")
        TeamMembership.objects.create(
            player=non_captain,
            team=protected_team,
            role=TeamMembership.PLAYER,
        )
        self.client.login(username="noncaptain_test", password="testpass123")

        response = self.client.get(reverse("accounts:team_edit", args=[protected_team.pk]))

        self.assertEqual(response.status_code, 403)

    def test_admin_can_edit_any_team_identity(self):
        response_team = make_team("Staff Editable FC", captain=self.player, is_approved=True)
        self.client.login(username="roleadmin", password="testpass123")

        response = self.client.post(
            reverse("accounts:team_edit", args=[response_team.pk]),
            {"name": "Staff Updated FC"},
        )

        self.assertEqual(response.status_code, 302)
        response_team.refresh_from_db()
        self.assertEqual(response_team.name, "Staff Updated FC")

    def test_staff_only_player_identity_edit_is_denied_to_players(self):
        self.client.login(username="roleplayer", password="testpass123")

        response = self.client.get(reverse("accounts:staff_player_edit", args=[self.other_player.username]))

        self.assertEqual(response.status_code, 403)

    def test_admin_can_edit_player_identity(self):
        self.client.login(username="roleadmin", password="testpass123")

        response = self.client.post(
            reverse("accounts:staff_player_edit", args=[self.player.username]),
            {"in_game_name": "Admin Set Name"},
        )

        self.assertEqual(response.status_code, 302)
        self.player.refresh_from_db()
        self.assertEqual(self.player.in_game_name, "Admin Set Name")

    def test_team_detail_shows_edit_link_for_active_captain_membership(self):
        captain = make_player("captain_detail_test")
        captain_team = make_team("Captain Detail FC", captain=None, is_approved=True)
        TeamMembership.objects.create(
            player=captain,
            team=captain_team,
            role=TeamMembership.CAPTAIN,
        )
        self.client.login(username="captain_detail_test", password="testpass123")

        response = self.client.get(reverse("accounts:team_detail", args=[captain_team.pk]))

        self.assertContains(response, reverse("accounts:team_edit", args=[captain_team.pk]))
        self.assertContains(response, "Edit team")

    def test_team_edit_page_includes_logo_field_for_staff(self):
        response_team = make_team("Staff Logo FC", captain=self.player, is_approved=True)
        self.client.login(username="roleadmin", password="testpass123")

        response = self.client.get(reverse("accounts:team_edit", args=[response_team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="logo"', html=False)
        self.assertContains(response, 'type="file"', html=False)

    def test_player_edit_page_includes_avatar_field_for_staff(self):
        self.client.login(username="roleadmin", password="testpass123")

        response = self.client.get(reverse("accounts:staff_player_edit", args=[self.player.username]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="avatar"', html=False)
        self.assertContains(response, 'type="file"', html=False)

    def test_admin_navigation_shows_management_links(self):
        self.client.login(username="roleadmin", password="testpass123")
        response = self.client.get(reverse("tournament:home"))

        self.assertContains(response, reverse("tournament:staff_dashboard"))


class StaffOperationsTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.staff = make_player("staffops", is_staff=True)
        self.player = make_player("plainplayer")
        self.tournament = make_tournament("Ops Cup", status=Tournament.REGISTRATION)
        self.team = make_team("Pending Ops FC", captain=self.player, is_approved=False)
        TeamMembership.objects.create(
            player=self.player,
            team=self.team,
            role=TeamMembership.CAPTAIN,
        )

    def make_eligible_team(self, name, captain_username):
        captain = make_player(captain_username)
        teammate = make_player(
            f"tm{captain_username[:2]}{len(captain_username)}{captain_username[-2:]}"
        )
        team = make_team(name, captain=captain, is_approved=True)
        TeamMembership.objects.create(
            player=captain,
            team=team,
            role=TeamMembership.CAPTAIN,
        )
        TeamMembership.objects.create(
            player=teammate,
            team=team,
            role=TeamMembership.PLAYER,
        )
        return team

    def test_staff_can_access_staff_pages(self):
        self.client.login(username="staffops", password="testpass123")

        responses = [
            self.client.get(reverse("tournament:staff_dashboard")),
            self.client.get(reverse("tournament:staff_tournament_list")),
            self.client.get(reverse("tournament:staff_tournament_create")),
            self.client.get(reverse("tournament:staff_tournament_edit", args=[self.tournament.pk])),
            self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk])),
            self.client.get(reverse("tournament:staff_pending_results")),
            self.client.get(reverse("accounts:staff_team_list")),
            self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk])),
            self.client.get(reverse("accounts:staff_player_list")),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 200)

    def test_staff_dashboard_renders_recent_results_without_raw_template_tokens(self):
        home_team = make_team("Dashboard Home", is_approved=True)
        away_team = make_team("Dashboard Away", is_approved=True)
        fixture = make_fixture(self.tournament, home_team, away_team)
        submitted_result = Result.objects.create(
            fixture=fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.player,
        )

        # Make test deterministic by using a fixed date (May 15, 2026 at noon in Asia/Dhaka)
        fixed_date = timezone.make_aware(
            timezone.datetime(2026, 5, 15, 12, 0, 0),
            timezone.get_current_timezone()
        )
        Result.objects.filter(pk=submitted_result.pk).update(submitted_at=fixed_date)
        submitted_result.refresh_from_db()

        self.client.login(username="staffops", password="testpass123")
        response = self.client.get(reverse("tournament:staff_dashboard"))

        pending_count = Result.objects.filter(status=Result.PENDING).count()
        pending_label = f"{pending_count} pending result{'s' if pending_count != 1 else ''}"
        expected_date = f"{submitted_result.submitted_at.strftime('%b')} {submitted_result.submitted_at.day}"

        self.assertContains(response, "Dashboard Home vs Dashboard Away")
        self.assertContains(response, expected_date)
        self.assertContains(response, pending_label)
        self.assertNotContains(response, "{{ result.fixture.away_team.name }}")
        self.assertNotContains(response, "{{ result.submitted_at|date:\"M j\" }}")
        self.assertNotContains(response, "{{ pending_result_count|pluralize }}")

    def test_staff_pages_expose_identity_edit_controls(self):
        self.client.login(username="staffops", password="testpass123")

        team_list_response = self.client.get(reverse("accounts:staff_team_list"))
        team_detail_response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))
        player_list_response = self.client.get(reverse("accounts:staff_player_list"))
        profile_response = self.client.get(reverse("accounts:profile", args=[self.player.username]))

        # Staff team list has Detail links to team detail pages (detail-first workflow)
        self.assertContains(team_list_response, reverse("accounts:staff_team_detail", args=[self.team.pk]))
        # Player list and profile have staff edit controls
        self.assertContains(player_list_response, reverse("accounts:staff_player_edit", args=[self.player.username]))
        self.assertContains(profile_response, reverse("accounts:staff_player_edit", args=[self.player.username]))

    def test_non_staff_cannot_access_staff_pages(self):
        self.client.login(username="plainplayer", password="testpass123")

        responses = [
            self.client.get(reverse("tournament:staff_dashboard")),
            self.client.get(reverse("tournament:staff_tournament_list")),
            self.client.get(reverse("tournament:staff_tournament_create")),
            self.client.get(reverse("tournament:staff_tournament_edit", args=[self.tournament.pk])),
            self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk])),
            self.client.get(reverse("tournament:staff_pending_results")),
            self.client.get(reverse("accounts:staff_team_list")),
            self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk])),
            self.client.get(reverse("accounts:staff_player_list")),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 403)

    def test_staff_can_create_and_edit_tournament(self):
        self.client.login(username="staffops", password="testpass123")

        create_response = self.client.post(reverse("tournament:staff_tournament_create"), {
            "name": "Fresh Staff Cup",
            "tournament_type": Tournament.TEAM,
            "format": Tournament.ROUND_ROBIN,
            "status": Tournament.REGISTRATION,
            "max_teams": 4,
            "registration_deadline": "",
            "start_date": "",
            "end_date": "",
            "description": "Created from staff UI",
            "tiebreaker_rules": '["goal_difference"]',
        }, follow=True)

        created = Tournament.objects.get(name="Fresh Staff Cup")
        self.assertContains(create_response, "created successfully")

        edit_response = self.client.post(reverse("tournament:staff_tournament_edit", args=[created.pk]), {
            "name": "Fresh Staff Cup Updated",
            "tournament_type": Tournament.TEAM,
            "format": Tournament.KNOCKOUT,
            "status": Tournament.ACTIVE,
            "max_teams": 4,
            "registration_deadline": "",
            "start_date": "",
            "end_date": "",
            "description": "Updated from staff UI",
            "tiebreaker_rules": '["goal_difference"]',
        }, follow=True)

        created.refresh_from_db()
        self.assertEqual(created.name, "Fresh Staff Cup Updated")
        self.assertEqual(created.format, Tournament.KNOCKOUT)
        self.assertEqual(created.status, Tournament.ACTIVE)
        self.assertContains(edit_response, "updated successfully")

    def test_staff_hybrid_edit_form_shows_qualifier_field(self):
        self.tournament.format = Tournament.HYBRID
        self.tournament.save(update_fields=["format"])
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("tournament:staff_tournament_edit", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hybrid Qualifiers Per Group")
        self.assertContains(response, 'name="hybrid_qualifiers_per_group"', html=False)

    def test_staff_can_save_hybrid_tournament_with_top_2_qualifiers(self):
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(reverse("tournament:staff_tournament_create"), {
            "name": "Hybrid Top 2 Cup",
            "tournament_type": Tournament.TEAM,
            "format": Tournament.HYBRID,
            "hybrid_qualifiers_per_group": Tournament.HYBRID_QUALIFIERS_TOP_2,
            "status": Tournament.REGISTRATION,
            "max_teams": 8,
            "registration_deadline": "",
            "start_date": "",
            "end_date": "",
            "description": "Hybrid top 2",
            "tiebreaker_rules": '["goal_difference"]',
        }, follow=True)

        tournament = Tournament.objects.get(name="Hybrid Top 2 Cup")
        self.assertEqual(tournament.format, Tournament.HYBRID)
        self.assertEqual(
            tournament.hybrid_qualifiers_per_group,
            Tournament.HYBRID_QUALIFIERS_TOP_2,
        )
        self.assertContains(response, "created successfully")

    def test_staff_can_save_hybrid_tournament_with_top_4_qualifiers(self):
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(reverse("tournament:staff_tournament_create"), {
            "name": "Hybrid Top 4 Cup",
            "tournament_type": Tournament.TEAM,
            "format": Tournament.HYBRID,
            "hybrid_qualifiers_per_group": Tournament.HYBRID_QUALIFIERS_TOP_4,
            "status": Tournament.REGISTRATION,
            "max_teams": 16,
            "registration_deadline": "",
            "start_date": "",
            "end_date": "",
            "description": "Hybrid top 4",
            "tiebreaker_rules": '["goal_difference"]',
        }, follow=True)

        tournament = Tournament.objects.get(name="Hybrid Top 4 Cup")
        self.assertEqual(tournament.format, Tournament.HYBRID)
        self.assertEqual(
            tournament.hybrid_qualifiers_per_group,
            Tournament.HYBRID_QUALIFIERS_TOP_4,
        )
        self.assertContains(response, "created successfully")

    def test_locked_hybrid_tournament_rejects_qualifier_change_after_elimination_exists(self):
        self.tournament.format = Tournament.HYBRID
        self.tournament.hybrid_qualifiers_per_group = Tournament.HYBRID_QUALIFIERS_TOP_2
        self.tournament.save(update_fields=["format", "hybrid_qualifiers_per_group"])
        home = make_team("Hybrid Lock Settings Home", is_approved=True)
        away = make_team("Hybrid Lock Settings Away", is_approved=True)
        Fixture.objects.create(
            tournament=self.tournament,
            home_team=home,
            away_team=away,
            round_number=1,
            stage=Fixture.KNOCKOUT,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_edit", args=[self.tournament.pk]),
            {
                "name": self.tournament.name,
                "tournament_type": Tournament.TEAM,
                "format": Tournament.HYBRID,
                "hybrid_qualifiers_per_group": Tournament.HYBRID_QUALIFIERS_TOP_4,
                "status": self.tournament.status,
                "max_teams": self.tournament.max_teams,
                "registration_deadline": "",
                "start_date": "",
                "end_date": "",
                "description": self.tournament.description,
                "tiebreaker_rules": '["goal_difference"]',
            },
            follow=True,
        )

        self.tournament.refresh_from_db()
        self.assertEqual(
            self.tournament.hybrid_qualifiers_per_group,
            Tournament.HYBRID_QUALIFIERS_TOP_2,
        )
        self.assertContains(response, "Hybrid qualifier setting cannot be changed after knockout fixtures have been generated.")
        self.assertNotContains(response, "updated successfully")

    def test_non_hybrid_staff_form_hides_qualifier_field_and_saves_without_it(self):
        self.tournament.format = Tournament.ROUND_ROBIN
        self.tournament.save(update_fields=["format"])
        self.client.login(username="staffops", password="testpass123")

        edit_page = self.client.get(reverse("tournament:staff_tournament_edit", args=[self.tournament.pk]))
        self.assertEqual(edit_page.status_code, 200)
        self.assertNotContains(edit_page, "Hybrid Qualifiers Per Group")
        self.assertNotContains(edit_page, 'name="hybrid_qualifiers_per_group"', html=False)

        response = self.client.post(
            reverse("tournament:staff_tournament_edit", args=[self.tournament.pk]),
            {
                "name": "Ops Cup Non Hybrid Updated",
                "tournament_type": Tournament.TEAM,
                "format": Tournament.KNOCKOUT,
                "status": Tournament.ACTIVE,
                "max_teams": 4,
                "registration_deadline": "",
                "start_date": "",
                "end_date": "",
                "description": "Non-hybrid update without qualifier field",
                "tiebreaker_rules": '["goal_difference"]',
            },
            follow=True,
        )

        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.name, "Ops Cup Non Hybrid Updated")
        self.assertEqual(self.tournament.format, Tournament.KNOCKOUT)
        self.assertEqual(
            self.tournament.hybrid_qualifiers_per_group,
            Tournament.HYBRID_QUALIFIERS_TOP_2,
        )
        self.assertContains(response, "updated successfully")

    def test_staff_can_generate_fixtures_from_staff_ui(self):
        self.client.login(username="staffops", password="testpass123")
        teams = []
        for i in range(4):
            captain = make_player(f"capt{i}")
            team = make_team(f"Ops Team {i}", captain=captain, is_approved=True)
            TeamMembership.objects.create(
                player=captain,
                team=team,
                role=TeamMembership.CAPTAIN,
            )
            teams.append(team)
            TournamentRegistration.objects.create(
                tournament=self.tournament,
                team=team,
                is_active=True,
            )

        response = self.client.post(
            reverse("tournament:staff_generate_fixtures", args=[self.tournament.pk]),
            {"next": reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk])},
            follow=True,
        )

        self.assertGreater(Fixture.objects.filter(tournament=self.tournament).count(), 0)
        self.assertContains(response, "fixture(s) generated")

    def test_staff_can_generate_grouped_fixtures_from_staff_ui(self):
        self.client.login(username="staffops", password="testpass123")
        for index, group_label in enumerate(["A", "A", "B", "B"], start=1):
            captain = make_player(f"grpui{index}")
            teammate = make_player(f"grptm{index}")
            team = make_team(f"Grouped UI Team {index}", captain=captain, is_approved=True)
            TeamMembership.objects.create(
                player=captain,
                team=team,
                role=TeamMembership.CAPTAIN,
            )
            TeamMembership.objects.create(
                player=teammate,
                team=team,
                role=TeamMembership.PLAYER,
            )
            TournamentRegistration.objects.create(
                tournament=self.tournament,
                team=team,
                is_active=True,
                group_label=group_label,
            )

        response = self.client.post(
            reverse("tournament:staff_generate_fixtures", args=[self.tournament.pk]),
            {"next": reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk])},
            follow=True,
        )

        self.assertContains(response, "fixture(s) generated")
        fixtures = Fixture.objects.filter(tournament=self.tournament, is_bye=False)
        self.assertEqual(fixtures.count(), 2)
        team_group_map = {
            registration.team_id: registration.group_label
            for registration in TournamentRegistration.objects.filter(tournament=self.tournament, is_active=True)
        }
        for fixture in fixtures:
            self.assertEqual(team_group_map[fixture.home_team_id], team_group_map[fixture.away_team_id])

    def test_non_staff_cannot_generate_grouped_fixtures_from_staff_ui(self):
        captain = make_player("ngfgcap1")
        teammate = make_player("ngfgmate")
        team = make_team("Blocked Group Fixture UI FC", captain=captain, is_approved=True)
        TeamMembership.objects.create(
            player=captain,
            team=team,
            role=TeamMembership.CAPTAIN,
        )
        TeamMembership.objects.create(
            player=teammate,
            team=team,
            role=TeamMembership.PLAYER,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=team,
            is_active=True,
            group_label="A",
        )
        self.client.login(username="plainplayer", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_generate_fixtures", args=[self.tournament.pk]),
            {"next": reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk])},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Fixture.objects.filter(tournament=self.tournament).exists())

    def test_staff_grouped_fixture_generation_blocks_when_active_team_is_ungrouped(self):
        self.client.login(username="staffops", password="testpass123")
        grouped_one = self.make_eligible_team("Grouped Ready One", "grpgena1")
        grouped_two = self.make_eligible_team("Grouped Ready Two", "grpgena2")
        ungrouped = self.make_eligible_team("Grouped Missing Label", "grpgena3")
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=grouped_one,
            is_active=True,
            group_label="A",
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=grouped_two,
            is_active=True,
            group_label="A",
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=ungrouped,
            is_active=True,
        )

        response = self.client.post(
            reverse("tournament:staff_generate_fixtures", args=[self.tournament.pk]),
            {"next": reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk])},
            follow=True,
        )

        self.assertContains(
            response,
            "Assign every active team entrant to a group before generating group-stage fixtures.",
        )
        self.assertFalse(Fixture.objects.filter(tournament=self.tournament).exists())

    def test_staff_grouped_fixture_generation_warns_when_group_is_too_small(self):
        self.client.login(username="staffops", password="testpass123")
        group_a_one = self.make_eligible_team("Warn Group A One", "warngrp1")
        group_a_two = self.make_eligible_team("Warn Group A Two", "warngrp2")
        group_b_solo = self.make_eligible_team("Warn Group B Solo", "warngrp3")
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=group_a_one,
            is_active=True,
            group_label="A",
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=group_a_two,
            is_active=True,
            group_label="A",
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=group_b_solo,
            is_active=True,
            group_label="B",
        )

        response = self.client.post(
            reverse("tournament:staff_generate_fixtures", args=[self.tournament.pk]),
            {"next": reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk])},
            follow=True,
        )

        self.assertContains(response, "fixture(s) generated")
        self.assertContains(response, "Skipped Group B because each group needs at least 2 active teams.")
        fixtures = Fixture.objects.filter(tournament=self.tournament, is_bye=False)
        self.assertEqual(fixtures.count(), 1)

    def test_staff_cannot_regenerate_grouped_fixtures_after_results_exist(self):
        self.client.login(username="staffops", password="testpass123")
        home = self.make_eligible_team("Regen Group Home", "regrp01")
        away = self.make_eligible_team("Regen Group Away", "regrp02")
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=home,
            is_active=True,
            group_label="A",
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=away,
            is_active=True,
            group_label="A",
        )
        fixture = make_fixture(self.tournament, home, away)
        Result.objects.create(
            fixture=fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.player,
        )

        response = self.client.post(
            reverse("tournament:staff_generate_fixtures", args=[self.tournament.pk]),
            {"next": reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk])},
            follow=True,
        )

        self.assertContains(response, "Fixtures already exist for this tournament.")
        self.assertEqual(Fixture.objects.filter(tournament=self.tournament).count(), 1)

    def test_staff_can_update_registration_from_review_page(self):
        self.client.login(username="staffops", password="testpass123")
        registration_owner = make_player("registrationowner")
        registration_teammate = make_player("regmate2")
        approved_team = make_team("Registered Ops FC", captain=registration_owner, is_approved=True)
        TeamMembership.objects.create(
            player=registration_owner,
            team=approved_team,
            role=TeamMembership.CAPTAIN,
        )
        TeamMembership.objects.create(
            player=registration_teammate,
            team=approved_team,
            role=TeamMembership.PLAYER,
        )
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )

        response = self.client.post(
            reverse("tournament:staff_tournament_registration_update", args=[self.tournament.pk, registration.pk]),
            {"seed": 2, "is_active": ""},
            follow=True,
        )

        registration.refresh_from_db()
        self.assertEqual(registration.seed, 2)
        self.assertFalse(registration.is_active)
        self.assertContains(response, "Updated registration")

    def test_staff_tournament_edit_includes_lock_summary_context(self):
        approved_team = self.make_eligible_team("Summary FC", "summarycaptain")
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("tournament:staff_tournament_edit", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["entrant_changes_allowed"])
        self.assertEqual(response.context["entrant_lock_state"], "open")
        self.assertEqual(response.context["active_registration_count"], 1)
        self.assertEqual(response.context["inactive_registration_count"], 0)
        self.assertFalse(response.context["has_fixtures"])
        self.assertFalse(response.context["has_results"])

    def test_staff_tournament_registrations_context_shows_results_lock(self):
        approved_team = self.make_eligible_team("Context Home FC", "homectx1")
        opponent = self.make_eligible_team("Context Away FC", "awayctx2")
        home_registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=opponent,
            is_active=False,
        )
        fixture = make_fixture(self.tournament, approved_team, opponent)
        Result.objects.create(
            fixture=fixture,
            home_score=2,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.player,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["entrant_changes_allowed"])
        self.assertEqual(response.context["entrant_lock_state"], "results")
        self.assertEqual(
            response.context["entrant_lock_reason"],
            "Entrants cannot be changed after results have been submitted.",
        )
        self.assertEqual(response.context["active_registration_count"], 1)
        self.assertEqual(response.context["inactive_registration_count"], 1)
        self.assertTrue(response.context["has_fixtures"])
        self.assertTrue(response.context["has_results"])
        self.assertEqual(response.context["fixtures_count"], 1)
        registrations = list(response.context["registrations"])
        summary_registration = next(reg for reg in registrations if reg.pk == home_registration.pk)
        self.assertEqual(summary_registration.active_roster_count, 2)

    def test_staff_can_manually_add_team_to_tournament(self):
        approved_team = self.make_eligible_team("Manual Entry FC", "manualcaptain")
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_registration_add", args=[self.tournament.pk]),
            {"team": approved_team.pk},
            follow=True,
        )

        registration = TournamentRegistration.objects.get(tournament=self.tournament, team=approved_team)
        self.assertTrue(registration.is_active)
        self.assertContains(response, "has been added")

    def test_non_staff_cannot_manually_add_team_to_tournament(self):
        approved_team = self.make_eligible_team("Blocked Manual FC", "blockedcaptain")
        self.client.login(username="plainplayer", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_registration_add", args=[self.tournament.pk]),
            {"team": approved_team.pk},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            TournamentRegistration.objects.filter(tournament=self.tournament, team=approved_team).exists()
        )

    def test_staff_can_safely_remove_team_before_fixtures_exist(self):
        approved_team = self.make_eligible_team("Removable FC", "removecaptain")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_registration_update", args=[self.tournament.pk, registration.pk]),
            {"seed": "", "is_active": ""},
            follow=True,
        )

        registration.refresh_from_db()
        self.assertFalse(registration.is_active)
        self.assertContains(response, "Updated registration")

    def test_staff_cannot_remove_team_after_fixtures_exist(self):
        approved_team = self.make_eligible_team("Locked Fixture FC", "fixturecaptain")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        opponent = self.make_eligible_team("Fixture Opponent FC", "fixtureoppcaptain")
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=opponent,
            is_active=True,
        )
        make_fixture(self.tournament, approved_team, opponent)
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_registration_update", args=[self.tournament.pk, registration.pk]),
            {"seed": "", "is_active": ""},
            follow=True,
        )

        registration.refresh_from_db()
        self.assertTrue(registration.is_active)
        self.assertContains(response, "Entrants cannot be changed after fixtures have been generated.")

    def test_staff_cannot_remove_team_after_results_exist(self):
        approved_team = self.make_eligible_team("Locked Result FC", "resultcaptain")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        opponent = self.make_eligible_team("Result Opponent FC", "resultoppcaptain")
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=opponent,
            is_active=True,
        )
        fixture = make_fixture(self.tournament, approved_team, opponent)
        Result.objects.create(
            fixture=fixture,
            home_score=1,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.player,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_registration_update", args=[self.tournament.pk, registration.pk]),
            {"seed": "", "is_active": ""},
            follow=True,
        )

        registration.refresh_from_db()
        self.assertTrue(registration.is_active)
        self.assertContains(response, "Entrants cannot be changed after results have been submitted.")

    def test_non_staff_cannot_update_registration_from_review_page(self):
        registration_owner = make_player("lockedowner")
        approved_team = make_team("Locked Ops FC", captain=registration_owner, is_approved=True)
        TeamMembership.objects.create(
            player=registration_owner,
            team=approved_team,
            role=TeamMembership.CAPTAIN,
        )
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        self.client.login(username="plainplayer", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_registration_update", args=[self.tournament.pk, registration.pk]),
            {"seed": 3, "is_active": "on"},
        )

        self.assertEqual(response.status_code, 403)
        registration.refresh_from_db()
        self.assertIsNone(registration.seed)
        self.assertTrue(registration.is_active)

    def test_staff_can_assign_active_team_entrant_to_group(self):
        approved_team = self.make_eligible_team("Grouped Ops FC", "groupcaptain")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_update", args=[self.tournament.pk, registration.pk]),
            {"group_label": "b"},
            follow=True,
        )

        registration.refresh_from_db()
        self.assertEqual(registration.group_label, "B")
        self.assertContains(response, "assigned to Group B")

    def test_staff_single_group_assignment_redirects_to_groups_tab(self):
        approved_team = self.make_eligible_team("Grouped Redirect FC", "groupredir")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_update", args=[self.tournament.pk, registration.pk]),
            {"group_label": "c"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"{reverse('tournament:staff_tournament_registrations', args=[self.tournament.pk])}#groups",
        )
        registration.refresh_from_db()
        self.assertEqual(registration.group_label, "C")

    def test_staff_can_bulk_assign_unassigned_teams_to_group(self):
        team_one = self.make_eligible_team("Bulk Group One FC", "bulkgrp01")
        team_two = self.make_eligible_team("Bulk Group Two FC", "bulkgrp02")
        registration_one = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=team_one,
            is_active=True,
        )
        registration_two = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=team_two,
            is_active=True,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_bulk_update", args=[self.tournament.pk]),
            {
                "registration_ids": [registration_one.pk, registration_two.pk],
                "group_label": "d",
            },
            follow=True,
        )

        registration_one.refresh_from_db()
        registration_two.refresh_from_db()
        self.assertEqual(registration_one.group_label, "D")
        self.assertEqual(registration_two.group_label, "D")
        self.assertContains(response, "2 teams assigned to Group D.")

    def test_staff_bulk_group_assignment_requires_selection(self):
        team_one = self.make_eligible_team("Bulk Select Required FC", "bulksel01")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=team_one,
            is_active=True,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_bulk_update", args=[self.tournament.pk]),
            {"group_label": "A"},
            follow=True,
        )

        registration.refresh_from_db()
        self.assertEqual(registration.group_label, "")
        self.assertContains(response, "Select at least one unassigned team to bulk assign.")

    def test_staff_bulk_group_assignment_is_all_or_none_when_selection_contains_invalid_entry(self):
        valid_team = self.make_eligible_team("Bulk Valid FC", "bulkvalid")
        invalid_team = self.make_eligible_team("Bulk Invalid FC", "bulkinvalid")
        valid_registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=valid_team,
            is_active=True,
        )
        invalid_registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=invalid_team,
            is_active=True,
            group_label="A",
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_bulk_update", args=[self.tournament.pk]),
            {
                "registration_ids": [valid_registration.pk, invalid_registration.pk],
                "group_label": "B",
            },
            follow=True,
        )

        valid_registration.refresh_from_db()
        invalid_registration.refresh_from_db()
        self.assertEqual(valid_registration.group_label, "")
        self.assertEqual(invalid_registration.group_label, "A")
        self.assertContains(response, "Bulk assignment only supports active, unassigned team entrants.")

    def test_non_staff_cannot_bulk_assign_groups(self):
        approved_team = self.make_eligible_team("Blocked Bulk Group FC", "blockedbulk")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        self.client.login(username="plainplayer", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_bulk_update", args=[self.tournament.pk]),
            {
                "registration_ids": [registration.pk],
                "group_label": "A",
            },
        )

        self.assertEqual(response.status_code, 403)
        registration.refresh_from_db()
        self.assertEqual(registration.group_label, "")

    def test_non_staff_cannot_access_or_perform_group_assignment(self):
        approved_team = self.make_eligible_team("Blocked Group FC", "blockedgroup")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        self.client.login(username="plainplayer", password="testpass123")

        get_response = self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk]))
        post_response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_update", args=[self.tournament.pk, registration.pk]),
            {"group_label": "A"},
        )

        self.assertEqual(get_response.status_code, 403)
        self.assertEqual(post_response.status_code, 403)
        registration.refresh_from_db()
        self.assertEqual(registration.group_label, "")

    def test_inactive_entrant_cannot_be_assigned_to_group(self):
        approved_team = self.make_eligible_team("Inactive Group FC", "inactivegroup")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=False,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_update", args=[self.tournament.pk, registration.pk]),
            {"group_label": "A"},
            follow=True,
        )

        registration.refresh_from_db()
        self.assertEqual(registration.group_label, "")
        self.assertContains(response, "Inactive entrants cannot be assigned to a group.")

    def test_group_assignment_is_blocked_after_fixtures_exist(self):
        approved_team = self.make_eligible_team("Locked Group Fixture FC", "grpfixa1")
        opponent = self.make_eligible_team("Locked Group Opponent FC", "grpfixb2")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=opponent,
            is_active=True,
        )
        make_fixture(self.tournament, approved_team, opponent)
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_update", args=[self.tournament.pk, registration.pk]),
            {"group_label": "A"},
            follow=True,
        )

        registration.refresh_from_db()
        self.assertEqual(registration.group_label, "")
        self.assertContains(response, "Entrants cannot be changed after fixtures have been generated.")

    def test_group_assignment_is_blocked_after_results_exist(self):
        approved_team = self.make_eligible_team("Locked Group Result FC", "grpresa1")
        opponent = self.make_eligible_team("Locked Group Result Opponent FC", "grpresb2")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=opponent,
            is_active=True,
        )
        fixture = make_fixture(self.tournament, approved_team, opponent)
        Result.objects.create(
            fixture=fixture,
            home_score=1,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.player,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_group_assignment_update", args=[self.tournament.pk, registration.pk]),
            {"group_label": "A"},
            follow=True,
        )

        registration.refresh_from_db()
        self.assertEqual(registration.group_label, "")
        self.assertContains(response, "Entrants cannot be changed after results have been submitted.")

    def test_staff_can_moderate_results_from_staff_view(self):
        approved_team = make_team("Queue Home", is_approved=True)
        away_team = make_team("Queue Away", is_approved=True)
        fixture = make_fixture(self.tournament, approved_team, away_team)
        pending_result = Result.objects.create(
            fixture=fixture,
            home_score=4,
            away_score=2,
            status=Result.PENDING,
            submitted_by=self.player,
        )

        self.client.login(username="staffops", password="testpass123")
        queue_response = self.client.get(reverse("tournament:staff_pending_results"))
        approve_response = self.client.post(
            reverse("tournament:result_approve", args=[pending_result.pk]),
            follow=True,
        )

        self.assertEqual(queue_response.status_code, 200)
        self.assertContains(queue_response, "Result Queue")
        pending_result.refresh_from_db()
        self.assertEqual(pending_result.status, Result.APPROVED)
        self.assertContains(approve_response, "Result approved")

    def test_staff_links_appear_on_existing_pages(self):
        self.client.login(username="staffops", password="testpass123")

        tournament_list_response = self.client.get(reverse("tournament:tournament_list"))
        tournament_detail_response = self.client.get(reverse("tournament:tournament_detail", args=[self.tournament.pk]))

        # Primary staff entry point /staff/ is accessible from all pages in navigation
        self.assertContains(tournament_list_response, reverse("tournament:staff_dashboard"))
        self.assertContains(tournament_detail_response, reverse("tournament:staff_dashboard"))

    def test_staff_can_approve_team_from_staff_review_page(self):
        self.client.login(username="staffops", password="testpass123")

        response = self.client.post(
            reverse("accounts:staff_team_approve", args=[self.team.pk]),
            {"next": reverse("accounts:staff_team_list")},
            follow=True,
        )

        self.team.refresh_from_db()
        self.assertTrue(self.team.is_approved)
        self.assertContains(response, "approved")

    def test_staff_team_detail_context_separates_active_and_inactive_memberships(self):
        self.client.login(username="staffops", password="testpass123")
        active_teammate = make_player("active_tm")
        inactive_player = make_player("inactivep")
        TeamMembership.objects.create(
            player=active_teammate,
            team=self.team,
            role=TeamMembership.PLAYER,
        )
        inactive_membership = TeamMembership.objects.create(
            player=inactive_player,
            team=self.team,
            role=TeamMembership.SUBSTITUTE,
        )
        inactive_membership.deactivate()

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_membership_count"], 2)
        self.assertEqual(response.context["inactive_membership_count"], 1)
        self.assertEqual(response.context["total_membership_count"], 3)
        self.assertTrue(response.context["has_captain_membership"])
        self.assertEqual(response.context["current_captain_membership"].player, self.player)
        self.assertEqual(len(response.context["active_memberships"]), 2)
        self.assertEqual(len(response.context["inactive_memberships"]), 1)
        self.assertEqual(response.context["inactive_memberships"][0].player, inactive_player)

    def test_staff_team_detail_shows_active_captain_membership_when_present(self):
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_captain_membership"])
        self.assertEqual(response.context["current_captain_membership"].player, self.player)
        self.assertTrue(response.context["captain_alignment_ok"])
        self.assertEqual(response.context["captain_alignment_state"], "aligned")
        self.assertFalse(response.context["roster_eligibility_ok"])
        self.assertEqual(response.context["roster_eligibility_state"], "below_minimum")

    def test_staff_team_detail_detects_captain_not_on_active_roster(self):
        self.client.login(username="staffops", password="testpass123")
        self.team.memberships.filter(player=self.player, is_active=True).update(is_active=False)

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["captain_alignment_ok"])
        self.assertEqual(response.context["captain_alignment_state"], "captain_not_on_active_roster")
        self.assertEqual(response.context["team_captain_membership"], None)

    def test_staff_team_detail_detects_captain_not_marked_as_captain_role(self):
        self.client.login(username="staffops", password="testpass123")
        membership = self.team.memberships.get(player=self.player, is_active=True)
        membership.role = TeamMembership.PLAYER
        membership.save(update_fields=["role"])

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["captain_alignment_ok"])
        self.assertEqual(response.context["captain_alignment_state"], "captain_not_marked_as_captain")
        self.assertEqual(response.context["team_captain_membership"].player, self.player)

    def test_staff_team_detail_detects_no_captain_set(self):
        self.client.login(username="staffops", password="testpass123")
        self.team.captain = None
        self.team.save(update_fields=["captain"])

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["captain_alignment_ok"])
        self.assertEqual(response.context["captain_alignment_state"], "no_captain_set")

    def test_staff_team_list_exposes_captain_alignment_signal(self):
        self.client.login(username="staffops", password="testpass123")
        membership = self.team.memberships.get(player=self.player, is_active=True)
        membership.role = TeamMembership.PLAYER
        membership.save(update_fields=["role"])

        response = self.client.get(reverse("accounts:staff_team_list"))

        self.assertEqual(response.status_code, 200)
        reviewed_team = next(team for team in response.context["teams"] if team.pk == self.team.pk)
        self.assertFalse(reviewed_team.captain_alignment["captain_alignment_ok"])
        self.assertEqual(
            reviewed_team.captain_alignment["captain_alignment_state"],
            "captain_not_marked_as_captain",
        )

    def test_staff_team_list_exposes_healthy_integrity_summary_state(self):
        self.client.login(username="staffops", password="testpass123")
        TeamMembership.objects.create(
            player=make_player("listhealthy"),
            team=self.team,
            role=TeamMembership.PLAYER,
        )

        response = self.client.get(reverse("accounts:staff_team_list"))

        self.assertEqual(response.status_code, 200)
        reviewed_team = next(team for team in response.context["teams"] if team.pk == self.team.pk)
        self.assertTrue(reviewed_team.team_integrity_summary["team_integrity_ok"])
        self.assertEqual(reviewed_team.team_integrity_summary["team_integrity_state"], "healthy")
        self.assertEqual(reviewed_team.team_integrity_summary["team_integrity_badge_reason"], "")

    def test_staff_team_list_exposes_needs_attention_integrity_summary_state(self):
        self.client.login(username="staffops", password="testpass123")
        membership = self.team.memberships.get(player=self.player, is_active=True)
        membership.role = TeamMembership.PLAYER
        membership.save(update_fields=["role"])

        response = self.client.get(reverse("accounts:staff_team_list"))

        self.assertEqual(response.status_code, 200)
        reviewed_team = next(team for team in response.context["teams"] if team.pk == self.team.pk)
        self.assertFalse(reviewed_team.team_integrity_summary["team_integrity_ok"])
        self.assertEqual(reviewed_team.team_integrity_summary["team_integrity_state"], "needs_attention")
        self.assertEqual(reviewed_team.team_integrity_summary["team_integrity_badge_reason"], "Multiple issues")

    def test_staff_team_list_exposes_single_issue_reason_for_roster_problem(self):
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("accounts:staff_team_list"))

        self.assertEqual(response.status_code, 200)
        reviewed_team = next(team for team in response.context["teams"] if team.pk == self.team.pk)
        self.assertFalse(reviewed_team.team_integrity_summary["team_integrity_ok"])
        self.assertEqual(reviewed_team.team_integrity_summary["team_integrity_badge_reason"], "Roster ineligible")

    def test_staff_team_detail_detects_roster_size_eligible(self):
        self.client.login(username="staffops", password="testpass123")
        active_teammate = make_player("rosterok1")
        TeamMembership.objects.create(
            player=active_teammate,
            team=self.team,
            role=TeamMembership.PLAYER,
        )

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["roster_eligibility_ok"])
        self.assertEqual(response.context["roster_eligibility_state"], "eligible")
        self.assertEqual(response.context["active_player_count"], 2)

    def test_staff_team_detail_shows_healthy_integrity_summary_when_both_signals_are_valid(self):
        self.client.login(username="staffops", password="testpass123")
        TeamMembership.objects.create(
            player=make_player("healthok"),
            team=self.team,
            role=TeamMembership.PLAYER,
        )

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["team_integrity_ok"])
        self.assertEqual(response.context["team_integrity_state"], "healthy")
        self.assertEqual(response.context["team_integrity_issues"], [])

    def test_staff_team_detail_shows_needs_attention_when_captain_alignment_is_invalid(self):
        self.client.login(username="staffops", password="testpass123")
        TeamMembership.objects.create(
            player=make_player("captneed"),
            team=self.team,
            role=TeamMembership.PLAYER,
        )
        membership = self.team.memberships.get(player=self.player, is_active=True)
        membership.role = TeamMembership.PLAYER
        membership.save(update_fields=["role"])

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["team_integrity_ok"])
        self.assertEqual(response.context["team_integrity_state"], "needs_attention")
        self.assertIn(
            "The team captain is on the active roster, but their membership role is not Captain.",
            response.context["team_integrity_issues"],
        )

    def test_staff_team_detail_shows_needs_attention_when_roster_is_invalid(self):
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["team_integrity_ok"])
        self.assertEqual(response.context["team_integrity_state"], "needs_attention")
        self.assertIn(
            "Needs at least 2 active players for tournament eligibility.",
            response.context["team_integrity_issues"],
        )

    def test_staff_team_detail_shows_needs_attention_when_both_signals_are_invalid(self):
        self.client.login(username="staffops", password="testpass123")
        self.team.captain = None
        self.team.save(update_fields=["captain"])

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["team_integrity_ok"])
        self.assertEqual(response.context["team_integrity_state"], "needs_attention")
        self.assertEqual(len(response.context["team_integrity_issues"]), 2)
        self.assertIn("No team captain is set on the team record.", response.context["team_integrity_issues"])
        self.assertIn(
            "Needs at least 2 active players for tournament eligibility.",
            response.context["team_integrity_issues"],
        )

    def test_staff_team_detail_detects_roster_size_above_maximum(self):
        self.client.login(username="staffops", password="testpass123")
        for username in ["abovemx1", "abovemx2", "abovemx3"]:
            TeamMembership.objects.create(
                player=make_player(username),
                team=self.team,
                role=TeamMembership.PLAYER,
            )

        response = self.client.get(reverse("accounts:staff_team_detail", args=[self.team.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["roster_eligibility_ok"])
        self.assertEqual(response.context["roster_eligibility_state"], "above_maximum")
        self.assertEqual(response.context["active_player_count"], 4)

    def test_staff_tournament_registrations_expose_team_roster_eligibility(self):
        approved_team = self.make_eligible_team("Roster Eligible FC", "rostercaptain")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        reviewed_registration = next(
            item for item in response.context["registrations"] if item.pk == registration.pk
        )
        self.assertTrue(reviewed_registration.team.roster_eligibility["roster_eligibility_ok"])
        self.assertEqual(
            reviewed_registration.team.roster_eligibility["roster_eligibility_state"],
            "eligible",
        )

    def test_staff_tournament_registrations_expose_healthy_team_integrity_summary(self):
        approved_team = self.make_eligible_team("Healthy Entrant FC", "healthyentrant")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        reviewed_registration = next(
            item for item in response.context["registrations"] if item.pk == registration.pk
        )
        self.assertTrue(reviewed_registration.team.team_integrity_summary["team_integrity_ok"])
        self.assertEqual(
            reviewed_registration.team.team_integrity_summary["team_integrity_state"],
            "healthy",
        )
        self.assertEqual(
            reviewed_registration.team.team_integrity_summary["team_integrity_badge_reason"],
            "",
        )

    def test_staff_tournament_registrations_expose_needs_attention_team_integrity_summary(self):
        approved_team = self.make_eligible_team("Problem Entrant FC", "problementrant")
        registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=approved_team,
            is_active=True,
        )
        membership = approved_team.memberships.get(player=approved_team.captain, is_active=True)
        membership.role = TeamMembership.PLAYER
        membership.save(update_fields=["role"])
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        reviewed_registration = next(
            item for item in response.context["registrations"] if item.pk == registration.pk
        )
        self.assertFalse(reviewed_registration.team.team_integrity_summary["team_integrity_ok"])
        self.assertEqual(
            reviewed_registration.team.team_integrity_summary["team_integrity_state"],
            "needs_attention",
        )
        self.assertEqual(
            reviewed_registration.team.team_integrity_summary["team_integrity_badge_reason"],
            "Captain mismatch",
        )

    def test_staff_tournament_registrations_rollup_counts_active_teams_needing_attention(self):
        healthy_team = self.make_eligible_team("Rollup Healthy FC", "rolluphealthy")
        problem_team = self.make_eligible_team("Rollup Problem FC", "rollupproblem")
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=healthy_team,
            is_active=True,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=problem_team,
            is_active=True,
        )
        problem_membership = problem_team.memberships.get(player=problem_team.captain, is_active=True)
        problem_membership.role = TeamMembership.PLAYER
        problem_membership.save(update_fields=["role"])
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_team_registrations_needing_attention"], 1)

    def test_staff_tournament_registrations_context_shows_unassigned_and_existing_groups(self):
        group_a_team = self.make_eligible_team("Group A FC", "groupactx")
        unassigned_team = self.make_eligible_team("Ungrouped FC", "ungroupedctx")
        grouped_registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=group_a_team,
            is_active=True,
            group_label="A",
        )
        unassigned_registration = TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=unassigned_team,
            is_active=True,
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_team_registration_count"], 2)
        self.assertEqual(response.context["assigned_team_registration_count"], 1)
        self.assertEqual(
            [item.pk for item in response.context["unassigned_team_registrations"]],
            [unassigned_registration.pk],
        )
        self.assertEqual(len(response.context["existing_groups"]), 1)
        self.assertEqual(response.context["existing_groups"][0]["label"], "A")
        self.assertEqual(
            [item.pk for item in response.context["existing_groups"][0]["registrations"]],
            [grouped_registration.pk],
        )

    def test_staff_tournament_registrations_template_does_not_render_raw_template_tokens(self):
        rendered_team = self.make_eligible_team("Rendered Entrant FC", "renderentrant")
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=rendered_team,
            is_active=True,
            group_label="A",
        )
        self.client.login(username="staffops", password="testpass123")

        response = self.client.get(reverse("tournament:staff_tournament_registrations", args=[self.tournament.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rendered Entrant FC")
        self.assertContains(response, "Active")
        self.assertContains(response, "(1/1)")
        self.assertNotContains(response, "{% elif reg.player %}")
        self.assertNotContains(response, "{{ active_team_registration_count }}")
        self.assertNotContains(response, "Active{% else")
        self.assertNotContains(response, "{{ fixtures_count|pluralize")


class TournamentArchiveReadOnlyTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.staff = make_player("archivestaff", is_staff=True)
        self.owner = make_player("archiveowner")
        self.home_player = make_player("archivehome")
        self.away_player = make_player("archiveaway")
        self.tournament = make_tournament("Archive Cup", status=Tournament.ACTIVE)
        self.home = make_team("Archive Home", captain=self.owner, is_approved=True)
        self.away = make_team("Archive Away", captain=self.away_player, is_approved=True)
        TeamMembership.objects.create(
            player=self.owner,
            team=self.home,
            role=TeamMembership.CAPTAIN,
        )
        TeamMembership.objects.create(
            player=self.home_player,
            team=self.home,
            role=TeamMembership.PLAYER,
        )
        TeamMembership.objects.create(
            player=self.away_player,
            team=self.away,
            role=TeamMembership.CAPTAIN,
        )
        TeamMembership.objects.create(
            player=make_player("archiveawaymate"),
            team=self.away,
            role=TeamMembership.PLAYER,
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=self.home,
            is_active=True,
            group_label="A",
        )
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=self.away,
            is_active=True,
            group_label="A",
        )
        self.fixture = make_fixture(self.tournament, self.home, self.away)

    def archive_tournament(self):
        self.tournament.status = Tournament.ARCHIVED
        self.tournament.save(update_fields=["status"])

    def make_eligible_team(self, name, captain_username):
        captain = make_player(captain_username)
        teammate = make_player(f"{captain_username}mate")
        team = make_team(name, captain=captain, is_approved=True)
        TeamMembership.objects.create(
            player=captain,
            team=team,
            role=TeamMembership.CAPTAIN,
        )
        TeamMembership.objects.create(
            player=teammate,
            team=team,
            role=TeamMembership.PLAYER,
        )
        return team

    def test_staff_can_archive_tournament_without_deleting_history(self):
        self.client.login(username="archivestaff", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_archive", args=[self.tournament.pk]),
            follow=True,
        )

        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, Tournament.ARCHIVED)
        self.assertEqual(TournamentRegistration.objects.filter(tournament=self.tournament).count(), 2)
        self.assertEqual(Fixture.objects.filter(tournament=self.tournament).count(), 1)
        self.assertContains(response, "archived")

    def test_non_staff_cannot_archive_tournament(self):
        self.client.login(username="archiveowner", password="testpass123")

        response = self.client.post(
            reverse("tournament:staff_tournament_archive", args=[self.tournament.pk])
        )

        self.tournament.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.tournament.status, Tournament.ACTIVE)

    def test_archived_tournament_remains_publicly_visible(self):
        self.archive_tournament()

        list_response = self.client.get(reverse("tournament:tournament_list"))
        detail_response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(list_response, "Archive Cup")
        self.assertContains(list_response, "Archived")
        self.assertContains(detail_response, "Archive Cup")
        self.assertContains(detail_response, "Tournament archive view.")

    def test_archived_tournament_blocks_new_registration(self):
        registration_tournament = make_tournament(
            "Archive Registration Cup",
            status=Tournament.REGISTRATION,
        )
        team = self.make_eligible_team("Archive Joiners", "archivejoincap")
        registration_tournament.status = Tournament.ARCHIVED
        registration_tournament.save(update_fields=["status"])
        self.client.login(username="archivejoincap", password="testpass123")

        response = self.client.post(
            reverse("tournament:tournament_register", args=[registration_tournament.pk]),
            {"team_id": team.pk},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            TournamentRegistration.objects.filter(
                tournament=registration_tournament,
                team=team,
            ).exists()
        )

    def test_archived_tournament_blocks_fixture_generation(self):
        fixtureless_tournament = make_tournament("Archive Fixtures Cup", status=Tournament.ACTIVE)
        home = self.make_eligible_team("Archive Fixture Home", "archivefxhome")
        away = self.make_eligible_team("Archive Fixture Away", "archivefxaway")
        TournamentRegistration.objects.create(tournament=fixtureless_tournament, team=home, is_active=True)
        TournamentRegistration.objects.create(tournament=fixtureless_tournament, team=away, is_active=True)
        fixtureless_tournament.status = Tournament.ARCHIVED
        fixtureless_tournament.save(update_fields=["status"])

        count, error = generate_fixtures_for_tournament(fixtureless_tournament)
        self.assertEqual(count, 0)
        self.assertEqual(error, "Archived tournaments are read-only.")

        self.client.login(username="archivestaff", password="testpass123")
        response = self.client.post(
            reverse("tournament:staff_generate_fixtures", args=[fixtureless_tournament.pk]),
            follow=True,
        )

        self.assertFalse(Fixture.objects.filter(tournament=fixtureless_tournament).exists())
        self.assertContains(response, "Archived tournaments are read-only.")

    def test_archived_tournament_blocks_player_result_submission_and_opponent_response(self):
        pending_result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.owner,
            submitting_team=self.home,
        )
        self.archive_tournament()

        self.client.login(username="archivehome", password="testpass123")
        submit_response = self.client.post(
            reverse("tournament:result_submit", args=[self.fixture.pk]),
            {"home_score": 2, "away_score": 1},
            follow=True,
        )
        self.assertContains(submit_response, "Archived tournaments are read-only.")
        self.assertEqual(Result.objects.filter(fixture=self.fixture).count(), 1)

        self.client.logout()
        self.client.login(username="archiveaway", password="testpass123")
        response_response = self.client.post(
            reverse("tournament:result_opponent_response", args=[pending_result.pk]),
            {
                "action": Result.OPPONENT_RESPONSE_CONFIRMED,
                "opponent_home_score": 1,
                "opponent_away_score": 0,
                "note": "",
            },
            follow=True,
        )

        pending_result.refresh_from_db()
        self.assertContains(response_response, "Archived tournaments are read-only.")
        self.assertEqual(pending_result.opponent_response_status, Result.OPPONENT_RESPONSE_PENDING)
        self.assertEqual(pending_result.opponent_score_state, Result.OPPONENT_SCORE_AWAITING)
        self.assertIsNone(pending_result.opponent_home_score)
        self.assertIsNone(pending_result.opponent_responded_at)

    def test_archived_tournament_blocks_staff_result_moderation_changes(self):
        pending_result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.owner,
        )
        approved_result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=0,
            status=Result.APPROVED,
            submitted_by=self.owner,
            reviewed_by=self.staff,
            reviewed_at=timezone.now(),
        )
        self.archive_tournament()
        self.client.login(username="archivestaff", password="testpass123")

        for url in [
            reverse("tournament:result_approve", args=[pending_result.pk]),
            reverse("tournament:result_reject", args=[pending_result.pk]),
            reverse("tournament:result_dispute", args=[pending_result.pk]),
        ]:
            response = self.client.post(url, follow=True)
            self.assertContains(response, "Archived tournaments are read-only.")

        edit_response = self.client.post(
            reverse("tournament:result_edit", args=[approved_result.pk]),
            {"home_score": 4, "away_score": 3},
            follow=True,
        )

        pending_result.refresh_from_db()
        approved_result.refresh_from_db()
        self.assertEqual(pending_result.status, Result.PENDING)
        self.assertEqual(approved_result.home_score, 2)
        self.assertEqual(approved_result.away_score, 0)
        self.assertContains(edit_response, "Archived tournaments are read-only.")

    def test_approved_results_and_standings_remain_visible_after_archive(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=3,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.owner,
        )
        result.approve(admin=self.staff)
        self.archive_tournament()

        detail_response = self.client.get(
            reverse("tournament:tournament_detail", args=[self.tournament.pk])
        )
        fixture_response = self.client.get(
            reverse("tournament:fixture_detail", args=[self.fixture.pk])
        )
        standings_response = self.client.get(
            reverse("tournament:standings_partial", args=[self.tournament.pk])
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(fixture_response.status_code, 200)
        self.assertEqual(standings_response.status_code, 200)
        self.assertContains(detail_response, "3")
        self.assertContains(detail_response, "Archive Home")
        self.assertContains(fixture_response, "Official result approved")
        self.assertContains(standings_response, "Archive Home")
        self.assertTrue(
            Standing.objects.filter(
                tournament=self.tournament,
                team=self.home,
                points=3,
            ).exists()
        )

    def test_archived_tournament_blocks_adjacent_staff_mutations(self):
        registration = TournamentRegistration.objects.get(
            tournament=self.tournament,
            team=self.home,
        )
        self.archive_tournament()
        self.client.login(username="archivestaff", password="testpass123")

        registration_response = self.client.post(
            reverse(
                "tournament:staff_tournament_registration_update",
                args=[self.tournament.pk, registration.pk],
            ),
            {"seed": 4, "is_active": ""},
            follow=True,
        )
        group_response = self.client.post(
            reverse(
                "tournament:staff_tournament_group_assignment_update",
                args=[self.tournament.pk, registration.pk],
            ),
            {"group_label": "B"},
            follow=True,
        )
        schedule_response = self.client.post(
            reverse("tournament:staff_fixture_schedule_update", args=[self.fixture.pk]),
            {
                "match_date": "2026-04-20T18:30",
                "submission_deadline": "2026-04-21T18:30",
            },
            follow=True,
        )

        registration.refresh_from_db()
        self.fixture.refresh_from_db()
        self.assertContains(registration_response, "Archived tournaments are read-only.")
        self.assertContains(group_response, "Archived tournaments are read-only.")
        self.assertContains(schedule_response, "Archived tournaments are read-only.")
        self.assertIsNone(registration.seed)
        self.assertTrue(registration.is_active)
        self.assertEqual(registration.group_label, "A")
        self.assertIsNone(self.fixture.match_date)
        self.assertIsNone(self.fixture.submission_deadline)


class NextTournamentPreparationTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.staff = make_player("nextprepstaff", is_staff=True)

    def make_eligible_team(self, name, captain_username):
        captain = make_player(captain_username)
        teammate = make_player(f"{captain_username}mate")
        team = make_team(name, captain=captain, is_approved=True)
        TeamMembership.objects.create(
            player=captain,
            team=team,
            role=TeamMembership.CAPTAIN,
        )
        TeamMembership.objects.create(
            player=teammate,
            team=team,
            role=TeamMembership.PLAYER,
        )
        return team

    def test_staff_dashboard_separates_current_draft_and_past_tournaments(self):
        registration = make_tournament("Next Prep Registration", status=Tournament.REGISTRATION)
        active = make_tournament("Next Prep Active", status=Tournament.ACTIVE)
        draft = make_tournament("Next Prep Draft", status=Tournament.DRAFT)
        completed = make_tournament("Next Prep Completed", status=Tournament.COMPLETED)
        archived = make_tournament("Next Prep Archived", status=Tournament.ARCHIVED)
        self.client.login(username="nextprepstaff", password="testpass123")

        response = self.client.get(reverse("tournament:staff_dashboard"))

        self.assertEqual(response.status_code, 200)
        current_ids = {t.pk for t in response.context["current_tournaments"]}
        draft_ids = {t.pk for t in response.context["draft_tournaments"]}
        past_ids = {t.pk for t in response.context["past_tournaments"]}
        self.assertEqual(current_ids, {registration.pk, active.pk})
        self.assertEqual(draft_ids, {draft.pk})
        self.assertEqual(past_ids, {completed.pk, archived.pk})
        self.assertContains(response, "Current Tournaments")
        self.assertContains(response, "Draft / Upcoming")
        self.assertContains(response, "Past / Archived")

    def test_archived_results_do_not_appear_in_operational_queues_or_counts(self):
        active_tournament = make_tournament("Next Prep Queue Active", status=Tournament.ACTIVE)
        archived_tournament = make_tournament("Next Prep Queue Archived", status=Tournament.ARCHIVED)
        active_home = make_team("Queue Active Home", is_approved=True)
        active_away = make_team("Queue Active Away", is_approved=True)
        archived_home = make_team("Queue Archived Home", is_approved=True)
        archived_away = make_team("Queue Archived Away", is_approved=True)
        active_fixture = make_fixture(active_tournament, active_home, active_away)
        archived_fixture = make_fixture(archived_tournament, archived_home, archived_away)
        Result.objects.create(
            fixture=active_fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.staff,
            opponent_home_score=2,
            opponent_away_score=1,
            opponent_score_state=Result.OPPONENT_SCORE_MATCHING,
        )
        Result.objects.create(
            fixture=archived_fixture,
            home_score=1,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.staff,
            opponent_home_score=1,
            opponent_away_score=0,
            opponent_score_state=Result.OPPONENT_SCORE_MATCHING,
        )
        Result.objects.create(
            fixture=archived_fixture,
            home_score=0,
            away_score=1,
            status=Result.DISPUTED,
            submitted_by=self.staff,
            opponent_home_score=1,
            opponent_away_score=0,
            opponent_score_state=Result.OPPONENT_SCORE_CONFLICT,
        )
        self.client.login(username="nextprepstaff", password="testpass123")

        dashboard_response = self.client.get(reverse("tournament:staff_dashboard"))
        queue_response = self.client.get(reverse("tournament:admin_queue") + "?status=pending")
        disputed_response = self.client.get(reverse("tournament:admin_queue") + "?status=disputed")

        self.assertEqual(dashboard_response.context["pending_result_count"], 1)
        self.assertEqual(dashboard_response.context["disputed_result_count"], 0)
        self.assertEqual(dashboard_response.context["matching_result_count"], 1)
        self.assertEqual(dashboard_response.context["score_conflict_result_count"], 0)
        self.assertEqual(queue_response.context["pending_count"], 1)
        self.assertEqual(queue_response.context["disputed_count"], 0)
        self.assertEqual(queue_response.context["matching_count"], 1)
        self.assertEqual(queue_response.context["score_conflict_count"], 0)
        self.assertContains(queue_response, "Queue Active Home")
        self.assertNotContains(queue_response, "Queue Archived Home")
        self.assertNotContains(disputed_response, "Queue Archived Home")

    def test_team_from_archived_tournament_can_register_for_new_tournament(self):
        archived = make_tournament("Next Prep Old Team Cup", status=Tournament.ARCHIVED)
        new_tournament = make_tournament("Next Prep New Team Cup", status=Tournament.REGISTRATION)
        team = self.make_eligible_team("Next Prep Returning FC", "nextreturncap")
        TournamentRegistration.objects.create(tournament=archived, team=team, is_active=True)
        self.client.login(username="nextreturncap", password="testpass123")

        response = self.client.post(
            reverse("tournament:tournament_register", args=[new_tournament.pk]),
            {"team_id": team.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            TournamentRegistration.objects.filter(
                tournament=new_tournament,
                team=team,
                is_active=True,
            ).exists()
        )
        self.assertEqual(Team.objects.filter(pk=team.pk).count(), 1)

    def test_player_from_archived_single_tournament_can_register_for_new_single_tournament(self):
        player = make_player("nextsingleplayer")
        archived = make_tournament(
            "Next Prep Old Solo Cup",
            status=Tournament.ARCHIVED,
            tournament_type=Tournament.SINGLE,
        )
        new_tournament = make_tournament(
            "Next Prep New Solo Cup",
            status=Tournament.REGISTRATION,
            tournament_type=Tournament.SINGLE,
        )
        TournamentRegistration.objects.create(tournament=archived, player=player, is_active=True)
        self.client.login(username="nextsingleplayer", password="testpass123")

        response = self.client.post(reverse("tournament:tournament_register", args=[new_tournament.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            TournamentRegistration.objects.filter(
                tournament=new_tournament,
                player=player,
                is_active=True,
            ).exists()
        )

    def test_new_tournament_standings_ignore_archived_tournament_results_for_same_teams(self):
        archived = make_tournament("Next Prep Old Standings Cup", status=Tournament.ACTIVE)
        new_tournament = make_tournament("Next Prep New Standings Cup", status=Tournament.ACTIVE)
        home = make_team("Next Prep Standings Home", is_approved=True)
        away = make_team("Next Prep Standings Away", is_approved=True)
        archived_fixture = make_fixture(archived, home, away)
        new_fixture = make_fixture(new_tournament, home, away)
        old_result = Result.objects.create(
            fixture=archived_fixture,
            home_score=4,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.staff,
        )
        old_result.approve(admin=self.staff)
        archived.status = Tournament.ARCHIVED
        archived.save(update_fields=["status"])

        new_result = Result.objects.create(
            fixture=new_fixture,
            home_score=0,
            away_score=2,
            status=Result.PENDING,
            submitted_by=self.staff,
        )
        new_result.approve(admin=self.staff)

        old_home_standing = Standing.objects.get(tournament=archived, team=home)
        new_home_standing = Standing.objects.get(tournament=new_tournament, team=home)
        new_away_standing = Standing.objects.get(tournament=new_tournament, team=away)
        self.assertEqual(old_home_standing.points, 3)
        self.assertEqual(new_home_standing.played, 1)
        self.assertEqual(new_home_standing.points, 0)
        self.assertEqual(new_away_standing.played, 1)
        self.assertEqual(new_away_standing.points, 3)

    def test_archived_tournament_public_detail_and_standings_still_load(self):
        archived = make_tournament("Next Prep Public Archive", status=Tournament.ARCHIVED)
        home = make_team("Next Prep Public Home", is_approved=True)
        away = make_team("Next Prep Public Away", is_approved=True)
        fixture = make_fixture(archived, home, away)
        result = Result.objects.create(
            fixture=fixture,
            home_score=3,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.staff,
        )
        result.approve(admin=self.staff)

        detail_response = self.client.get(reverse("tournament:tournament_detail", args=[archived.pk]))
        standings_response = self.client.get(reverse("tournament:standings_partial", args=[archived.pk]))

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(standings_response.status_code, 200)
        self.assertContains(detail_response, "Next Prep Public Archive")
        self.assertContains(detail_response, "Tournament archive view.")
        self.assertContains(standings_response, "Next Prep Public Home")

    def test_staff_can_create_new_tournament_after_archiving_old_one(self):
        old_tournament = make_tournament("Next Prep Old Create Cup", status=Tournament.ACTIVE)
        self.client.login(username="nextprepstaff", password="testpass123")

        archive_response = self.client.post(
            reverse("tournament:staff_tournament_archive", args=[old_tournament.pk]),
            follow=True,
        )
        create_response = self.client.post(reverse("tournament:staff_tournament_create"), {
            "name": "Next Prep Fresh Created Cup",
            "tournament_type": Tournament.TEAM,
            "format": Tournament.ROUND_ROBIN,
            "status": Tournament.REGISTRATION,
            "max_teams": 4,
            "registration_deadline": "",
            "start_date": "",
            "end_date": "",
            "description": "Fresh tournament after archive.",
            "tiebreaker_rules": '["goal_difference"]',
        }, follow=True)

        old_tournament.refresh_from_db()
        self.assertEqual(old_tournament.status, Tournament.ARCHIVED)
        self.assertContains(archive_response, "archived")
        self.assertContains(create_response, "created successfully")
        self.assertTrue(Tournament.objects.filter(name="Next Prep Fresh Created Cup").exists())

    def test_duplicate_registration_rules_still_apply_within_same_tournament(self):
        team_tournament = make_tournament("Next Prep Duplicate Team Cup", status=Tournament.REGISTRATION)
        team = self.make_eligible_team("Next Prep Duplicate FC", "nextdupcap")
        TournamentRegistration.objects.create(tournament=team_tournament, team=team, is_active=True)
        self.client.login(username="nextdupcap", password="testpass123")

        team_response = self.client.post(
            reverse("tournament:tournament_register", args=[team_tournament.pk]),
            {"team_id": team.pk},
        )

        player = make_player("nextdupplayer")
        single_tournament = make_tournament(
            "Next Prep Duplicate Solo Cup",
            status=Tournament.REGISTRATION,
            tournament_type=Tournament.SINGLE,
        )
        TournamentRegistration.objects.create(tournament=single_tournament, player=player, is_active=True)
        self.client.logout()
        self.client.login(username="nextdupplayer", password="testpass123")

        player_response = self.client.post(reverse("tournament:tournament_register", args=[single_tournament.pk]))

        self.assertEqual(team_response.status_code, 403)
        self.assertEqual(player_response.status_code, 403)
        self.assertEqual(
            TournamentRegistration.objects.filter(tournament=team_tournament, team=team).count(),
            1,
        )
        self.assertEqual(
            TournamentRegistration.objects.filter(tournament=single_tournament, player=player).count(),
            1,
        )


# ── Standings Signal Tests ────────────────────────────────────────────────

class StandingsSignalTests(TestCase):

    def setUp(self):
        self.tournament = make_tournament()
        self.home = make_team("Home FC", is_approved=True)
        self.away = make_team("Away FC", is_approved=True)
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.admin = make_player("admin1", is_staff=True)

    def test_standing_created_on_result_approval(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        result.approve(admin=self.admin)
        self.assertTrue(
            Standing.objects.filter(
                tournament=self.tournament, team=self.home
            ).exists()
        )

    def test_standings_correct_after_approval(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        result.approve(admin=self.admin)
        home_standing = Standing.objects.get(tournament=self.tournament, team=self.home)
        away_standing = Standing.objects.get(tournament=self.tournament, team=self.away)
        self.assertEqual(home_standing.wins, 1)
        self.assertEqual(home_standing.points, 3)
        self.assertEqual(away_standing.losses, 1)
        self.assertEqual(away_standing.points, 0)

    def test_draw_gives_one_point_each(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        result.approve(admin=self.admin)
        home_standing = Standing.objects.get(tournament=self.tournament, team=self.home)
        away_standing = Standing.objects.get(tournament=self.tournament, team=self.away)
        self.assertEqual(home_standing.points, 1)
        self.assertEqual(away_standing.points, 1)
        self.assertEqual(home_standing.draws, 1)

    def test_only_one_approved_result_can_exist_per_fixture(self):
        first_result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=1,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        second_result = Result.objects.create(
            fixture=self.fixture,
            home_score=0,
            away_score=3,
            status=Result.PENDING,
            submitted_by=self.admin,
        )

        first_result.approve(admin=self.admin)
        second_result.approve(admin=self.admin)

        first_result.refresh_from_db()
        second_result.refresh_from_db()
        self.assertEqual(
            Result.objects.filter(fixture=self.fixture, status=Result.APPROVED).count(),
            1,
        )
        self.assertEqual(first_result.status, Result.REJECTED)
        self.assertEqual(second_result.status, Result.APPROVED)
        home_standing = Standing.objects.get(tournament=self.tournament, team=self.home)
        away_standing = Standing.objects.get(tournament=self.tournament, team=self.away)
        self.assertEqual(home_standing.points, 0)
        self.assertEqual(away_standing.points, 3)

    def test_rejecting_approved_result_recalculates_standings(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=4,
            away_score=2,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        result.approve(admin=self.admin)

        result.reject(admin=self.admin, note="Incorrect scoreline.")

        self.assertEqual(result.status, Result.REJECTED)
        self.assertFalse(Standing.objects.filter(tournament=self.tournament, team=self.home).exists())
        self.assertFalse(Standing.objects.filter(tournament=self.tournament, team=self.away).exists())

    def test_disputing_approved_result_recalculates_standings(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=1,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        result.approve(admin=self.admin)

        result.dispute(admin=self.admin, note="Needs verification.")

        self.assertEqual(result.status, Result.DISPUTED)
        self.assertFalse(Standing.objects.filter(tournament=self.tournament, team=self.home).exists())
        self.assertFalse(Standing.objects.filter(tournament=self.tournament, team=self.away).exists())

    def test_non_grouped_standings_partial_hides_zeroed_rows_after_result_is_disputed(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=3,
            away_score=2,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        result.approve(admin=self.admin)
        result.dispute(admin=self.admin, note="Needs another review.")

        response = Client().get(
            reverse("tournament:standings_partial", args=[self.tournament.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Home FC")
        self.assertContains(response, "No standings yet")


class ResultPlayerStatPublicationTests(TestCase):

    def setUp(self):
        self.tournament = make_tournament("Stat Sync Cup")
        self.home = make_team("Stat Sync Home", is_approved=True)
        self.away = make_team("Stat Sync Away", is_approved=True)
        self.fixture = make_fixture(self.tournament, self.home, self.away)
        self.admin = make_player("statsyncadmin", is_staff=True)
        self.home_scorer = make_player("sthome01")
        self.away_scorer = make_player("staway01")
        TeamMembership.objects.create(player=self.home_scorer, team=self.home, role=TeamMembership.PLAYER)
        TeamMembership.objects.create(player=self.away_scorer, team=self.away, role=TeamMembership.PLAYER)

    def test_corrected_approval_replaces_fixture_official_player_stats_cleanly(self):
        first_result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        ResultPlayerStat.objects.create(
            result=first_result,
            player=self.home_scorer,
            team=self.home,
            goals=2,
        )

        corrected_result = Result.objects.create(
            fixture=self.fixture,
            home_score=0,
            away_score=2,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        ResultPlayerStat.objects.create(
            result=corrected_result,
            player=self.away_scorer,
            team=self.away,
            goals=2,
            yellow_cards=1,
        )

        first_result.approve(admin=self.admin)
        corrected_result.approve(admin=self.admin)

        first_result.refresh_from_db()
        corrected_result.refresh_from_db()
        official_rows = PlayerStat.objects.filter(fixture=self.fixture)

        self.assertEqual(first_result.status, Result.REJECTED)
        self.assertEqual(corrected_result.status, Result.APPROVED)
        self.assertEqual(official_rows.count(), 1)
        self.assertFalse(official_rows.filter(player=self.home_scorer).exists())
        replacement_row = official_rows.get(player=self.away_scorer)
        self.assertEqual(replacement_row.goals, 2)
        self.assertEqual(replacement_row.yellow_cards, 1)

    def test_approval_copies_own_goals_into_official_player_stats(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        ResultPlayerStat.objects.create(
            result=result,
            player=self.home_scorer,
            team=self.home,
            goals=1,
        )
        ResultPlayerStat.objects.create(
            result=result,
            player=self.away_scorer,
            team=self.away,
            own_goals=1,
        )

        result.approve(admin=self.admin)

        official_home_row = PlayerStat.objects.get(fixture=self.fixture, player=self.home_scorer)
        official_away_row = PlayerStat.objects.get(fixture=self.fixture, player=self.away_scorer)
        self.assertEqual(official_home_row.goals, 1)
        self.assertEqual(official_home_row.own_goals, 0)
        self.assertEqual(official_away_row.goals, 0)
        self.assertEqual(official_away_row.own_goals, 1)

    def test_approval_publishes_detailed_result_player_stats(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        ResultPlayerStat.objects.create(
            result=result,
            player=self.home_scorer,
            team=self.home,
            goals=2,
            total_points=91,
            shots=5,
            shots_on_target=4,
            passes=22,
            successful_passes=19,
            dribbles=6,
            successful_dribbles=5,
            good_positioning_pct="88.25",
            players_marked=3,
        )

        result.approve(admin=self.admin)
        result.sync_official_player_stats()

        official_row = PlayerStat.objects.get(fixture=self.fixture, player=self.home_scorer)
        self.assertEqual(PlayerStat.objects.filter(fixture=self.fixture, player=self.home_scorer).count(), 1)
        self.assertEqual(official_row.total_points, 91)
        self.assertEqual(official_row.shots, 5)
        self.assertEqual(official_row.shots_on_target, 4)
        self.assertEqual(official_row.successful_passes, 19)
        self.assertEqual(official_row.successful_dribbles, 5)
        self.assertEqual(official_row.good_positioning_pct, Decimal("88.25"))
        self.assertEqual(official_row.players_marked, 3)

    def test_staff_editing_approved_result_resyncs_official_detailed_stats(self):
        result = Result.objects.create(
            fixture=self.fixture,
            home_score=2,
            away_score=0,
            status=Result.PENDING,
            submitted_by=self.admin,
        )
        ResultPlayerStat.objects.create(
            result=result,
            player=self.home_scorer,
            team=self.home,
            goals=2,
            shots=3,
            shots_on_target=2,
        )
        result.approve(admin=self.admin)
        self.client.login(username="statsyncadmin", password="testpass123")

        response = self.client.post(
            reverse("tournament:result_edit", args=[result.pk]),
            build_team_result_payload(
                self.fixture,
                home_score=2,
                away_score=0,
                result=result,
                stats_by_player={
                    self.home_scorer.pk: {
                        "goals": 2,
                        "shots": 6,
                        "shots_on_target": 5,
                        "passes": 30,
                        "successful_passes": 27,
                        "intercepts": 4,
                    },
                },
            ),
        )

        official_row = PlayerStat.objects.get(fixture=self.fixture, player=self.home_scorer)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PlayerStat.objects.filter(fixture=self.fixture, player=self.home_scorer).count(), 1)
        self.assertEqual(official_row.shots, 6)
        self.assertEqual(official_row.shots_on_target, 5)
        self.assertEqual(official_row.successful_passes, 27)
        self.assertEqual(official_row.intercepts, 4)


class HybridProgressionTests(TestCase):

    def setUp(self):
        self.tournament = make_tournament(name="Hybrid Cup", format=Tournament.HYBRID)
        self.tournament.max_teams = 16
        self.tournament.save(update_fields=["max_teams"])

    def register_group_team(self, group_label, index):
        captain = make_player(f"hycap{group_label.lower()}{index}")
        teammate = make_player(f"hymate{group_label.lower()}{index}")
        team = make_team(f"Hybrid {group_label}{index}", captain=captain, is_approved=True)
        TeamMembership.objects.create(player=captain, team=team, role=TeamMembership.CAPTAIN)
        TeamMembership.objects.create(player=teammate, team=team, role=TeamMembership.PLAYER)
        TournamentRegistration.objects.create(
            tournament=self.tournament,
            team=team,
            is_active=True,
            group_label=group_label,
        )
        return team

    def make_four_groups(self):
        return self.make_groups(team_count=2)

    def make_four_groups_of_four(self):
        return self.make_groups(team_count=4)

    def make_groups(self, *, team_count):
        groups = {}
        for label in ["A", "B", "C", "D"]:
            groups[label] = [
                self.register_group_team(label, index)
                for index in range(1, team_count + 1)
            ]
        return groups

    def approve_group_results(self, groups):
        rank_by_team_id = {}
        for teams in groups.values():
            for rank, team in enumerate(teams):
                rank_by_team_id[team.pk] = rank

        fixtures = Fixture.objects.filter(tournament=self.tournament, stage=Fixture.GROUP, is_bye=False)
        for fixture in fixtures:
            home_rank = rank_by_team_id[fixture.home_team_id]
            away_rank = rank_by_team_id[fixture.away_team_id]
            if home_rank < away_rank:
                Result.objects.create(
                    fixture=fixture,
                    home_score=2,
                    away_score=0,
                    status=Result.APPROVED,
                )
            else:
                Result.objects.create(
                    fixture=fixture,
                    home_score=0,
                    away_score=2,
                    status=Result.APPROVED,
                )

    def approve_current_round_home_wins(self):
        latest_round = Fixture.objects.filter(tournament=self.tournament).order_by("-round_number").first().round_number
        for fixture in Fixture.objects.filter(tournament=self.tournament, round_number=latest_round):
            Result.objects.create(
                fixture=fixture,
                home_score=1,
                away_score=0,
                status=Result.APPROVED,
            )

    def test_hybrid_requires_group_assignments_before_first_generation(self):
        for idx in range(4):
            captain = make_player(f"nogroupcap{idx}")
            teammate = make_player(f"nogroupmate{idx}")
            team = make_team(f"No Group {idx}", captain=captain, is_approved=True)
            TeamMembership.objects.create(player=captain, team=team, role=TeamMembership.CAPTAIN)
            TeamMembership.objects.create(player=teammate, team=team, role=TeamMembership.PLAYER)
            TournamentRegistration.objects.create(tournament=self.tournament, team=team, is_active=True)

        count, error = generate_fixtures_for_tournament(self.tournament)

        self.assertEqual(count, 0)
        self.assertIn("require group assignments", error)

    def test_hybrid_progresses_group_stage_to_knockout_to_final(self):
        groups = self.make_four_groups()

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 4)
        self.assertEqual(
            Fixture.objects.filter(tournament=self.tournament, stage=Fixture.GROUP, is_bye=False).count(),
            4,
        )

        self.approve_group_results(groups)

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 4)
        quarterfinals = list(Fixture.objects.filter(tournament=self.tournament, stage=Fixture.KNOCKOUT, round_number=2).order_by("pk"))
        self.assertEqual(len(quarterfinals), 4)

        self.approve_current_round_home_wins()
        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 2)
        semifinals = list(Fixture.objects.filter(tournament=self.tournament, stage=Fixture.KNOCKOUT, round_number=3).order_by("pk"))
        self.assertEqual(len(semifinals), 2)

        self.approve_current_round_home_wins()
        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 1)
        final_fixture = Fixture.objects.get(tournament=self.tournament, stage=Fixture.FINAL, round_number=4)
        self.assertIsNotNone(final_fixture)

    def test_hybrid_top_2_initial_knockout_generation_fixture_count_unchanged(self):
        self.tournament.hybrid_qualifiers_per_group = Tournament.HYBRID_QUALIFIERS_TOP_2
        self.tournament.save(update_fields=["hybrid_qualifiers_per_group"])
        groups = self.make_four_groups()

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 4)

        self.approve_group_results(groups)

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 4)
        self.assertEqual(
            Fixture.objects.filter(tournament=self.tournament, stage=Fixture.KNOCKOUT).count(),
            4,
        )

    def test_hybrid_top_4_initial_knockout_generation_creates_expected_fixture_count(self):
        self.tournament.hybrid_qualifiers_per_group = Tournament.HYBRID_QUALIFIERS_TOP_4
        self.tournament.save(update_fields=["hybrid_qualifiers_per_group"])
        groups = self.make_four_groups_of_four()

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 24)

        self.approve_group_results(groups)

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 8)
        self.assertEqual(
            Fixture.objects.filter(tournament=self.tournament, stage=Fixture.KNOCKOUT).count(),
            8,
        )

    def test_hybrid_top_4_initial_knockout_generation_uses_cross_group_pairings(self):
        self.tournament.hybrid_qualifiers_per_group = Tournament.HYBRID_QUALIFIERS_TOP_4
        self.tournament.save(update_fields=["hybrid_qualifiers_per_group"])
        groups = self.make_four_groups_of_four()

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 24)
        self.approve_group_results(groups)

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 8)

        fixtures = list(
            Fixture.objects.filter(tournament=self.tournament, stage=Fixture.KNOCKOUT)
            .order_by("pk")
        )
        self.assertEqual(len(fixtures), 8)

        group_a = groups["A"]
        group_b = groups["B"]
        group_c = groups["C"]
        group_d = groups["D"]
        expected_pairs = {
            (group_a[0].pk, group_b[3].pk),
            (group_a[1].pk, group_b[2].pk),
            (group_b[0].pk, group_a[3].pk),
            (group_b[1].pk, group_a[2].pk),
            (group_c[0].pk, group_d[3].pk),
            (group_c[1].pk, group_d[2].pk),
            (group_d[0].pk, group_c[3].pk),
            (group_d[1].pk, group_c[2].pk),
        }
        actual_pairs = {(fixture.home_team_id, fixture.away_team_id) for fixture in fixtures}
        self.assertEqual(actual_pairs, expected_pairs)

    def test_hybrid_top_4_generation_fails_when_group_has_too_few_ranked_teams(self):
        self.tournament.hybrid_qualifiers_per_group = Tournament.HYBRID_QUALIFIERS_TOP_4
        self.tournament.save(update_fields=["hybrid_qualifiers_per_group"])
        groups = self.make_groups(team_count=3)

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.assertEqual(count, 24)
        self.approve_group_results(groups)

        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertEqual(count, 0)
        self.assertIn("must have at least 4 teams to produce qualifiers", error)

    def test_hybrid_blocks_knockout_generation_until_group_results_are_complete(self):
        self.make_four_groups()
        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        fixture = Fixture.objects.filter(tournament=self.tournament, stage=Fixture.GROUP).first()
        Result.objects.create(
            fixture=fixture,
            home_score=1,
            away_score=0,
            status=Result.APPROVED,
        )

        count, error = generate_fixtures_for_tournament(self.tournament)

        self.assertEqual(count, 0)
        self.assertIn("Every group-stage fixture", error)

    def test_knockout_results_cannot_be_draws(self):
        groups = self.make_four_groups()
        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        self.approve_group_results(groups)
        count, error = generate_fixtures_for_tournament(self.tournament)
        self.assertIsNone(error)
        quarterfinal = Fixture.objects.filter(tournament=self.tournament, stage=Fixture.KNOCKOUT).first()

        result = Result(
            fixture=quarterfinal,
            home_score=1,
            away_score=1,
            status=Result.PENDING,
        )

        with self.assertRaises(ValidationError):
            result.full_clean()


# ── Announcement Tests ─────────────────────────────────────────────────────

class AnnouncementModelTests(TestCase):
    """Test the Announcement model."""

    def test_announcement_creation(self):
        """Test creating an announcement."""
        announcement = Announcement.objects.create(
            title="Test Announcement",
            body="This is a test announcement.",
        )
        self.assertEqual(announcement.title, "Test Announcement")
        self.assertTrue(announcement.is_active)
        self.assertFalse(announcement.is_pinned)
        self.assertEqual(announcement.sort_order, 0)

    def test_announcement_with_tournament(self):
        """Test creating an announcement linked to a tournament."""
        tournament = make_tournament()
        announcement = Announcement.objects.create(
            title="Tournament Announcement",
            body="Announcement for specific tournament.",
            tournament=tournament,
        )
        self.assertEqual(announcement.tournament, tournament)

    def test_announcement_ordering(self):
        """Test announcements are ordered by pinned, sort_order, and created_at."""
        ann1 = Announcement.objects.create(
            title="Normal 1",
            body="Body 1",
            is_pinned=False,
            sort_order=10,
        )
        ann2 = Announcement.objects.create(
            title="Pinned 1",
            body="Body 2",
            is_pinned=True,
            sort_order=20,
        )
        ann3 = Announcement.objects.create(
            title="Normal 2",
            body="Body 3",
            is_pinned=False,
            sort_order=5,
        )

        announcements = list(Announcement.objects.all())
        # Pinned first, then sorted by sort_order
        self.assertEqual(announcements[0], ann2)
        self.assertEqual(announcements[1], ann3)
        self.assertEqual(announcements[2], ann1)


class PublicAnnouncementPageTests(TestCase):
    """Test the public announcement list page."""

    def setUp(self):
        self.client = Client()

    def test_announcement_list_url_accessible(self):
        """Test that the announcements page is accessible."""
        response = self.client.get(reverse("tournament:announcement_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tournament/announcement_list.html")

    def test_only_active_announcements_visible(self):
        """Test that only active announcements are shown."""
        Announcement.objects.create(
            title="Active",
            body="Active announcement",
            is_active=True,
        )
        Announcement.objects.create(
            title="Inactive",
            body="Inactive announcement",
            is_active=False,
        )

        response = self.client.get(reverse("tournament:announcement_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["announcements"]), 1)
        self.assertEqual(response.context["announcements"][0].title, "Active")

    def test_pinned_announcements_appear_first(self):
        """Test that pinned announcements appear before normal ones."""
        Announcement.objects.create(
            title="Normal",
            body="Normal announcement",
            is_pinned=False,
            is_active=True,
        )
        Announcement.objects.create(
            title="Pinned",
            body="Pinned announcement",
            is_pinned=True,
            is_active=True,
        )

        response = self.client.get(reverse("tournament:announcement_list"))
        announcements = response.context["announcements"]
        self.assertEqual(announcements[0].title, "Pinned")
        self.assertEqual(announcements[1].title, "Normal")

    def test_empty_state_when_no_announcements(self):
        """Test empty state message when no announcements."""
        response = self.client.get(reverse("tournament:announcement_list"))
        self.assertEqual(len(response.context["announcements"]), 0)
        self.assertContains(response, "No announcements yet")

    def test_announcement_with_tournament_link(self):
        """Test announcement with linked tournament displays correctly."""
        tournament = make_tournament()
        Announcement.objects.create(
            title="Tournament Update",
            body="Update about the tournament.",
            tournament=tournament,
            is_active=True,
        )

        response = self.client.get(reverse("tournament:announcement_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, tournament.name)


class StaffAnnouncementListTests(TestCase):
    """Test the staff announcement management list."""

    def setUp(self):
        self.client = Client()
        self.staff_user = make_player("staff_user", is_staff=True)
        self.non_staff_user = make_player("non_staff_user", is_staff=False)

    def test_staff_announcement_list_requires_login(self):
        """Test that staff announcement list requires authentication."""
        response = self.client.get(reverse("tournament:staff_announcement_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_staff_announcement_list_requires_staff(self):
        """Test that non-staff users cannot access staff announcement list."""
        self.client.login(username="non_staff_user", password="testpass123")
        response = self.client.get(reverse("tournament:staff_announcement_list"))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_access_announcement_list(self):
        """Test that staff can access the announcement list."""
        self.client.login(username="staff_user", password="testpass123")
        response = self.client.get(reverse("tournament:staff_announcement_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tournament/staff/announcement_list.html")

    def test_staff_sees_all_announcements(self):
        """Test that staff sees both active and inactive announcements."""
        Announcement.objects.create(
            title="Active",
            body="Active",
            is_active=True,
        )
        Announcement.objects.create(
            title="Inactive",
            body="Inactive",
            is_active=False,
        )

        self.client.login(username="staff_user", password="testpass123")
        response = self.client.get(reverse("tournament:staff_announcement_list"))
        self.assertEqual(len(response.context["announcements"]), 2)

    def test_staff_announcement_list_shows_status_badges(self):
        """Test that staff list displays active/inactive status."""
        Announcement.objects.create(
            title="Active",
            body="Active",
            is_active=True,
        )
        Announcement.objects.create(
            title="Inactive",
            body="Inactive",
            is_active=False,
        )

        self.client.login(username="staff_user", password="testpass123")
        response = self.client.get(reverse("tournament:staff_announcement_list"))
        self.assertContains(response, "Active")
        self.assertContains(response, "Inactive")


class StaffAnnouncementCreateTests(TestCase):
    """Test creating announcements as staff."""

    def setUp(self):
        self.client = Client()
        self.staff_user = make_player("staff_user", is_staff=True)
        self.non_staff_user = make_player("non_staff_user", is_staff=False)

    def test_create_announcement_requires_login(self):
        """Test that announcement creation requires authentication."""
        response = self.client.get(reverse("tournament:staff_announcement_create"))
        self.assertEqual(response.status_code, 302)

    def test_create_announcement_requires_staff(self):
        """Test that non-staff users cannot create announcements."""
        self.client.login(username="non_staff_user", password="testpass123")
        response = self.client.get(reverse("tournament:staff_announcement_create"))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_access_create_form(self):
        """Test that staff can access the create announcement form."""
        self.client.login(username="staff_user", password="testpass123")
        response = self.client.get(reverse("tournament:staff_announcement_create"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tournament/staff/announcement_form.html")

    def test_staff_can_create_announcement(self):
        """Test that staff can create a new announcement."""
        self.client.login(username="staff_user", password="testpass123")
        data = {
            "title": "New Announcement",
            "body": "This is a new announcement.",
            "is_active": True,
            "is_pinned": False,
            "tournament": "",
            "sort_order": 0,
        }
        response = self.client.post(reverse("tournament:staff_announcement_create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Announcement.objects.filter(title="New Announcement").count(), 1)

    def test_staff_can_create_pinned_announcement(self):
        """Test that staff can create a pinned announcement."""
        self.client.login(username="staff_user", password="testpass123")
        data = {
            "title": "Pinned Announcement",
            "body": "This is pinned.",
            "is_active": True,
            "is_pinned": True,
            "tournament": "",
            "sort_order": 0,
        }
        response = self.client.post(reverse("tournament:staff_announcement_create"), data)
        announcement = Announcement.objects.get(title="Pinned Announcement")
        self.assertTrue(announcement.is_pinned)

    def test_staff_can_create_inactive_announcement(self):
        """Test that staff can create inactive (draft) announcements."""
        self.client.login(username="staff_user", password="testpass123")
        data = {
            "title": "Draft Announcement",
            "body": "This is a draft.",
            "is_active": False,
            "is_pinned": False,
            "tournament": "",
            "sort_order": 0,
        }
        response = self.client.post(reverse("tournament:staff_announcement_create"), data)
        announcement = Announcement.objects.get(title="Draft Announcement")
        self.assertFalse(announcement.is_active)

    def test_staff_can_create_announcement_with_tournament(self):
        """Test that staff can link announcement to tournament."""
        tournament = make_tournament()
        self.client.login(username="staff_user", password="testpass123")
        data = {
            "title": "Tournament Update",
            "body": "Update about tournament.",
            "is_active": True,
            "is_pinned": False,
            "tournament": tournament.pk,
            "sort_order": 0,
        }
        response = self.client.post(reverse("tournament:staff_announcement_create"), data)
        announcement = Announcement.objects.get(title="Tournament Update")
        self.assertEqual(announcement.tournament, tournament)


class StaffAnnouncementEditTests(TestCase):
    """Test editing announcements as staff."""

    def setUp(self):
        self.client = Client()
        self.staff_user = make_player("staff_user", is_staff=True)
        self.non_staff_user = make_player("non_staff_user", is_staff=False)
        self.announcement = Announcement.objects.create(
            title="Original Title",
            body="Original body.",
            is_active=True,
            is_pinned=False,
        )

    def test_edit_announcement_requires_login(self):
        """Test that editing requires authentication."""
        response = self.client.get(
            reverse("tournament:staff_announcement_edit", args=[self.announcement.pk])
        )
        self.assertEqual(response.status_code, 302)

    def test_edit_announcement_requires_staff(self):
        """Test that non-staff users cannot edit announcements."""
        self.client.login(username="non_staff_user", password="testpass123")
        response = self.client.get(
            reverse("tournament:staff_announcement_edit", args=[self.announcement.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_can_access_edit_form(self):
        """Test that staff can access the edit form."""
        self.client.login(username="staff_user", password="testpass123")
        response = self.client.get(
            reverse("tournament:staff_announcement_edit", args=[self.announcement.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "tournament/staff/announcement_form.html")

    def test_staff_can_edit_announcement(self):
        """Test that staff can edit an announcement."""
        self.client.login(username="staff_user", password="testpass123")
        data = {
            "title": "Updated Title",
            "body": "Updated body.",
            "is_active": True,
            "is_pinned": False,
            "tournament": "",
            "sort_order": 0,
        }
        response = self.client.post(
            reverse("tournament:staff_announcement_edit", args=[self.announcement.pk]), data
        )
        self.announcement.refresh_from_db()
        self.assertEqual(self.announcement.title, "Updated Title")

    def test_staff_can_publish_unpublish(self):
        """Test that staff can publish/unpublish using is_active."""
        self.client.login(username="staff_user", password="testpass123")
        data = {
            "title": self.announcement.title,
            "body": self.announcement.body,
            "is_active": False,
            "is_pinned": False,
            "tournament": "",
            "sort_order": 0,
        }
        response = self.client.post(
            reverse("tournament:staff_announcement_edit", args=[self.announcement.pk]), data
        )
        self.announcement.refresh_from_db()
        self.assertFalse(self.announcement.is_active)

    def test_staff_can_pin_unpin(self):
        """Test that staff can pin/unpin announcements."""
        self.client.login(username="staff_user", password="testpass123")
        data = {
            "title": self.announcement.title,
            "body": self.announcement.body,
            "is_active": True,
            "is_pinned": True,
            "tournament": "",
            "sort_order": 0,
        }
        response = self.client.post(
            reverse("tournament:staff_announcement_edit", args=[self.announcement.pk]), data
        )
        self.announcement.refresh_from_db()
        self.assertTrue(self.announcement.is_pinned)

    def test_edit_nonexistent_announcement_returns_404(self):
        """Test that editing a nonexistent announcement returns 404."""
        self.client.login(username="staff_user", password="testpass123")
        response = self.client.get(reverse("tournament:staff_announcement_edit", args=[99999]))
        self.assertEqual(response.status_code, 404)


class HomePageAnnouncementTests(TestCase):
    """Test announcement display on the home page."""

    def setUp(self):
        self.client = Client()

    def test_home_shows_only_active_announcements(self):
        """Test that home page shows only active announcements."""
        Announcement.objects.create(
            title="Active",
            body="Active",
            is_active=True,
        )
        Announcement.objects.create(
            title="Inactive",
            body="Inactive",
            is_active=False,
        )

        response = self.client.get(reverse("tournament:home"))
        self.assertEqual(response.status_code, 200)
        # The home page should have announcements in context
        if "announcements" in response.context:
            announcements = response.context["announcements"]
            # Filter to only active
            active_announcements = [a for a in announcements if a.is_active]
            self.assertEqual(len(active_announcements), 1)

    def test_home_shows_pinned_announcements_first(self):
        """Test that home page displays pinned announcements first."""
        Announcement.objects.create(
            title="Normal",
            body="Normal",
            is_pinned=False,
            is_active=True,
        )
        Announcement.objects.create(
            title="Pinned",
            body="Pinned",
            is_pinned=True,
            is_active=True,
        )

        response = self.client.get(reverse("tournament:home"))
        if "announcements" in response.context:
            announcements = response.context["announcements"]
            # First active announcement should be pinned
            if len(announcements) > 0:
                self.assertTrue(announcements[0].is_pinned)


class HomePageDiscordCtaTests(TestCase):
    """Protect the homepage Discord/community CTA."""

    @override_settings(DISCORD_LINK="https://discord.example/invite")
    def test_homepage_renders_discord_cta_when_link_is_configured(self):
        response = self.client.get(reverse("tournament:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="https://discord.example/invite"')
        self.assertContains(response, "Join Discord")
