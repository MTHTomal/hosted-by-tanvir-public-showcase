# tournament/signals.py
#
# Wires Result approval → automatic Standing recalculation.
# Import this in tournament/apps.py → ready() so it registers on startup.

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


def _recalculate_fixture_standings(fixture):
    from standings.models import Standing
    tournament = fixture.tournament

    for team in [fixture.home_team, fixture.away_team]:
        standing, _ = Standing.objects.get_or_create(
            tournament=tournament, team=team
        )
        standing.recalculate()


@receiver(post_save, sender="tournament.Result")
def recalculate_standings_on_result_save(sender, instance, **kwargs):
    """
    Recalculate standings whenever a result row changes.

    This keeps standings correct not only on approval, but also when an
    approved result is edited, rejected, disputed, or superseded.
    """
    _recalculate_fixture_standings(instance.fixture)


@receiver(post_delete, sender="tournament.Result")
def recalculate_standings_on_result_delete(sender, instance, **kwargs):
    _recalculate_fixture_standings(instance.fixture)


# ── tournament/apps.py ───────────────────────────────────────────────────
# Add this to your TournamentConfig.ready() method:
#
#   class TournamentConfig(AppConfig):
#       name = "tournament"
#       def ready(self):
#           import tournament.signals  # noqa: F401
