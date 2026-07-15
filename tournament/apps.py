from django.apps import AppConfig

class TournamentConfig(AppConfig):
    name = 'tournament'
    def ready(self):
        import tournament.signals  # noqa: F401