from django.db import migrations, models
from django.db.models import Count, Q


def reconcile_duplicate_approved_results(apps, schema_editor):
    Result = apps.get_model("tournament", "Result")

    duplicate_fixture_ids = (
        Result.objects
        .filter(status="approved")
        .values("fixture_id")
        .annotate(approved_count=Count("id"))
        .filter(approved_count__gt=1)
        .values_list("fixture_id", flat=True)
    )

    for fixture_id in duplicate_fixture_ids:
        approved_results = list(
            Result.objects
            .filter(fixture_id=fixture_id, status="approved")
            .order_by("-reviewed_at", "-submitted_at", "-pk")
        )
        keeper = approved_results[0]
        for result in approved_results[1:]:
            note = (result.admin_note or "").strip()
            suffix = "Superseded automatically by the result integrity migration."
            result.status = "rejected"
            result.admin_note = f"{note} {suffix}".strip() if note else suffix
            result.save(update_fields=["status", "admin_note"])


class Migration(migrations.Migration):

    dependencies = [
        ("tournament", "0007_alter_result_screenshot"),
    ]

    operations = [
        migrations.RunPython(
            reconcile_duplicate_approved_results,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="result",
            constraint=models.UniqueConstraint(
                fields=("fixture",),
                condition=Q(status="approved"),
                name="unique_approved_result_per_fixture",
            ),
        ),
    ]
