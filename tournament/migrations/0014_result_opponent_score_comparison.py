from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournament", "0013_tournament_hybrid_qualifiers_per_group"),
    ]

    operations = [
        migrations.AddField(
            model_name="result",
            name="opponent_away_score",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="result",
            name="opponent_home_score",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="result",
            name="opponent_score_state",
            field=models.CharField(
                choices=[
                    ("awaiting_opponent", "Awaiting opponent score"),
                    ("matching", "Scores match"),
                    ("score_conflict", "Score conflict"),
                    ("not_applicable", "Not applicable"),
                ],
                default="awaiting_opponent",
                help_text="Advisory comparison between submitted score and opponent-entered fixture home/away score.",
                max_length=25,
            ),
        ),
    ]
