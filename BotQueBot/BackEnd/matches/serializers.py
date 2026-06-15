from rest_framework import serializers
from .models import ActiveMap, Match, MatchPlayer, RatingSettings


class ActiveMapSerializer(serializers.ModelSerializer):
    map_display_name = serializers.CharField(
        source="get_map_name_display",
        read_only=True
    )

    class Meta:
        model = ActiveMap
        fields = [
            "id",
            "map_name",
            "map_display_name",
            "created_at",
        ]


class RatingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = RatingSettings
        fields = [
            "lock_min_rating",
            "decay_start_after_days",
            "decay_repeat_every_days",
            "decay_mmr_loss",
            "win_base_mmr_change",
            "loss_base_mmr_change",
            "win_team_diff_mmr_cap",
            "win_player_average_mmr_cap",
            "loss_team_diff_mmr_cap",
            "loss_player_average_mmr_relief_cap",
            "loss_player_average_mmr_penalty_cap",
            "role_tier_2_win_bonus_percent",
            "role_tier_1_win_bonus_percent",
            "ultra_boss_instinct_win_bonus_percent",
            "updated_at",
        ]


class MatchPlayerSerializer(serializers.ModelSerializer):
    player_username = serializers.CharField(
        source="player.username",
        read_only=True
    )
    player_discord_id = serializers.CharField(
        source="player.discord_id",
        read_only=True
    )

    class Meta:
        model = MatchPlayer
        fields = [
            "id",
            "match",
            "player",
            "player_username",
            "player_discord_id",
            "team",
            "assigned_role",
            "mmr_before",
            "mmr_after",
            "mmr_change",
            "role_tier_before",
            "streak_before",
            "won",
        ]


class MatchSerializer(serializers.ModelSerializer):
    match_players = MatchPlayerSerializer(
        many=True,
        read_only=True
    )

    class Meta:
        model = Match
        fields = [
            "id",
            "match_number",
            "created_at",
            "completed_at",
            "status",
            "winner",
            "map_name",
            "map_image",
            "team_1_mmr",
            "team_2_mmr",
            "mmr_difference",
            "role_score",
            "match_players",
        ]
