from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0006_ratingsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="ratingsettings",
            name="decay_mmr_loss",
            field=models.PositiveIntegerField(default=15),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="decay_repeat_every_days",
            field=models.PositiveIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="decay_start_after_days",
            field=models.PositiveIntegerField(default=14),
        ),
    ]
