from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from players.models import Player

from .models import ActiveMap, Match, MatchPlayer, RatingSettings
from .serializers import (
    ActiveMapSerializer,
    MatchSerializer,
    MatchPlayerSerializer,
    RatingSettingsSerializer,
)


TEAM_DIFF_ELO_DIVISOR = 50
PLAYER_AVERAGE_ELO_DIVISOR = 200
MAX_ROLE_TIER_WIN_BONUS = 1.5
ULTRA_BOSS_INSTINCT_STREAK_THRESHOLD = 4
MIN_ELO_CHANGE_ON_MATCH_RESULT = 10
PUNISH_CANCEL_MMR_PENALTY = 20
WIN_BY_PUNISH_MMR_AWARD = 10
LOCKED_MIN_RATING = 1400
UNLOCKED_MIN_RATING = 0


def valid_map_keys():
    return {
        map_key
        for map_key, _map_name in Match.PALADINS_MAP_CHOICES
    }


def current_min_rating():
    settings = RatingSettings.get_settings()

    if settings.lock_min_rating:
        return LOCKED_MIN_RATING

    return UNLOCKED_MIN_RATING


def update_player_after_match(player, won, mmr_after):
    player.mmr = mmr_after
    player.total_games += 1
    player.last_match_end = timezone.now()
    player.last_decay_at = None

    if won:
        player.wins += 1
        player.streak = player.streak + 1 if player.streak >= 0 else 1
    else:
        player.losses += 1
        player.streak = player.streak - 1 if player.streak <= 0 else -1

    player.peak_streak = max(player.peak_streak, player.streak)
    player.save()


def elo_change_for_match(match, winner, settings=None):
    settings = settings or RatingSettings.get_settings()
    team_diff = abs(match.team_1_mmr - match.team_2_mmr)
    team_diff_bonus = min(
        team_diff / TEAM_DIFF_ELO_DIVISOR,
        settings.win_team_diff_mmr_cap
    )

    team_1_is_underdog = match.team_1_mmr < match.team_2_mmr
    team_2_is_underdog = match.team_2_mmr < match.team_1_mmr
    winner_is_underdog = (
        (winner == Match.TEAM_1 and team_1_is_underdog)
        or (winner == Match.TEAM_2 and team_2_is_underdog)
    )

    if winner_is_underdog:
        elo_change = settings.win_base_mmr_change + team_diff_bonus
    else:
        elo_change = settings.win_base_mmr_change - team_diff_bonus

    return max(MIN_ELO_CHANGE_ON_MATCH_RESULT, elo_change)


def elo_loss_for_match(match, winner, settings=None):
    settings = settings or RatingSettings.get_settings()
    team_diff = abs(match.team_1_mmr - match.team_2_mmr)
    team_diff_bonus = min(
        team_diff / TEAM_DIFF_ELO_DIVISOR,
        settings.loss_team_diff_mmr_cap
    )

    team_1_is_underdog = match.team_1_mmr < match.team_2_mmr
    team_2_is_underdog = match.team_2_mmr < match.team_1_mmr
    winner_is_underdog = (
        (winner == Match.TEAM_1 and team_1_is_underdog)
        or (winner == Match.TEAM_2 and team_2_is_underdog)
    )

    if winner_is_underdog:
        elo_loss = settings.loss_base_mmr_change + team_diff_bonus
    else:
        elo_loss = settings.loss_base_mmr_change - team_diff_bonus

    return max(MIN_ELO_CHANGE_ON_MATCH_RESULT, elo_loss)


def player_average_elo_adjustment(match, player_mmr):
    match_average_mmr = (match.team_1_mmr + match.team_2_mmr) / 2
    return (match_average_mmr - player_mmr) / PLAYER_AVERAGE_ELO_DIVISOR


def capped_win_player_average_adjustment(match, player_mmr, settings=None):
    settings = settings or RatingSettings.get_settings()
    adjustment = player_average_elo_adjustment(match, player_mmr)
    return max(
        -settings.win_player_average_mmr_cap,
        min(settings.win_player_average_mmr_cap, adjustment)
    )


def capped_loss_player_average_adjustment(match, player_mmr, settings=None):
    settings = settings or RatingSettings.get_settings()
    adjustment = player_average_elo_adjustment(match, player_mmr)

    if adjustment >= 0:
        return min(settings.loss_player_average_mmr_relief_cap, adjustment)

    return max(-settings.loss_player_average_mmr_penalty_cap, adjustment)


def elo_change_for_match_player(match, match_player, winner, settings=None):
    settings = settings or RatingSettings.get_settings()
    won = match_player.team == winner
    role_tier = (
        match_player.role_tier_before
        if match_player.role_tier_before is not None
        else match_player.player.role_tier
    )
    streak = (
        match_player.streak_before
        if match_player.streak_before is not None
        else match_player.player.streak
    )

    if won:
        base_change = elo_change_for_match(match, winner, settings)
        personal_adjustment = capped_win_player_average_adjustment(
            match,
            match_player.mmr_before,
            settings
        )
        pre_bonus_elo_change = base_change + personal_adjustment
        elo_change = pre_bonus_elo_change
    else:
        base_change = elo_loss_for_match(match, winner, settings)
        personal_adjustment = capped_loss_player_average_adjustment(
            match,
            match_player.mmr_before,
            settings
        )
        elo_change = base_change - personal_adjustment

    if won:
        if role_tier == 2:
            role_bonus = (
                pre_bonus_elo_change
                * settings.role_tier_2_win_bonus_percent
            )
        elif role_tier == 1:
            role_bonus = (
                pre_bonus_elo_change
                * settings.role_tier_1_win_bonus_percent
            )
        else:
            role_bonus = 0

        elo_change += min(role_bonus, MAX_ROLE_TIER_WIN_BONUS)

        if streak >= ULTRA_BOSS_INSTINCT_STREAK_THRESHOLD:
            elo_change += (
                pre_bonus_elo_change
                * settings.ultra_boss_instinct_win_bonus_percent
            )

    return max(MIN_ELO_CHANGE_ON_MATCH_RESULT, elo_change)


def refresh_player_record_from_matches(player):
    completed_match_players = player.match_players.filter(
        match__status=Match.COMPLETED,
        won__isnull=False,
        match__completed_at__isnull=False,
    ).select_related("match").order_by("match__completed_at", "match_id")

    player.wins = 0
    player.losses = 0
    player.total_games = 0
    player.streak = 0
    player.peak_streak = 0
    player.last_match_end = None

    for match_player in completed_match_players:
        player.total_games += 1
        player.last_match_end = match_player.match.completed_at

        if match_player.won:
            player.wins += 1
            player.streak = player.streak + 1 if player.streak >= 0 else 1
        else:
            player.losses += 1
            player.streak = player.streak - 1 if player.streak <= 0 else -1

        player.peak_streak = max(player.peak_streak, player.streak)


def player_summary(player):
    return {
        "id": player.id,
        "username": player.username,
        "discord_id": player.discord_id,
        "mmr": player.mmr,
        "rank": player.rank,
        "rank_display": player.get_rank_display(),
    }


def apply_punish_cancel_penalty(player):
    player.mmr = max(
        current_min_rating(),
        player.mmr - PUNISH_CANCEL_MMR_PENALTY
    )
    player.save()


def apply_win_by_punish_award(player):
    player.mmr += WIN_BY_PUNISH_MMR_AWARD
    player.last_decay_at = None
    player.save()


def record_match_player_adjustment(match_player, player, mmr_before, won):
    match_player.mmr_before = mmr_before
    match_player.mmr_change = player.mmr - mmr_before
    match_player.mmr_after = player.mmr
    match_player.won = won
    match_player.save()


def player_decay_due_at(player, settings=None):
    settings = settings or RatingSettings.get_settings()

    if player.last_decay_at is not None:
        return player.last_decay_at + timedelta(
            days=settings.decay_repeat_every_days
        )

    last_activity = player.last_match_end or player.created_at
    return last_activity + timedelta(days=settings.decay_start_after_days)


def apply_rating_decay(player, now, settings=None):
    settings = settings or RatingSettings.get_settings()
    mmr_before = player.mmr
    player.mmr = max(current_min_rating(), player.mmr - settings.decay_mmr_loss)
    player.last_decay_at = now
    player.save()

    return {
        **player_summary(player),
        "mmr_before": mmr_before,
        "mmr_change": player.mmr - mmr_before,
        "decay_due_at": player_decay_due_at(player, settings),
    }


def revoke_completed_match(match):
    affected_players = []
    match.status = Match.CANCELLED
    match.winner = None
    match.save()

    for match_player in match.match_players.select_related("player"):
        player = Player.objects.select_for_update().get(
            pk=match_player.player_id
        )
        player.mmr -= match_player.mmr_change
        player.mmr = max(UNLOCKED_MIN_RATING, player.mmr)

        refresh_player_record_from_matches(player)
        player.save()
        affected_players.append(player)

    return affected_players


def complete_match(match, winner, completed_at=None):
    settings = RatingSettings.get_settings()
    match.status = Match.COMPLETED
    match.winner = winner
    match.completed_at = completed_at or timezone.now()
    match.save()

    for match_player in match.match_players.select_related("player"):
        player = Player.objects.select_for_update().get(
            pk=match_player.player_id
        )
        won = match_player.team == winner
        elo_change_for_player = elo_change_for_match_player(
            match,
            match_player,
            winner,
            settings
        )
        mmr_change = (
            elo_change_for_player
            if won
            else -elo_change_for_player
        )
        raw_mmr_after = player.mmr + mmr_change
        mmr_after = max(
            current_min_rating(),
            raw_mmr_after
        )
        mmr_change = mmr_after - player.mmr

        match_player.won = won
        match_player.mmr_change = mmr_change
        match_player.mmr_after = mmr_after
        match_player.save()

        update_player_after_match(player, won, mmr_after)


def reset_match_players_to_original_mmr(match):
    for match_player in match.match_players.select_related("player"):
        match_player.mmr_after = None
        match_player.mmr_change = 0
        match_player.won = None
        match_player.save()


@api_view(["POST"])
def revoke_match(request, pk):
    try:
        match = Match.objects.prefetch_related("match_players").get(pk=pk)
    except Match.DoesNotExist:
        return Response(
            {"error": "Match not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    with transaction.atomic():
        if match.status == Match.COMPLETED:
            affected_players = revoke_completed_match(match)
            match.refresh_from_db()
        else:
            affected_players = []
            match.status = Match.CANCELLED
            match.winner = None
            match.save()

    serializer = MatchSerializer(match)
    data = serializer.data
    data["affected_players"] = [
        player_summary(player)
        for player in affected_players
    ]
    return Response(data)


@api_view(["POST"])
def change_match_winner(request, pk):
    try:
        match = Match.objects.prefetch_related("match_players").get(pk=pk)
    except Match.DoesNotExist:
        return Response(
            {"error": "Match not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    if match.status != Match.COMPLETED or match.winner not in {
        Match.TEAM_1,
        Match.TEAM_2,
    }:
        return Response(
            {"error": "Only completed matches with a winner can be flipped."},
            status=status.HTTP_400_BAD_REQUEST
        )

    old_winner = match.winner
    new_winner = Match.TEAM_2 if old_winner == Match.TEAM_1 else Match.TEAM_1
    original_completed_at = match.completed_at or match.created_at

    with transaction.atomic():
        affected_players = revoke_completed_match(match)
        match.refresh_from_db()
        reset_match_players_to_original_mmr(match)
        complete_match(match, new_winner, original_completed_at)

        updated_players = [
            match_player.player
            for match_player in match.match_players.select_related("player")
        ]
        affected_by_id = {
            player.id: player
            for player in affected_players
        }

        for player in updated_players:
            affected_by_id[player.id] = player

        affected_players = list(affected_by_id.values())
        for player in affected_players:
            player.refresh_from_db()
            refresh_player_record_from_matches(player)
            player.save()

        match.refresh_from_db()

    serializer = MatchSerializer(match)
    data = serializer.data
    data["old_winner"] = old_winner
    data["new_winner"] = new_winner
    data["affected_players"] = [
        player_summary(player)
        for player in affected_players
    ]
    return Response(data)


@api_view(["POST"])
def set_match_winner(request, pk):
    try:
        match = Match.objects.prefetch_related("match_players").get(pk=pk)
    except Match.DoesNotExist:
        return Response(
            {"error": "Match not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    new_winner = request.data.get("winner")

    if new_winner not in {Match.TEAM_1, Match.TEAM_2}:
        return Response(
            {"error": "Winner must be team_1 or team_2."},
            status=status.HTTP_400_BAD_REQUEST
        )

    old_winner = match.winner
    original_completed_at = match.completed_at or match.created_at

    if match.status == Match.CANCELLED:
        return Response(
            {"error": "Cancelled matches cannot have a winner set."},
            status=status.HTTP_400_BAD_REQUEST
        )

    with transaction.atomic():
        affected_players = []

        if match.status == Match.COMPLETED:
            affected_players = revoke_completed_match(match)
            match.refresh_from_db()
            reset_match_players_to_original_mmr(match)

        complete_match(
            match,
            new_winner,
            original_completed_at if affected_players else None
        )

        updated_players = [
            match_player.player
            for match_player in match.match_players.select_related("player")
        ]
        affected_by_id = {
            player.id: player
            for player in affected_players
        }

        for player in updated_players:
            affected_by_id[player.id] = player

        affected_players = list(affected_by_id.values())
        for player in affected_players:
            player.refresh_from_db()
            refresh_player_record_from_matches(player)
            player.save()

        match.refresh_from_db()

    serializer = MatchSerializer(match)
    data = serializer.data
    data["old_winner"] = old_winner
    data["new_winner"] = new_winner
    data["affected_players"] = [
        player_summary(player)
        for player in affected_players
    ]
    return Response(data)


@api_view(["POST"])
def set_cancelled_match_winner(request, pk):
    try:
        match = Match.objects.prefetch_related("match_players").get(pk=pk)
    except Match.DoesNotExist:
        return Response(
            {"error": "Match not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    new_winner = request.data.get("winner")

    if new_winner not in {Match.TEAM_1, Match.TEAM_2}:
        return Response(
            {"error": "Winner must be team_1 or team_2."},
            status=status.HTTP_400_BAD_REQUEST
        )

    if match.status != Match.CANCELLED:
        return Response(
            {"error": "Only cancelled matches can be restored here."},
            status=status.HTTP_400_BAD_REQUEST
        )

    old_winner = match.winner
    restored_completed_at = match.completed_at

    with transaction.atomic():
        reset_match_players_to_original_mmr(match)
        complete_match(match, new_winner, restored_completed_at)

        affected_players = [
            match_player.player
            for match_player in match.match_players.select_related("player")
        ]

        for player in affected_players:
            player.refresh_from_db()
            refresh_player_record_from_matches(player)
            player.save()

        match.refresh_from_db()

    serializer = MatchSerializer(match)
    data = serializer.data
    data["old_winner"] = old_winner
    data["new_winner"] = new_winner
    data["affected_players"] = [
        player_summary(player)
        for player in affected_players
    ]
    return Response(data)


@api_view(["POST"])
def punish_cancel_match(request, pk):
    punished_discord_id = request.data.get("punished_discord_id")

    if punished_discord_id is None:
        return Response(
            {"punished_discord_id": "This field is required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        match = Match.objects.prefetch_related("match_players").get(pk=pk)
    except Match.DoesNotExist:
        return Response(
            {"error": "Match not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    punished_match_player = next(
        (
            match_player
            for match_player in match.match_players.select_related("player")
            if str(match_player.player.discord_id) == str(punished_discord_id)
        ),
        None
    )

    if punished_match_player is None:
        return Response(
            {"error": "Punished player was not in this match."},
            status=status.HTTP_400_BAD_REQUEST
        )

    if match.status == Match.CANCELLED:
        return Response(
            {"error": "Cancelled matches cannot be punished again."},
            status=status.HTTP_400_BAD_REQUEST
        )

    with transaction.atomic():
        if match.status == Match.COMPLETED:
            affected_players = revoke_completed_match(match)
            match.refresh_from_db()
            reset_match_players_to_original_mmr(match)
        elif match.status == Match.PENDING:
            affected_players = []
            match.status = Match.CANCELLED
            match.winner = None
            match.save()
        else:
            affected_players = []

        punished_player = Player.objects.select_for_update().get(
            pk=punished_match_player.player_id
        )
        mmr_before = punished_player.mmr
        apply_punish_cancel_penalty(punished_player)
        record_match_player_adjustment(
            punished_match_player,
            punished_player,
            mmr_before,
            False
        )

        affected_by_id = {
            player.id: player
            for player in affected_players
        }
        affected_by_id[punished_player.id] = punished_player
        affected_players = list(affected_by_id.values())
        match.refresh_from_db()

    serializer = MatchSerializer(match)
    data = serializer.data
    data["punished_player"] = player_summary(punished_player)
    data["punishment_mmr_change"] = -PUNISH_CANCEL_MMR_PENALTY
    data["affected_players"] = [
        player_summary(player)
        for player in affected_players
    ]
    return Response(data)


@api_view(["POST"])
def win_by_punish_match(request, pk):
    punished_discord_id = request.data.get("punished_discord_id")

    if punished_discord_id is None:
        return Response(
            {"punished_discord_id": "This field is required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        match = Match.objects.prefetch_related("match_players").get(pk=pk)
    except Match.DoesNotExist:
        return Response(
            {"error": "Match not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    match_players = list(match.match_players.select_related("player"))
    punished_match_player = next(
        (
            match_player
            for match_player in match_players
            if str(match_player.player.discord_id) == str(punished_discord_id)
        ),
        None
    )

    if punished_match_player is None:
        return Response(
            {"error": "Punished player was not in this match."},
            status=status.HTTP_400_BAD_REQUEST
        )

    winning_team = (
        Match.TEAM_2
        if punished_match_player.team == Match.TEAM_1
        else Match.TEAM_1
    )

    if match.status == Match.CANCELLED:
        return Response(
            {"error": "Cancelled matches cannot be awarded by punishment."},
            status=status.HTTP_400_BAD_REQUEST
        )

    with transaction.atomic():
        if match.status == Match.COMPLETED:
            affected_players = revoke_completed_match(match)
            match.refresh_from_db()
            reset_match_players_to_original_mmr(match)
            match_players = list(match.match_players.select_related("player"))
        else:
            affected_players = []
            match.status = Match.CANCELLED
            match.winner = None
            match.save()

        awarded_players = []

        for match_player in match_players:
            if match_player.team != winning_team:
                continue

            player = Player.objects.select_for_update().get(
                pk=match_player.player_id
            )
            mmr_before = player.mmr
            apply_win_by_punish_award(player)
            record_match_player_adjustment(match_player, player, mmr_before, True)
            awarded_players.append(player)

        affected_by_id = {
            player.id: player
            for player in affected_players
        }

        for player in awarded_players:
            affected_by_id[player.id] = player

        punished_player = Player.objects.select_for_update().get(
            pk=punished_match_player.player_id
        )
        mmr_before = punished_player.mmr
        apply_punish_cancel_penalty(punished_player)
        record_match_player_adjustment(
            punished_match_player,
            punished_player,
            mmr_before,
            False
        )
        affected_by_id[punished_player.id] = punished_player

        affected_players = list(affected_by_id.values())
        match.refresh_from_db()

    serializer = MatchSerializer(match)
    data = serializer.data
    data["punished_player"] = player_summary(punished_player)
    data["punishment_mmr_change"] = -PUNISH_CANCEL_MMR_PENALTY
    data["winning_team"] = winning_team
    data["award_mmr_change"] = WIN_BY_PUNISH_MMR_AWARD
    data["awarded_players"] = [
        player_summary(player)
        for player in awarded_players
    ]
    data["affected_players"] = [
        player_summary(player)
        for player in affected_players
    ]
    return Response(data)


@api_view(["GET", "POST"])
def matches_list_create(request):
    if request.method == "GET":
        matches = Match.objects.all().order_by("-created_at")
        serializer = MatchSerializer(matches, many=True)
        return Response(serializer.data)

    if request.method == "POST":
        requested_status = request.data.get("status", Match.PENDING)

        if requested_status != Match.PENDING:
            return Response(
                {"status": "New matches must start as pending."},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = MatchSerializer(data=request.data)

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "PATCH", "DELETE"])
def match_detail(request, pk):
    try:
        match = Match.objects.get(pk=pk)
    except Match.DoesNotExist:
        return Response(
            {"error": "Match not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    if request.method == "GET":
        serializer = MatchSerializer(match)
        return Response(serializer.data)

    if request.method == "PATCH":
        winner = request.data.get("winner")
        status_value = request.data.get("status")

        if status_value == Match.COMPLETED:
            if not winner:
                return Response(
                    {"error": "Completed matches require a winner."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if winner not in {Match.TEAM_1, Match.TEAM_2}:
                return Response(
                    {"error": "Winner must be team_1 or team_2."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if match.status != Match.PENDING:
                return Response(
                    {"error": "Only pending matches can be completed here."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            with transaction.atomic():
                complete_match(match, winner)
                match.refresh_from_db()

            serializer = MatchSerializer(match)
            return Response(serializer.data)

        if status_value == Match.CANCELLED:
            if match.status == Match.COMPLETED:
                return Response(
                    {
                        "error": (
                            "Completed matches must be cancelled through the "
                            "revoke endpoint so MMR is reversed correctly."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
        elif status_value is not None:
            return Response(
                {"error": "Use a match action endpoint to change status."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if winner is not None:
            return Response(
                {"error": "Use a winner endpoint to change match winners."},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = MatchSerializer(match, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == "DELETE":
        if match.status == Match.COMPLETED:
            return Response(
                {
                    "error": (
                        "Completed matches must be revoked before they can "
                        "be deleted."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        match.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
def add_match_player(request):
    serializer = MatchPlayerSerializer(data=request.data)

    if serializer.is_valid():
        match = serializer.validated_data["match"]

        if match.status != Match.PENDING:
            return Response(
                {"match": "Players can only be added to pending matches."},
                status=status.HTTP_400_BAD_REQUEST
            )

        player = serializer.validated_data["player"]
        match_player = serializer.save(
            role_tier_before=(
                serializer.validated_data.get("role_tier_before")
                if serializer.validated_data.get("role_tier_before") is not None
                else player.role_tier
            ),
            streak_before=(
                serializer.validated_data.get("streak_before")
                if serializer.validated_data.get("streak_before") is not None
                else player.streak
            ),
        )
        return Response(
            MatchPlayerSerializer(match_player).data,
            status=status.HTTP_201_CREATED
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET", "POST"])
def active_maps_list_create(request):
    if request.method == "GET":
        active_maps = ActiveMap.objects.all()
        serializer = ActiveMapSerializer(active_maps, many=True)
        return Response(serializer.data)

    map_name = request.data.get("map_name")

    if map_name not in valid_map_keys():
        return Response(
            {"map_name": "Invalid map."},
            status=status.HTTP_400_BAD_REQUEST
        )

    active_map, created = ActiveMap.objects.get_or_create(
        map_name=map_name
    )
    serializer = ActiveMapSerializer(active_map)

    return Response(
        serializer.data,
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )


@api_view(["DELETE"])
def active_map_detail(request, map_name):
    deleted_count, _deleted = ActiveMap.objects.filter(
        map_name=map_name
    ).delete()

    if deleted_count == 0:
        return Response(
            {"error": "Active map not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET", "PATCH"])
def rating_settings_detail(request):
    settings = RatingSettings.get_settings()

    if request.method == "GET":
        serializer = RatingSettingsSerializer(settings)
        return Response(serializer.data)

    allowed_fields = {
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
        "ultra_boss_instinct_win_bonus",
        "ultra_boss_instinct_win_bonus_percent",
    }

    if not any(field in request.data for field in allowed_fields):
        return Response(
            {"error": "No rating settings were provided."},
            status=status.HTTP_400_BAD_REQUEST
        )

    if Match.objects.filter(status=Match.PENDING).exists():
        return Response(
            {
                "error": (
                    "Finish or cancel pending matches before changing rating "
                    "settings."
                )
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    if "lock_min_rating" in request.data:
        lock_min_rating = request.data.get("lock_min_rating")

        if not isinstance(lock_min_rating, bool):
            return Response(
                {"lock_min_rating": "Expected true or false."},
                status=status.HTTP_400_BAD_REQUEST
            )

        settings.lock_min_rating = lock_min_rating

    for field in (
        "decay_start_after_days",
        "decay_repeat_every_days",
        "decay_mmr_loss",
    ):
        if field not in request.data:
            continue

        value = request.data.get(field)

        if not isinstance(value, int) or value < 0:
            return Response(
                {field: "Expected a non-negative integer."},
                status=status.HTTP_400_BAD_REQUEST
            )

        setattr(settings, field, value)

    for field in (
        "win_base_mmr_change",
        "loss_base_mmr_change",
        "win_team_diff_mmr_cap",
        "win_player_average_mmr_cap",
        "loss_team_diff_mmr_cap",
        "loss_player_average_mmr_relief_cap",
        "loss_player_average_mmr_penalty_cap",
        "role_tier_2_win_bonus_percent",
        "role_tier_1_win_bonus_percent",
        "ultra_boss_instinct_win_bonus",
        "ultra_boss_instinct_win_bonus_percent",
    ):
        if field not in request.data:
            continue

        value = request.data.get(field)

        if not isinstance(value, (int, float)) or value < 0:
            return Response(
                {field: "Expected a non-negative number."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if field == "ultra_boss_instinct_win_bonus":
            settings.ultra_boss_instinct_win_bonus_percent = value
        else:
            setattr(settings, field, value)

    settings.save()
    clamped_players = 0

    if settings.lock_min_rating:
        clamped_players = Player.objects.filter(
            mmr__lt=LOCKED_MIN_RATING
        ).update(mmr=LOCKED_MIN_RATING)

    serializer = RatingSettingsSerializer(settings)
    data = serializer.data
    data["min_rating"] = current_min_rating()
    data["clamped_players"] = clamped_players

    return Response(data)


@api_view(["POST"])
def run_rating_decay(request):
    now = timezone.now()
    decayed_players = []
    settings = RatingSettings.get_settings()

    with transaction.atomic():
        players = Player.objects.select_for_update().all()

        for player in players:
            if now < player_decay_due_at(player, settings):
                continue

            decayed_players.append(apply_rating_decay(player, now, settings))

    return Response(
        {
            "decayed_players": decayed_players,
            "decayed_count": len(decayed_players),
            "decay_start_after_days": settings.decay_start_after_days,
            "decay_repeat_every_days": settings.decay_repeat_every_days,
            "decay_mmr_loss": settings.decay_mmr_loss,
        }
    )


@api_view(["PATCH"])
def match_player_detail(request, pk):
    try:
        match_player = MatchPlayer.objects.get(pk=pk)
    except MatchPlayer.DoesNotExist:
        return Response(
            {"error": "Match player not found"},
            status=status.HTTP_404_NOT_FOUND
        )

    protected_fields = {
        "match",
        "player",
        "team",
        "mmr_before",
        "mmr_after",
        "mmr_change",
        "role_tier_before",
        "streak_before",
        "won",
    }

    if any(field in request.data for field in protected_fields):
        return Response(
            {
                "error": (
                    "Match player result fields are managed by match "
                    "action endpoints."
                )
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    serializer = MatchPlayerSerializer(
        match_player,
        data=request.data,
        partial=True
    )

    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
