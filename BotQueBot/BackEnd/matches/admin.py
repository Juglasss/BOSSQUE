from django.contrib import admin
from .models import ActiveMap, Match, MatchPlayer, RatingSettings


class MatchPlayerInline(admin.TabularInline):
    model = MatchPlayer
    extra = 0


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "match_number",
        "status",
        "winner",
        "map_name",
        "map_image",
        "team_1_mmr",
        "team_2_mmr",
        "mmr_difference",
        "role_score",
        "created_at",
    )

    list_filter = (
        "status",
        "winner",
        "map_name",
        "created_at",
    )

    search_fields = (
        "id",
        "match_number",
    )

    ordering = ("-created_at",)

    inlines = [
        MatchPlayerInline,
    ]


@admin.register(MatchPlayer)
class MatchPlayerAdmin(admin.ModelAdmin):
    list_display = (
        "match",
        "player",
        "team",
        "assigned_role",
        "mmr_before",
        "mmr_after",
        "mmr_change",
        "won",
    )

    list_filter = (
        "team",
        "assigned_role",
        "won",
    )

    search_fields = (
        "player__username",
        "player__discord_id",
        "match__id",
    )


@admin.register(ActiveMap)
class ActiveMapAdmin(admin.ModelAdmin):
    list_display = (
        "map_name",
        "created_at",
    )

    list_filter = (
        "map_name",
    )


@admin.register(RatingSettings)
class RatingSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "lock_min_rating",
        "decay_start_after_days",
        "decay_repeat_every_days",
        "decay_mmr_loss",
        "win_base_mmr_change",
        "loss_base_mmr_change",
        "role_tier_2_win_bonus_percent",
        "role_tier_1_win_bonus_percent",
        "ultra_boss_instinct_win_bonus_percent",
        "updated_at",
    )
