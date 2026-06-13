from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0007_ratingsettings_decay_values"),
    ]

    operations = [
        migrations.AddField(
            model_name="ratingsettings",
            name="loss_base_mmr_change",
            field=models.FloatField(default=19.5),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="role_tier_1_win_bonus_percent",
            field=models.FloatField(default=0.075),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="role_tier_2_win_bonus_percent",
            field=models.FloatField(default=0.05),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="ultra_boss_instinct_win_bonus",
            field=models.FloatField(default=5),
        ),
        migrations.AddField(
            model_name="ratingsettings",
            name="win_base_mmr_change",
            field=models.FloatField(default=20),
        ),
    ]
