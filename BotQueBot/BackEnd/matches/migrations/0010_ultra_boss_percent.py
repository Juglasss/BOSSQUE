from django.db import migrations, models


def set_ultra_boss_percent(apps, schema_editor):
    RatingSettings = apps.get_model("matches", "RatingSettings")
    RatingSettings.objects.update(
        ultra_boss_instinct_win_bonus_percent=0.40
    )


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0009_rating_formula_caps"),
    ]

    operations = [
        migrations.RenameField(
            model_name="ratingsettings",
            old_name="ultra_boss_instinct_win_bonus",
            new_name="ultra_boss_instinct_win_bonus_percent",
        ),
        migrations.AlterField(
            model_name="ratingsettings",
            name="ultra_boss_instinct_win_bonus_percent",
            field=models.FloatField(default=0.4),
        ),
        migrations.RunPython(
            set_ultra_boss_percent,
            migrations.RunPython.noop,
        ),
    ]
