from django.urls import path
from . import views

urlpatterns = [
    path(
        "matches/",
        views.matches_list_create,
        name="matches-list-create"
    ),

    path(
        "matches/<int:pk>/",
        views.match_detail,
        name="match-detail"
    ),

    path(
        "matches/<int:pk>/revoke/",
        views.revoke_match,
        name="match-revoke"
    ),

    path(
        "matches/<int:pk>/change-winner/",
        views.change_match_winner,
        name="match-change-winner"
    ),

    path(
        "matches/<int:pk>/set-winner/",
        views.set_match_winner,
        name="match-set-winner"
    ),

    path(
        "matches/<int:pk>/punish-cancel/",
        views.punish_cancel_match,
        name="match-punish-cancel"
    ),

    path(
        "matches/<int:pk>/win-by-punish/",
        views.win_by_punish_match,
        name="match-win-by-punish"
    ),

    path(
        "match-players/",
        views.add_match_player,
        name="add-match-player"
    ),

    path(
        "match-players/<int:pk>/",
        views.match_player_detail,
        name="match-player-detail"
    ),

    path(
        "active-maps/",
        views.active_maps_list_create,
        name="active-maps-list-create"
    ),

    path(
        "active-maps/<str:map_name>/",
        views.active_map_detail,
        name="active-map-detail"
    ),

    path(
        "rating-settings/",
        views.rating_settings_detail,
        name="rating-settings-detail"
    ),

    path(
        "rating-decay/run/",
        views.run_rating_decay,
        name="rating-decay-run"
    ),
]
