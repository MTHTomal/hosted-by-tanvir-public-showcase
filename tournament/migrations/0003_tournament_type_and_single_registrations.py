from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_teammembership_history_constraints"),
        ("tournament", "0002_alter_fixture_away_team"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="tournament_type",
            field=models.CharField(
                choices=[("team", "Team"), ("single", "Single-player")],
                default="team",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="tournamentregistration",
            name="player",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tournament_entries",
                to="accounts.player",
            ),
        ),
        migrations.AlterField(
            model_name="tournamentregistration",
            name="team",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tournament_entries",
                to="accounts.team",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="tournamentregistration",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="tournamentregistration",
            constraint=models.UniqueConstraint(
                condition=models.Q(("team__isnull", False)),
                fields=("tournament", "team"),
                name="unique_team_registration_per_tournament",
            ),
        ),
        migrations.AddConstraint(
            model_name="tournamentregistration",
            constraint=models.UniqueConstraint(
                condition=models.Q(("player__isnull", False)),
                fields=("tournament", "player"),
                name="unique_player_registration_per_tournament",
            ),
        ),
        migrations.AddConstraint(
            model_name="tournamentregistration",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("team__isnull", False), ("player__isnull", True))
                    | models.Q(("team__isnull", True), ("player__isnull", False))
                ),
                name="exactly_one_registration_target",
            ),
        ),
    ]
