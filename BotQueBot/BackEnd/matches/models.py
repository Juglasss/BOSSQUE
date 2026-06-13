from django.db import models
from django.db.models import Max
from players.models import Player


MATCH_NUMBER_START = 2392


class Match(models.Model):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (COMPLETED, "Completed"),
        (CANCELLED, "Cancelled"),
    ]

    TEAM_1 = "team_1"
    TEAM_2 = "team_2"

    WINNER_CHOICES = [
        (TEAM_1, "Team 1"),
        (TEAM_2, "Team 2"),
    ]

    PALADINS_MAP_CHOICES = [
        ("ascension_peak", "Ascension Peak"),
        ("bazaar", "Bazaar"),
        ("brightmarsh", "Brightmarsh"),
        ("frog_isle", "Frog Isle"),
        ("frozen_guard", "Frozen Guard"),
        ("fish_market", "Fish Market"),
        ("ice_mines", "Ice Mines"),
        ("jaguar_falls", "Jaguar Falls"),
        ("serpent_beach", "Serpent Beach"),
        ("shattered_desert", "Shattered Desert"),
        ("splitstone_quarry", "Splitstone Quarry"),
        ("stone_keep", "Stone Keep"),
        ("timber_mill", "Timber Mill"),
        ("warders_gate", "Warder's Gate"),
        ("dawnforge", "Dawnforge"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)

    match_number = models.PositiveIntegerField(
        unique=True,
        editable=False,
        null=True,
        blank=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING
    )

    winner = models.CharField(
        max_length=20,
        choices=WINNER_CHOICES,
        blank=True,
        null=True
    )

    map_name = models.CharField(
        max_length=50,
        choices=PALADINS_MAP_CHOICES,
        blank=True,
        default=""
    )

    map_image = models.ImageField(
        upload_to="maps/",
        blank=True,
        null=True
    )

    team_1_mmr = models.FloatField()
    team_2_mmr = models.FloatField()
    mmr_difference = models.FloatField()

    role_score = models.IntegerField(default=0)

    players = models.ManyToManyField(
        Player,
        through="MatchPlayer",
        related_name="matches"
    )

    def save(self, *args, **kwargs):
        if self.match_number is None:
            last_match_number = (
                Match.objects
                .aggregate(max_match_number=Max("match_number"))
                .get("max_match_number")
            ) or (MATCH_NUMBER_START - 1)
            self.match_number = last_match_number + 1

        super().save(*args, **kwargs)

    def __str__(self):
        return f"Match #{self.match_number} - {self.get_status_display()}"


class MatchPlayer(models.Model):
    TEAM_CHOICES = [
        (Match.TEAM_1, "Team 1"),
        (Match.TEAM_2, "Team 2"),
    ]

    ROLE_CHOICES = [
        ("tank", "Tank"),
        ("dps", "DPS"),
        ("sup", "Support"),
    ]

    match = models.ForeignKey(
        Match,
        on_delete=models.CASCADE,
        related_name="match_players"
    )

    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="match_players"
    )

    team = models.CharField(
        max_length=20,
        choices=TEAM_CHOICES
    )

    assigned_role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES
    )

    mmr_before = models.FloatField()
    mmr_after = models.FloatField(blank=True, null=True)
    mmr_change = models.FloatField(default=0)

    won = models.BooleanField(blank=True, null=True)

    class Meta:
        unique_together = ("match", "player")

    def __str__(self):
        return f"{self.player.username} in Match #{self.match.id}"


class ActiveMap(models.Model):
    map_name = models.CharField(
        max_length=50,
        choices=Match.PALADINS_MAP_CHOICES,
        unique=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("map_name",)

    def __str__(self):
        return self.get_map_name_display()


class RatingSettings(models.Model):
    lock_min_rating = models.BooleanField(default=True)
    decay_start_after_days = models.PositiveIntegerField(default=14)
    decay_repeat_every_days = models.PositiveIntegerField(default=3)
    decay_mmr_loss = models.PositiveIntegerField(default=15)
    win_base_mmr_change = models.FloatField(default=16.75)
    loss_base_mmr_change = models.FloatField(default=14.5)
    win_team_diff_mmr_cap = models.FloatField(default=1.625)
    win_player_average_mmr_cap = models.FloatField(default=1.625)
    loss_team_diff_mmr_cap = models.FloatField(default=1.25)
    loss_player_average_mmr_relief_cap = models.FloatField(default=1.25)
    loss_player_average_mmr_penalty_cap = models.FloatField(default=1.25)
    role_tier_2_win_bonus_percent = models.FloatField(default=0.05)
    role_tier_1_win_bonus_percent = models.FloatField(default=0.075)
    ultra_boss_instinct_win_bonus_percent = models.FloatField(default=0.40)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "rating settings"

    @classmethod
    def get_settings(cls):
        settings, _created = cls.objects.get_or_create(pk=1)
        return settings

    def __str__(self):
        status = "on" if self.lock_min_rating else "off"
        return f"Minimum rating lock: {status}"
