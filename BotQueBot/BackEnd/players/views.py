from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from matches.models import Match
from .models import Player
from .serializers import PlayerSerializer


RANK_RANGES = [
    (Player.MUSTARD_GAS, "MUSTARD GAS", 0, 1500),
    (Player.WOODHUMAN, "WOODHUMAN", 1500, 1600),
    (Player.GOODMASTER, "GOODMASTER", 1600, 1700),
    (Player.GREATMASTER, "GREATMASTER", 1700, 1800),
    (Player.GRANDMASTER, "GRANDMASTER", 1800, 2000),
    (Player.SUPER_GRANDMASTER, "SUPER GRANDMASTER", 2000, 2200),
    (Player.SUPER_GRANDMASTER_GOD, "SUPER GRANDMASTER GOD", 2200, None),
]


def percentage_position(position, total):
    if total <= 0 or position is None:
        return None

    return round((position / total) * 100, 1)


def position_for(players, player):
    for index, ranked_player in enumerate(players, start=1):
        if ranked_player.id == player.id:
            return index

    return None


def rank_progress_for(player):
    mmr = player.mmr

    for rank_key, rank_name, rank_floor, rank_ceiling in RANK_RANGES:
        if rank_ceiling is None or mmr < rank_ceiling:
            if rank_ceiling is None:
                progress = 100
                mmr_to_next = 0
            else:
                rank_width = rank_ceiling - rank_floor
                progress = ((mmr - rank_floor) / rank_width) * 100
                mmr_to_next = max(0, rank_ceiling - mmr)

            return {
                "rank": rank_key,
                "rank_display": rank_name,
                "rank_floor": rank_floor,
                "rank_ceiling": rank_ceiling,
                "progress_percent": round(max(0, min(100, progress)), 1),
                "mmr_to_next": round(mmr_to_next, 1),
            }

    return {
        "rank": Player.SUPER_GRANDMASTER_GOD,
        "rank_display": "SUPER GRANDMASTER GOD",
        "rank_floor": 2200,
        "rank_ceiling": None,
        "progress_percent": 100,
        "mmr_to_next": 0,
    }


def player_match_history(player):
    return player.match_players.filter(
        match__status=Match.COMPLETED,
        won__isnull=False,
        mmr_after__isnull=False,
    ).select_related("match").order_by("match__created_at", "match_id")


def match_history_entry(match_player):
    return {
        "match_id": match_player.match_id,
        "played_at": match_player.match.created_at,
        "map_name": match_player.match.map_name,
        "won": match_player.won,
        "mmr_before": match_player.mmr_before,
        "mmr_after": match_player.mmr_after,
        "mmr_change": match_player.mmr_change,
    }


@api_view(["GET", "POST"])
def players_list_create(request):
    if request.method == "GET":
        players = Player.objects.all()
        serializer = PlayerSerializer(players, many=True)
        return Response(serializer.data)

    if request.method == "POST":
        serializer = PlayerSerializer(data=request.data)

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "PATCH", "DELETE"])
def player_detail(request, pk):
    try:
        player = Player.objects.get(pk=pk)
    except Player.DoesNotExist:
        return Response(
            {"error": "Player not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    if request.method == "GET":
        serializer = PlayerSerializer(player)
        return Response(serializer.data)

    if request.method == "PATCH":
        serializer = PlayerSerializer(
            player,
            data=request.data,
            partial=True
        )

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == "DELETE":
        player.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
def player_stats_by_discord_id(request, discord_id):
    try:
        player = Player.objects.get(discord_id=str(discord_id))
    except Player.DoesNotExist:
        return Response(
            {"error": "Player not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    players = list(Player.objects.filter(banned=False))
    total_players = len(players)

    mmr_ranked = sorted(
        players,
        key=lambda ranked_player: (
            -ranked_player.mmr,
            -ranked_player.wins,
            ranked_player.losses,
            ranked_player.username.lower(),
            ranked_player.id,
        )
    )
    winrate_ranked = sorted(
        players,
        key=lambda ranked_player: (
            -ranked_player.winrate,
            -ranked_player.total_games,
            -ranked_player.mmr,
            ranked_player.username.lower(),
            ranked_player.id,
        )
    )
    wins_ranked = sorted(
        players,
        key=lambda ranked_player: (
            -ranked_player.wins,
            -ranked_player.mmr,
            ranked_player.losses,
            ranked_player.username.lower(),
            ranked_player.id,
        )
    )
    games_ranked = sorted(
        players,
        key=lambda ranked_player: (
            -ranked_player.total_games,
            -ranked_player.mmr,
            ranked_player.username.lower(),
            ranked_player.id,
        )
    )

    rank_position = position_for(mmr_ranked, player)
    winrate_position = position_for(winrate_ranked, player)
    wins_position = position_for(wins_ranked, player)
    games_position = position_for(games_ranked, player)

    history = list(player_match_history(player))
    recent_games = [
        match_history_entry(match_player)
        for match_player in reversed(history[-8:])
    ]
    mmr_history = [
        {
            "game": index,
            "match_id": match_player.match_id,
            "mmr": match_player.mmr_after,
            "played_at": match_player.match.created_at,
        }
        for index, match_player in enumerate(history, start=1)
    ]

    return Response(
        {
            "player": PlayerSerializer(player).data,
            "total_players": total_players,
            "rank_position": rank_position,
            "rank_top_percent": percentage_position(
                rank_position,
                total_players
            ),
            "winrate_position": winrate_position,
            "winrate_top_percent": percentage_position(
                winrate_position,
                total_players
            ),
            "wins_position": wins_position,
            "wins_top_percent": percentage_position(
                wins_position,
                total_players
            ),
            "games_position": games_position,
            "games_top_percent": percentage_position(
                games_position,
                total_players
            ),
            "rank_progress": rank_progress_for(player),
            "recent_games": recent_games,
            "mmr_history": mmr_history,
        }
    )
