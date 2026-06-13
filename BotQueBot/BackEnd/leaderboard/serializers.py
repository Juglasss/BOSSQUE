from rest_framework import serializers

from .models import Leaderboard


class LeaderboardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Leaderboard
        fields = [
            "id",
            "name",
            "min_games",
            "include_banned",
            "is_active",
            "created_at",
            "updated_at",
        ]
