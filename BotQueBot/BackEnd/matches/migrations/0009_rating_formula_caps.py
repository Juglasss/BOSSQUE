from django.db import migrations, models


def set_default_rating_formula(apps, schema_editor):
    RatingSettings = apps.get_model("matches", "RatingSettings")
    RatingSettings.objects.update_or_create(
        pk=1,
        defaults={
            "win_base_mmr_change": 16.75,
            "loss_base_mmr_change": 14.5,
            "win_team_diff_mmr_cap": 1.625,
            "win_player_average_mmr_cap": 1.625,
            "loss_team_diff_mmr_cap": 1.25,
            "loss_player_average_mmr_relief_cap": 1.25,
            "loss_player_average_mmr_penalty_cap": 1.25,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0008_ratingsettings_elo_values"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ratingsettings",
            name="loss_base_mmr_change",
            field=models.FloatField(default=14.5),
        ),
        migrations.AlterField(
            model_name="ratingsettings",
            name="win_base_mmr_change",
            field=models.FloatField(default=16.75),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="loss_player_average_mmr_penalty_cap",
            field=models.FloatField(default=1.25),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="loss_player_average_mmr_relief_cap",
            field=models.FloatField(default=1.25),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="loss_team_diff_mmr_cap",
            field=models.FloatField(default=1.25),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="win_player_average_mmr_cap",
            field=models.FloatField(default=1.625),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="win_team_diff_mmr_cap",
            field=models.FloatField(default=1.625),
        ),
        migrations.RunPython(
            set_default_rating_formula,
            migrations.RunPython.noop,
        ),
    ]
