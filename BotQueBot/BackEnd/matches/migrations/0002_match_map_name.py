from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="map_name",
            field=models.CharField(
                blank=True,
                choices=[
                    ("abyss", "Abyss"),
                    ("ascension_peak", "Ascension Peak"),
                    ("bazaar", "Bazaar"),
                    ("brightmarsh", "Brightmarsh"),
                    ("dawnforge", "Dawnforge"),
                    ("dragon_arena", "Dragon Arena"),
                    ("fish_market", "Fish Market"),
                    ("foremans_rise", "Foreman's Rise"),
                    ("frostbite_cavern", "Frostbite Cavern"),
                    ("frog_isle", "Frog Isle"),
                    ("frozen_guard", "Frozen Guard"),
                    ("greenwood_outpost", "Greenwood Outpost"),
                    ("hidden_temple", "Hidden Temple"),
                    ("hole", "Hole"),
                    ("ice_mines", "Ice Mines"),
                    ("jaguar_falls", "Jaguar Falls"),
                    ("magistrates_archives", "Magistrate's Archives"),
                    ("marauders_port", "Marauder's Port"),
                    ("primal_court", "Primal Court"),
                    ("serpent_beach", "Serpent Beach"),
                    ("shattered_desert", "Shattered Desert"),
                    ("shooting_range", "Shooting Range"),
                    ("sniper_haven", "Sniper Haven"),
                    ("snowfall_junction", "Snowfall Junction"),
                    ("splitstone_quarry", "Splitstone Quarry"),
                    ("stone_keep", "Stone Keep"),
                    ("throne", "Throne"),
                    ("timber_mill", "Timber Mill"),
                    ("trade_district", "Trade District"),
                    ("tutorial", "Tutorial"),
                    ("warders_gate", "Warder's Gate"),
                ],
                default="",
                max_length=50,
            ),
        ),
    ]
