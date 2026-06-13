from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("players", "0004_player_last_decay_at"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="player",
            name="peak_mmr",
        ),
    ]
