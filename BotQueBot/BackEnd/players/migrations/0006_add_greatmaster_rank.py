from django.db import migrations, models


def rank_for_mmr(mmr):
    if mmr < 1500:
        return "mustard_gas"

    if mmr < 1600:
        return "woodhuman"

    if mmr < 1700:
        return "goodmaster"

    if mmr < 1800:
        return "greatmaster"

    if mmr < 2000:
        return "grandmaster"

    if mmr < 2200:
        return "super_grandmaster"

    return "super_grandmaster_god"


def recalculate_player_ranks(apps, schema_editor):
    Player = apps.get_model("players", "Player")

    for player in Player.objects.all():
        player.rank = rank_for_mmr(player.mmr)
        player.save(update_fields=["rank"])


class Migration(migrations.Migration):

    dependencies = [
        ("players", "0005_remove_player_peak_mmr"),
    ]

    operations = [
        migrations.AlterField(
            model_name="player",
            name="rank",
            field=models.CharField(
                choices=[
                    ("mustard_gas", "MUSTARD GAS"),
                    ("woodhuman", "WOODHUMAN"),
                    ("goodmaster", "GOODMASTER"),
                    ("greatmaster", "GREATMASTER"),
                    ("grandmaster", "GRANDMASTER"),
                    ("super_grandmaster", "SUPER GRANDMASTER"),
                    (
                        "super_grandmaster_god",
                        "SUPER GRANDMASTER GOD",
                    ),
                ],
                default="woodhuman",
                max_length=30,
            ),
        ),
        migrations.RunPython(
            recalculate_player_ranks,
            migrations.RunPython.noop,
        ),
    ]
