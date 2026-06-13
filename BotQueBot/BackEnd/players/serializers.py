

from rest_framework import serializers
from .models import Player

# Create your views here.
class PlayerSerializer(serializers.ModelSerializer):
    winrate = serializers.SerializerMethodField()
    rank_display = serializers.CharField(
        source="get_rank_display",
        read_only=True
    )

    class Meta:
        model = Player
        fields = [
            "id",
            "discord_id",
            "username",
            "ign",
            "avatar_url",
            "region",
            "mmr",
            "wins",
            "losses",
            "total_games",
            "streak",
            "peak_streak",
            "role_preference",
            "rank",
            "rank_display",
            "mvps",
            "winrate",
            "last_match_end",
            "last_decay_at",
            "last_role_change_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "wins",
            "losses",
            "total_games",
            "streak",
            "peak_streak",
            "mvps",
            "rank",
            "rank_display",
            "winrate",
            "last_match_end",
            "last_decay_at",
            "created_at",
            "updated_at",
        ]

    def get_winrate(self, obj):
        if obj.total_games == 0:
            return 0
        return round((obj.wins / obj.total_games) * 100, 2)
