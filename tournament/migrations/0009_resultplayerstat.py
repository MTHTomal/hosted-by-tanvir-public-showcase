from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_teammembership_history_constraints"),
        ("tournament", "0008_result_integrity_constraint"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResultPlayerStat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("goals", models.PositiveSmallIntegerField(default=0)),
                ("assists", models.PositiveSmallIntegerField(default=0)),
                ("yellow_cards", models.PositiveSmallIntegerField(default=0)),
                ("red_cards", models.PositiveSmallIntegerField(default=0)),
                ("player", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="submitted_match_stats", to="accounts.player")),
                ("result", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="submitted_player_stats", to="tournament.result")),
                ("team", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="submitted_match_stats", to="accounts.team")),
            ],
            options={
                "ordering": ["team__name", "player__username"],
            },
        ),
        migrations.AddConstraint(
            model_name="resultplayerstat",
            constraint=models.UniqueConstraint(fields=("result", "player"), name="unique_player_stat_per_submitted_result"),
        ),
    ]
