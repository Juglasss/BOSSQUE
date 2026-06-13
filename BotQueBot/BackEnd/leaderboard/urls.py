from django.urls import path

from . import views


urlpatterns = [
    path(
        "leaderboard/",
        views.leaderboard_detail,
        name="leaderboard-detail"
    ),
    path(
        "leaderboard/settings/",
        views.leaderboard_settings,
        name="leaderboard-settings"
    ),
]
