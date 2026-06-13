from django.db import migrations, models


def normalize_regions(apps, schema_editor):
    Player = apps.get_model("players", "Player")
    allowed_regions = {"NA", "EU", "LATAM"}

    for player in Player.objects.all():
        region = (player.region or "EU").upper()

        if region not in allowed_regions:
            region = "EU"

        if player.region != region:
            player.region = region
            player.save(update_fields=["region"])


class Migration(migrations.Migration):

    dependencies = [
        ("players", "0006_add_greatmaster_rank"),
    ]

    operations = [
        migrations.RunPython(
            normalize_regions,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="player",
            name="region",
            field=models.CharField(
                choices=[
                    ("NA", "NA"),
                    ("EU", "EU"),
                    ("LATAM", "LATAM"),
                ],
                default="EU",
                max_length=10,
            ),
        ),
    ]
