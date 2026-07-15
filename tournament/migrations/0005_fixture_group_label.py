from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournament", "0004_tournamentregistration_group_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="fixture",
            name="group_label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional stored group context for grouped stage fixtures.",
                max_length=20,
            ),
        ),
    ]
