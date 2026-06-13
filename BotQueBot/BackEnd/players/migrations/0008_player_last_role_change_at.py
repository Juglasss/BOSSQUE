from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("players", "0007_player_region_choices"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="last_role_change_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
