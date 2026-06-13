from django.db import models


class Leaderboard(models.Model):
    name = models.CharField(max_length=100, default="Round Table Leaderboard")
    min_games = models.PositiveIntegerField(default=0)
    include_banned = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    @classmethod
    def get_active(cls):
        leaderboard = cls.objects.filter(is_active=True).first()

        if leaderboard is not None:
            return leaderboard

        return cls.objects.create()

    def __str__(self):
        return self.name
