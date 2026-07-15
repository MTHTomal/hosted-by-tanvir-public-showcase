from django.db import migrations
import cloudinary.models


class Migration(migrations.Migration):

    dependencies = [
        ("tournament", "0006_announcement"),
    ]

    operations = [
        migrations.AlterField(
            model_name="result",
            name="screenshot",
            field=cloudinary.models.CloudinaryField(
                blank=True,
                help_text="Proof screenshot uploaded by the submitting team.",
                max_length=255,
                null=True,
                verbose_name="result_screenshot",
            ),
        ),
    ]
