from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournament", "0003_tournament_type_and_single_registrations"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournamentregistration",
            name="group_label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional manual group assignment for team tournaments.",
                max_length=20,
            ),
        ),
    ]
