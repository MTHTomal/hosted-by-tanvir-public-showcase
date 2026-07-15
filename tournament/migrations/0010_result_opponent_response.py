from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_teammembership_history_constraints"),
        ("tournament", "0009_resultplayerstat"),
    ]

    operations = [
        migrations.AddField(
            model_name="result",
            name="opponent_responded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="result",
            name="opponent_responded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="opponent_result_responses",
                to="accounts.player",
            ),
        ),
        migrations.AddField(
            model_name="result",
            name="opponent_response_note",
            field=models.TextField(
                blank=True,
                help_text="Optional note from the opposing team when disputing the submitted result.",
            ),
        ),
        migrations.AddField(
            model_name="result",
            name="opponent_response_status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("confirmed", "Confirmed"), ("disputed", "Disputed")],
                default="pending",
                help_text="Whether the opposing team confirmed or disputed this submitted result.",
                max_length=15,
            ),
        ),
        migrations.AddField(
            model_name="result",
            name="submitting_team",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="submitted_fixture_results",
                to="accounts.team",
            ),
        ),
    ]
