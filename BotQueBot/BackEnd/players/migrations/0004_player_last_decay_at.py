from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("players", "0003_player_rank"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="last_decay_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
