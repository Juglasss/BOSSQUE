from django.db import migrations, models


def backfill_completed_at(apps, schema_editor):
    Match = apps.get_model("matches", "Match")
    Match.objects.filter(
        status="completed",
        completed_at__isnull=True,
    ).update(completed_at=models.F("created_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0012_rebase_match_numbers"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="matchplayer",
            name="role_tier_before",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="matchplayer",
            name="streak_before",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_completed_at, migrations.RunPython.noop),
    ]
