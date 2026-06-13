from django.db import migrations


MATCH_NUMBER_START = 2392


def rebase_match_numbers(apps, schema_editor):
    Match = apps.get_model("matches", "Match")

    for offset, match in enumerate(
        Match.objects.order_by("created_at", "id")
    ):
        match.match_number = MATCH_NUMBER_START + offset
        match.save(update_fields=["match_number"])


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0011_match_match_number"),
    ]

    operations = [
        migrations.RunPython(
            rebase_match_numbers,
            migrations.RunPython.noop,
        ),
    ]
