from django.contrib import admin
from .models import Player

# Register your models here.

@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = (
        "username",
        "ign",
        "discord_id",
        "mmr",
        "wins",
        "losses",
        "total_games",
        "streak",
        "role_preference",
        "banned",
        "created_at",
    )

    search_fields = (
        "username",
        "ign",
        "discord_id",
    )

    list_filter = (
        "role_preference",
        "banned",
    )

    ordering = ("-mmr",)

    readonly_fields = (
        "created_at",
        "updated_at",
    )
