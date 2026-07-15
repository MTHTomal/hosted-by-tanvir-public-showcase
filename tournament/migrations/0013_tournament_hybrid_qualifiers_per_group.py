from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournament", "0012_result_away_player_stats_screenshot_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="hybrid_qualifiers_per_group",
            field=models.PositiveSmallIntegerField(
                choices=[(2, "Top 2"), (4, "Top 4")],
                default=2,
                help_text="How many teams per group qualify to knockout in hybrid tournaments.",
            ),
        ),
    ]
