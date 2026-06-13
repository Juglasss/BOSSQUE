from django.urls import path
from . import views

urlpatterns = [
    path("players/", views.players_list_create, name="players-list-create"),
    path(
        "players/discord/<str:discord_id>/stats/",
        views.player_stats_by_discord_id,
        name="player-stats-by-discord-id"
    ),
    path("players/<int:pk>/", views.player_detail, name="player-detail"),
]
