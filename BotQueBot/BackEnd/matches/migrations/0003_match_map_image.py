from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0002_match_map_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="map_image",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="maps/",
            ),
        ),
    ]
