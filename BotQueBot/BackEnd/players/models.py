from django.db import models


class Player(models.Model):
    NA = "NA"
    EU = "EU"
    LATAM = "LATAM"

    REGION_CHOICES = [
        (NA, "NA"),
        (EU, "EU"),
        (LATAM, "LATAM"),
    ]

    ROLE_CHOICES = [
        ("tank", "Tank"),
        ("dps", "DPS"),
        ("sup", "Support"),

        ("dps_tank", "DPS / Tank"),
        ("tank_sup", "Tank / Support"),
        ("dps_sup", "DPS / Support"),

        ("flex", "Flex"),
    ]

    ROLE_TIERS = {
        "tank": 3,
        "dps": 3,
        "sup": 3,

        "dps_tank": 2,
        "tank_sup": 2,
        "dps_sup": 2,

        "flex": 1,
    }

    MUSTARD_GAS = "mustard_gas"
    WOODHUMAN = "woodhuman"
    GOODMASTER = "goodmaster"
    GREATMASTER = "greatmaster"
    GRANDMASTER = "grandmaster"
    SUPER_GRANDMASTER = "super_grandmaster"
    SUPER_GRANDMASTER_GOD = "super_grandmaster_god"

    RANK_CHOICES = [
        (MUSTARD_GAS, "MUSTARD GAS"),
        (WOODHUMAN, "WOODHUMAN"),
        (GOODMASTER, "GOODMASTER"),
        (GREATMASTER, "GREATMASTER"),
        (GRANDMASTER, "GRANDMASTER"),
        (SUPER_GRANDMASTER, "SUPER GRANDMASTER"),
        (SUPER_GRANDMASTER_GOD, "SUPER GRANDMASTER GOD"),
    ]

    discord_id = models.CharField(max_length=30, unique=True)
    username = models.CharField(max_length=100)
    ign = models.CharField(max_length=100, blank=True, default="")

    avatar_url = models.URLField(blank=True, null=True)
    region = models.CharField(
        max_length=10,
        choices=REGION_CHOICES,
        default=EU
    )

    mmr = models.FloatField(default=1500)

    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    total_games = models.PositiveIntegerField(default=0)

    streak = models.IntegerField(default=0)
    peak_streak = models.IntegerField(default=0)

    role_preference = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="flex"
    )

    rank = models.CharField(
        max_length=30,
        choices=RANK_CHOICES,
        default=WOODHUMAN
    )

    mvps = models.PositiveIntegerField(default=0)
    banned = models.BooleanField(default=False)

    last_match_end = models.DateTimeField(blank=True, null=True)
    last_decay_at = models.DateTimeField(blank=True, null=True)
    last_role_change_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def winrate(self):
        if self.total_games == 0:
            return 0
        return round((self.wins / self.total_games) * 100, 2)

    @property
    def role_tier(self):
        return self.ROLE_TIERS.get(self.role_preference, 1)

    @classmethod
    def rank_for_mmr(cls, mmr):
        if mmr < 1500:
            return cls.MUSTARD_GAS

        if mmr < 1600:
            return cls.WOODHUMAN

        if mmr < 1700:
            return cls.GOODMASTER

        if mmr < 1800:
            return cls.GREATMASTER

        if mmr < 2000:
            return cls.GRANDMASTER

        if mmr < 2200:
            return cls.SUPER_GRANDMASTER

        return cls.SUPER_GRANDMASTER_GOD

    def save(self, *args, **kwargs):
        self.rank = self.rank_for_mmr(self.mmr)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.username} ({self.mmr})"
