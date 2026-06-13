from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("players", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="ign",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
