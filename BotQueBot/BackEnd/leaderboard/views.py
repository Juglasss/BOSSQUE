from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Case, ExpressionWrapper, F, FloatField, Value, When

from players.models import Player

from .models import Leaderboard
from .serializers import LeaderboardSerializer


DEFAULT_LEADERBOARD_LIMIT = 50
MAX_LEADERBOARD_LIMIT = 100
DEFAULT_SORT_BY = "mmr"
LEADERBOARD_SORTS = {
    "mmr": ("-mmr", "-wins", "losses", "username", "id"),
    "winrate": ("-leaderboard_winrate", "-total_games", "-mmr", "username", "id"),
    "wins": ("-wins", "-mmr", "losses", "username", "id"),
    "games": ("-total_games", "-mmr", "username", "id"),
    "streak": ("-streak", "-mmr", "username", "id"),
    "peak_streak": ("-peak_streak", "-mmr", "username", "id"),
}


def int_query_param(request, name, default):
    value = request.query_params.get(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


def leaderboard_queryset(leaderboard, sort_by):
    players = Player.objects.all()

    if not leaderboard.include_banned:
        players = players.filter(banned=False)

    if leaderboard.min_games > 0:
        players = players.filter(total_games__gte=leaderboard.min_games)

    if sort_by == "winrate":
        players = players.annotate(
            leaderboard_winrate=Case(
                When(
                    total_games=0,
                    then=Value(0.0),
                ),
                default=ExpressionWrapper(
                    F("wins") * 100.0 / F("total_games"),
                    output_field=FloatField(),
                ),
                output_field=FloatField(),
            )
        )

    return players.order_by(*LEADERBOARD_SORTS[sort_by])


def leaderboard_entry(player, position):
    return {
        "position": position,
        "player_id": player.id,
        "discord_id": player.discord_id,
        "username": player.username,
        "ign": player.ign,
        "avatar_url": player.avatar_url,
        "mmr": player.mmr,
        "rank": player.rank,
        "rank_display": player.get_rank_display(),
        "wins": player.wins,
        "losses": player.losses,
        "total_games": player.total_games,
        "streak": player.streak,
        "peak_streak": player.peak_streak,
        "winrate": player.winrate,
        "role_preference": player.role_preference,
    }


@api_view(["GET"])
def leaderboard_detail(request):
    leaderboard = Leaderboard.get_active()
    limit = int_query_param(request, "limit", DEFAULT_LEADERBOARD_LIMIT)
    limit = max(1, min(limit, MAX_LEADERBOARD_LIMIT))
    sort_by = request.query_params.get("sort_by", DEFAULT_SORT_BY)

    if sort_by not in LEADERBOARD_SORTS:
        sort_by = DEFAULT_SORT_BY

    players = list(leaderboard_queryset(leaderboard, sort_by)[:limit])
    serializer = LeaderboardSerializer(leaderboard)

    return Response(
        {
            "leaderboard": serializer.data,
            "sort_by": sort_by,
            "entries": [
                leaderboard_entry(player, index)
                for index, player in enumerate(players, start=1)
            ],
        }
    )


@api_view(["GET", "PATCH"])
def leaderboard_settings(request):
    leaderboard = Leaderboard.get_active()

    if request.method == "GET":
        serializer = LeaderboardSerializer(leaderboard)
        return Response(serializer.data)

    serializer = LeaderboardSerializer(
        leaderboard,
        data=request.data,
        partial=True
    )

    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)

    return Response(serializer.errors, status=400)
