from django.db import migrations, models


def backfill_match_numbers(apps, schema_editor):
    Match = apps.get_model("matches", "Match")

    for number, match in enumerate(
        Match.objects.order_by("created_at", "id"),
        start=1
    ):
        match.match_number = number
        match.save(update_fields=["match_number"])


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0010_ultra_boss_percent"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="match_number",
            field=models.PositiveIntegerField(
                blank=True,
                editable=False,
                null=True,
                unique=True
            ),
        ),
        migrations.RunPython(
            backfill_match_numbers,
            migrations.RunPython.noop,
        ),
    ]
