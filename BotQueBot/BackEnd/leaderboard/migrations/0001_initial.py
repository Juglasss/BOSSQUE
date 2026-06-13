from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Leaderboard",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        default="Round Table Leaderboard",
                        max_length=100,
                    ),
                ),
                ("min_games", models.PositiveIntegerField(default=0)),
                ("include_banned", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ("name",),
            },
        ),
    ]
