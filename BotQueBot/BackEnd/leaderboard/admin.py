from django.contrib import admin

from .models import Leaderboard


@admin.register(Leaderboard)
class LeaderboardAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "min_games",
        "include_banned",
        "is_active",
        "updated_at",
    )
    list_filter = ("include_banned", "is_active")
    search_fields = ("name",)

# Register your models here.
