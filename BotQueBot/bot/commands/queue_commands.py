import requests
import discord
import random
import asyncio
import json
import re
from io import BytesIO
from datetime import datetime, timezone, timedelta
from discord import Interaction
from pathlib import Path
from typing import Literal, Optional
from PIL import Image, ImageDraw, ImageFont, ImageOps

from config import (
    DJANGO_API_URL,
    NEATQUEUE_API_URL,
    NEATQUEUE_AUTHORIZATION,
    NEATQUEUE_PLAYERS_URL,
)
import queue_state
from queue_state import current_queue, last_queue_action
from embeds.queue_embed import (
    build_admin_match_embed,
    build_match_embed,
    build_match_result_embed,
    build_map_vote_embed,
    build_ready_check_embed,
)
from elo_nickname import (
    find_member,
    save_elo_nickname_enabled,
    sync_all_elo_nicknames,
    sync_member_elo_nickname,
)
from rank_roles import (
    sync_all_losing_streak_roles,
    sync_all_rank_roles,
    sync_all_ultra_boss_instinct_roles,
    sync_member_losing_streak_role,
    sync_member_rank_role,
    sync_member_ultra_boss_instinct_role,
)
from guild_config import (
    admin_match_panel_channel_id,
    bot_report_channel_id,
    elo_nickname_enabled as guild_elo_nickname_enabled,
    in_game_role_id,
    in_queue_role_id,
    match_results_channel_id,
    queue_channel_id,
    rank_role_sync_enabled,
    upsert_guild_config,
)
from matchmaking import find_best_match_for_queue
from matchmaking import PALADINS_MAPS


RoleChoice = Literal[
    "tank",
    "dps",
    "sup",
    "dps_tank",
    "tank_sup",
    "dps_sup",
    "flex",
]

MapChoice = Literal[
    "ascension_peak",
    "bazaar",
    "brightmarsh",
    "frog_isle",
    "frozen_guard",
    "fish_market",
    "ice_mines",
    "jaguar_falls",
    "serpent_beach",
    "shattered_desert",
    "splitstone_quarry",
    "stone_keep",
    "timber_mill",
    "warders_gate",
    "dawnforge",
]
ToggleChoice = Literal["on", "off"]
RegionChoice = Literal["NA", "EU", "LATAM"]

MAPS_BY_KEY = {
    map_choice["key"]: map_choice
    for map_choice in PALADINS_MAPS
}
MAP_VOTE_POOL_1_KEYS = {
    "ascension_peak",
    "brightmarsh",
    "ice_mines",
    "jaguar_falls",
    "serpent_beach",
    "splitstone_quarry",
    "stone_keep",
}
MAP_VOTE_POOL_2_KEYS = {
    "bazaar",
    "dawnforge",
    "fish_market",
    "frog_isle",
    "frozen_guard",
    "shattered_desert",
    "timber_mill",
    "warders_gate",
}
MAP_VOTE_SECOND_OPTION_POOL_1_CHANCE = 0.8

ROLE_DISCORD_NAMES = {
    "tank": "tank",
    "dps": "dps",
    "sup": "sup",
    "dps_tank": "dps_tank",
    "tank_sup": "tank_sup",
    "dps_sup": "dps_sup",
    "flex": "flex",
}
ROLE_CHANGE_COOLDOWN = timedelta(hours=8)
SELF_CHANGE_BLOCKED_ROLES = {"sup"}
READY_CHECK_TIMEOUT_SECONDS = 180
READY_CHECK_MISSED_PENALTY = 5
MAP_VOTE_TIMEOUT_SECONDS = 20
CANCEL_VOTE_YES_THRESHOLD = 9
MATCH_RESULT_BUTTON_DELAY_SECONDS = 5
LEADERBOARD_PAGE_SIZE = 10
LEADERBOARD_LIMIT = 100
LEADERBOARD_METRICS = {
    "mmr": "MMR",
    "winrate": "Winrate",
    "wins": "Wins",
    "games": "Games Played",
    "streak": "Current Streak",
    "peak_streak": "Peak Streak",
}
MAP_IMAGE_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "BackEnd"
    / "media"
    / "maps"
)
MAP_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"]
MAP_VOTE_VISIBLE_MAP_COUNT = 3
NEATQUEUE_TEST_OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "neatqueuetest.txt"
)


def register_queue_commands(bot, send_queue_panel):
    active_ready_checks = []
    active_matches_by_thread_id = {}
    active_cancel_votes_by_thread_id = {}
    active_admin_match_views_by_match_id = {}

    def player_discord_id_value(player):
        if isinstance(player, dict):
            discord_id = player.get("discord_id")
        else:
            discord_id = getattr(player, "discord_id", None)

        return str(discord_id) if discord_id is not None else None

    def reserve_players_for_match_flow(players):
        for player in players:
            discord_id = player_discord_id_value(player)

            if discord_id is not None:
                queue_state.active_match_discord_ids.add(discord_id)

    def release_players_from_match_flow(players):
        for player in players:
            discord_id = player_discord_id_value(player)

            if discord_id is not None:
                queue_state.active_match_discord_ids.discard(discord_id)

    def match_players_for_reservation(match):
        return list(match.get("team_1", [])) + list(match.get("team_2", []))

    def discord_id_reserved_for_match(discord_id):
        return str(discord_id) in queue_state.active_match_discord_ids

    async def get_channel(channel_id):
        channel = bot.get_channel(channel_id)

        if channel is not None:
            return channel

        return await bot.fetch_channel(channel_id)

    def guild_id_from_context(context=None):
        guild = getattr(context, "guild", None)

        if guild is not None:
            return guild.id

        if bot.guilds:
            return bot.guilds[0].id

        return None

    async def send_bot_report(message, context=None):
        try:
            channel = await get_channel(
                bot_report_channel_id(guild_id_from_context(context))
            )
            await channel.send(message)
        except discord.HTTPException:
            pass

    async def send_match_result(embed, context=None):
        guild_id = guild_id_from_context(context)

        try:
            channel = await get_channel(match_results_channel_id(guild_id))
            await channel.send(embed=embed)
        except discord.HTTPException as error:
            await send_bot_report(
                (
                    "Could not send match result panel to the configured "
                    f"match-results channel: {error}"
                ),
                context
            )

    async def configured_queue_channel(context=None):
        channel_id = queue_channel_id(guild_id_from_context(context))

        if channel_id is None:
            return None

        try:
            return await get_channel(channel_id)
        except discord.HTTPException:
            return None

    async def refresh_configured_queue_panel(context=None):
        channel = await configured_queue_channel(context)

        if channel is None:
            return None

        await send_queue_panel(channel)
        return channel

    def average_team_mmr(team):
        return sum(player.elo for player in team) / len(team)

    def assigned_role_for_player(player, assigned_roles):
        return assigned_roles.get(
            player.name,
            player.possible_roles()[0]
        )

    def display_match_number(match):
        backend_match = match.get("backend_match") or {}

        return (
            match.get("backend_match_number")
            or backend_match.get("match_number")
            or match.get("backend_match_id")
            or backend_match.get("id")
        )

    def fetch_backend_match_by_number(match_number):
        url = f"{DJANGO_API_URL}/matches/"

        while url:
            response = requests.get(url)
            response.raise_for_status()

            payload = response.json()

            if isinstance(payload, dict):
                matches = payload.get("results", [])
                url = payload.get("next")
            else:
                matches = payload
                url = None

            for match in matches:
                if str(match.get("match_number")) == str(match_number):
                    return match

        return None

    def latest_completed_match_map_key():
        response = requests.get(f"{DJANGO_API_URL}/matches/")
        response.raise_for_status()

        payload = response.json()

        if isinstance(payload, dict):
            matches = payload.get("results", [])
        else:
            matches = payload

        for match in matches:
            if match.get("status") == "completed" and match.get("map_name"):
                return match["map_name"]

        return None

    def create_backend_match(match):
        response = requests.post(
            f"{DJANGO_API_URL}/matches/",
            json={
                "status": "pending",
                "map_name": match["map"]["key"],
                "team_1_mmr": average_team_mmr(match["team_1"]),
                "team_2_mmr": average_team_mmr(match["team_2"]),
                "mmr_difference": match["elo_diff"],
                "role_score": match.get("role_score", 0),
            },
        )
        response.raise_for_status()

        backend_match = response.json()
        match["backend_match_id"] = backend_match["id"]
        match["backend_match_number"] = (
            backend_match.get("match_number")
            or backend_match["id"]
        )

        try:
            for team_key, team_name, role_key in (
                ("team_1", "team_1", "team_1_roles"),
                ("team_2", "team_2", "team_2_roles"),
            ):
                assigned_roles = match.get(role_key, {})

                for player in match[team_key]:
                    response = requests.post(
                        f"{DJANGO_API_URL}/match-players/",
                        json={
                            "match": backend_match["id"],
                            "player": player.backend_id,
                            "team": team_name,
                            "assigned_role": assigned_role_for_player(
                                player,
                                assigned_roles
                            ),
                            "mmr_before": player.elo,
                            "mmr_change": 0,
                        },
                    )
                    response.raise_for_status()
        except requests.RequestException:
            requests.delete(f"{DJANGO_API_URL}/matches/{backend_match['id']}/")
            raise

        return backend_match

    def complete_backend_match(match, winner):
        backend_match_id = match.get("backend_match_id")

        if backend_match_id is None:
            return

        winner_key = "team_1" if winner == "Team 1" else "team_2"

        response = requests.patch(
            f"{DJANGO_API_URL}/matches/{backend_match_id}/",
            json={
                "status": "completed",
                "winner": winner_key,
            },
        )
        response.raise_for_status()
        match["backend_match"] = response.json()

    def cancel_backend_match(match):
        backend_match_id = match.get("backend_match_id")

        if backend_match_id is None:
            return

        response = requests.patch(
            f"{DJANGO_API_URL}/matches/{backend_match_id}/",
            json={
                "status": "cancelled",
            },
        )
        response.raise_for_status()
        match["backend_match"] = response.json()

    def revoke_backend_match(match_id):
        response = requests.post(
            f"{DJANGO_API_URL}/matches/{match_id}/revoke/"
        )
        response.raise_for_status()
        return response.json()

    def change_backend_match_winner(match_id):
        response = requests.post(
            f"{DJANGO_API_URL}/matches/{match_id}/change-winner/"
        )
        response.raise_for_status()
        return response.json()

    def fetch_backend_match(match_id):
        response = requests.get(f"{DJANGO_API_URL}/matches/{match_id}/")
        response.raise_for_status()
        return response.json()

    def set_backend_match_winner(match_id, winner):
        response = requests.post(
            f"{DJANGO_API_URL}/matches/{match_id}/set-winner/",
            json={
                "winner": winner,
            },
        )
        response.raise_for_status()
        return response.json()

    def punish_cancel_backend_match(match_id, punished_discord_id):
        response = requests.post(
            f"{DJANGO_API_URL}/matches/{match_id}/punish-cancel/",
            json={
                "punished_discord_id": str(punished_discord_id),
            },
        )
        response.raise_for_status()
        return response.json()

    def win_by_punish_backend_match(match_id, punished_discord_id):
        response = requests.post(
            f"{DJANGO_API_URL}/matches/{match_id}/win-by-punish/",
            json={
                "punished_discord_id": str(punished_discord_id),
            },
        )
        response.raise_for_status()
        return response.json()

    def reset_local_queue_state():
        current_queue.clear()
        last_queue_action["type"] = None
        last_queue_action["player"] = None
        last_queue_action["discord_id"] = None
        last_queue_action["mmr"] = None
        last_queue_action["message"] = None
        queue_state.match_result_pending = False
        queue_state.last_queue_activity_at = None

    def mark_queue_activity():
        queue_state.last_queue_activity_at = discord.utils.utcnow()

    def clear_last_queue_action():
        last_queue_action["type"] = None
        last_queue_action["player"] = None
        last_queue_action["discord_id"] = None
        last_queue_action["mmr"] = None
        last_queue_action["message"] = None

    def requeue_players_at_front(players):
        queued_player_ids = {
            player["id"]
            for player in current_queue
        }

        for player in reversed(players):
            if player["id"] not in queued_player_ids:
                current_queue.insert(0, player)
                queued_player_ids.add(player["id"])

        mark_queue_activity()

    def queued_players_for_next_match():
        if len(current_queue) < 10:
            return []

        priority_players = current_queue[:9]
        remaining_players = current_queue[9:]
        open_slots = 10 - len(priority_players)
        highest_mmr_players = sorted(
            remaining_players,
            key=lambda player: float(player.get("mmr", 0)),
            reverse=True
        )[:open_slots]

        return priority_players + highest_mmr_players

    def remove_players_from_current_queue(players):
        player_ids_to_remove = {
            player["id"]
            for player in players
        }
        current_queue[:] = [
            player
            for player in current_queue
            if player["id"] not in player_ids_to_remove
        ]

    def cancel_pending_backend_matches():
        response = requests.get(f"{DJANGO_API_URL}/matches/")
        response.raise_for_status()

        pending_matches = [
            match
            for match in response.json()
            if match["status"] == "pending"
        ]

        for match in pending_matches:
            response = requests.patch(
                f"{DJANGO_API_URL}/matches/{match['id']}/",
                json={
                    "status": "cancelled",
                },
            )
            response.raise_for_status()

        return pending_matches

    def pending_backend_matches():
        response = requests.get(f"{DJANGO_API_URL}/matches/")
        response.raise_for_status()

        return [
            match
            for match in response.json()
            if match["status"] == "pending"
        ]

    def find_backend_player_by_discord_id(discord_id):
        response = requests.get(f"{DJANGO_API_URL}/players/")
        response.raise_for_status()

        return next(
            (
                player
                for player in response.json()
                if str(player["discord_id"]) == str(discord_id)
            ),
            None
        )

    def neatqueue_players_url():
        if NEATQUEUE_PLAYERS_URL:
            return NEATQUEUE_PLAYERS_URL

        if NEATQUEUE_API_URL:
            return f"{NEATQUEUE_API_URL.rstrip('/')}/players"

        return None

    def convert_neatqueue_mmr(mmr):
        x = float(mmr)
        return 1500 + ((((x - 1500) / 900) ** 2) * 400)

    def neatqueue_player_mmr(player):
        queues = player.get("queues")

        if isinstance(queues, dict):
            player_stats = queues.get("player_stats")

            if (
                isinstance(player_stats, dict)
                and player_stats.get("mmr") is not None
            ):
                return player_stats["mmr"]

        return player.get("mmr")

    def neatqueue_player_entries(payload):
        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            for key in ("players", "playerstats", "stats", "data", "results"):
                entries = payload.get(key)

                if isinstance(entries, list):
                    return entries

            values = list(payload.values())

            if values and all(isinstance(value, dict) for value in values):
                return values

        return []

    def fetch_neatqueue_player_by_discord_id(discord_id):
        players_url = neatqueue_players_url()

        if not players_url or not NEATQUEUE_AUTHORIZATION:
            return None

        response = requests.get(
            players_url,
            headers={"Authorization": NEATQUEUE_AUTHORIZATION},
            timeout=30,
        )
        response.raise_for_status()

        return next(
            (
                player
                for player in neatqueue_player_entries(response.json())
                if str(player.get("discord_id")) == str(discord_id)
            ),
            None
        )

    def create_backend_player_from_user(user, mmr=None):
        payload = {
            "discord_id": str(user.id),
            "username": user.display_name,
            "avatar_url": str(user.display_avatar.url),
            "region": "EU",
            "role_preference": "flex",
        }

        if mmr is not None:
            payload["mmr"] = mmr

        response = requests.post(
            f"{DJANGO_API_URL}/players/",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def get_or_create_backend_player(user):
        player = find_backend_player_by_discord_id(user.id)

        if player is not None:
            return player

        return create_backend_player_from_user(user)

    def find_backend_player_by_username(username):
        response = requests.get(f"{DJANGO_API_URL}/players/")
        response.raise_for_status()

        return next(
            (
                player
                for player in response.json()
                if player["username"].lower() == username.lower()
            ),
            None
        )

    def find_backend_player_by_id(player_id):
        response = requests.get(f"{DJANGO_API_URL}/players/{player_id}/")
        response.raise_for_status()
        return response.json()

    def all_backend_players():
        response = requests.get(f"{DJANGO_API_URL}/players/")
        response.raise_for_status()
        return response.json()

    def update_player_role(player_id, role, record_role_change=False):
        payload = {
            "role_preference": role,
        }

        if record_role_change:
            payload["last_role_change_at"] = (
                discord.utils.utcnow().isoformat()
            )

        response = requests.patch(
            f"{DJANGO_API_URL}/players/{player_id}/",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    def update_player_ign(player_id, ign):
        response = requests.patch(
            f"{DJANGO_API_URL}/players/{player_id}/",
            json={
                "ign": ign,
            },
        )
        response.raise_for_status()
        return response.json()

    def update_player_region(player_id, region):
        response = requests.patch(
            f"{DJANGO_API_URL}/players/{player_id}/",
            json={
                "region": region,
            },
        )
        response.raise_for_status()
        return response.json()

    def update_player_mmr(player_id, mmr):
        response = requests.patch(
            f"{DJANGO_API_URL}/players/{player_id}/",
            json={
                "mmr": mmr,
            },
        )
        response.raise_for_status()
        return response.json()

    def active_map_pool():
        response = requests.get(f"{DJANGO_API_URL}/active-maps/")
        response.raise_for_status()

        active_map_keys = [
            active_map["map_name"]
            for active_map in response.json()
        ]
        active_maps = [
            MAPS_BY_KEY[map_key]
            for map_key in active_map_keys
            if map_key in MAPS_BY_KEY
        ]

        if not active_maps:
            return PALADINS_MAPS

        return active_maps

    def add_active_map(map_name):
        response = requests.post(
            f"{DJANGO_API_URL}/active-maps/",
            json={
                "map_name": map_name,
            },
        )
        response.raise_for_status()
        return response.json()

    def remove_active_map(map_name):
        response = requests.delete(
            f"{DJANGO_API_URL}/active-maps/{map_name}/"
        )
        response.raise_for_status()

    def list_active_maps():
        response = requests.get(f"{DJANGO_API_URL}/active-maps/")
        response.raise_for_status()
        return response.json()

    def update_rating_settings(lock_min_rating):
        response = requests.patch(
            f"{DJANGO_API_URL}/rating-settings/",
            json={
                "lock_min_rating": lock_min_rating,
            },
        )
        response.raise_for_status()
        return response.json()

    def update_decay_settings(start_after_days, repeat_every_days, mmr_loss):
        response = requests.patch(
            f"{DJANGO_API_URL}/rating-settings/",
            json={
                "decay_start_after_days": start_after_days,
                "decay_repeat_every_days": repeat_every_days,
                "decay_mmr_loss": mmr_loss,
            },
        )
        response.raise_for_status()
        return response.json()

    def update_elo_change_settings(
        win_base,
        loss_base,
        win_team_cap,
        win_player_cap,
        loss_team_cap,
        loss_relief_cap,
        loss_penalty_cap,
        tier_2_bonus_percent,
        tier_1_bonus_percent,
        ultra_bonus_percent
    ):
        response = requests.patch(
            f"{DJANGO_API_URL}/rating-settings/",
            json={
                "win_base_mmr_change": win_base,
                "loss_base_mmr_change": loss_base,
                "win_team_diff_mmr_cap": win_team_cap,
                "win_player_average_mmr_cap": win_player_cap,
                "loss_team_diff_mmr_cap": loss_team_cap,
                "loss_player_average_mmr_relief_cap": loss_relief_cap,
                "loss_player_average_mmr_penalty_cap": loss_penalty_cap,
                "role_tier_2_win_bonus_percent": tier_2_bonus_percent / 100,
                "role_tier_1_win_bonus_percent": tier_1_bonus_percent / 100,
                "ultra_boss_instinct_win_bonus_percent": (
                    ultra_bonus_percent / 100
                ),
            },
        )
        response.raise_for_status()
        return response.json()

    def rating_settings():
        response = requests.get(f"{DJANGO_API_URL}/rating-settings/")
        response.raise_for_status()
        return response.json()

    def fetch_leaderboard(sort_by="mmr"):
        response = requests.get(
            f"{DJANGO_API_URL}/leaderboard/",
            params={
                "limit": LEADERBOARD_LIMIT,
                "sort_by": sort_by,
            }
        )
        response.raise_for_status()
        return response.json()

    def fetch_player_stats(discord_id):
        response = requests.get(
            f"{DJANGO_API_URL}/players/discord/{discord_id}/stats/",
            timeout=10
        )

        if response.status_code == 404:
            return None

        response.raise_for_status()
        return response.json()

    def run_backend_rating_decay():
        response = requests.post(f"{DJANGO_API_URL}/rating-decay/run/")
        response.raise_for_status()
        return response.json()

    def sync_queued_player(updated_player):
        for index, player in enumerate(current_queue):
            if player["id"] == updated_player["id"]:
                current_queue[index] = updated_player
                return True

        return False

    def parse_backend_datetime(value):
        if not value:
            return None

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def role_change_cooldown_remaining(player):
        last_changed_at = parse_backend_datetime(
            player.get("last_role_change_at")
        )

        if last_changed_at is None:
            return None

        ready_at = last_changed_at + ROLE_CHANGE_COOLDOWN
        remaining = ready_at - discord.utils.utcnow()

        if remaining.total_seconds() <= 0:
            return None

        return remaining

    def format_timedelta(duration):
        total_seconds = max(0, int(duration.total_seconds()))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        if hours and minutes:
            return f"{hours}h {minutes}m"

        if hours:
            return f"{hours}h"

        return f"{minutes}m"

    def member_has_in_game_role(member):
        role_id = in_game_role_id(member.guild.id)

        return any(
            role.id == role_id
            for role in member.roles
        ) if role_id else False

    async def sync_discord_role(member, role):
        role_names = set(ROLE_DISCORD_NAMES.values())
        roles_to_remove = [
            discord_role
            for discord_role in member.roles
            if discord_role.name in role_names
        ]
        role_to_add = discord.utils.get(
            member.guild.roles,
            name=ROLE_DISCORD_NAMES[role]
        )

        if role_to_add is None:
            raise ValueError(
                f"Discord role `{ROLE_DISCORD_NAMES[role]}` was not found."
            )

        if roles_to_remove:
            await member.remove_roles(
                *roles_to_remove,
                reason="Round Table queue role changed"
            )

        await member.add_roles(
            role_to_add,
            reason="Round Table queue role changed"
        )

    async def add_in_game_role(member):
        role_id = in_game_role_id(member.guild.id)
        role = member.guild.get_role(role_id) if role_id else None

        if role is None:
            raise ValueError(
                "The configured in-game Discord role was not found."
            )

        await member.add_roles(
            role,
            reason="Round Table match started"
        )

    async def remove_in_game_role(member):
        role_id = in_game_role_id(member.guild.id)
        role = member.guild.get_role(role_id) if role_id else None

        if role is None or role not in member.roles:
            return

        await member.remove_roles(
            role,
            reason="Round Table match finished"
        )

    async def add_in_queue_role(member):
        role_id = in_queue_role_id(member.guild.id)
        role = member.guild.get_role(role_id) if role_id else None

        if role is None or role in member.roles:
            return

        await member.add_roles(
            role,
            reason="Round Table queue joined"
        )

    async def remove_in_queue_role(member):
        role_id = in_queue_role_id(member.guild.id)
        role = member.guild.get_role(role_id) if role_id else None

        if role is None or role not in member.roles:
            return

        await member.remove_roles(
            role,
            reason="Round Table queue left"
        )

    async def add_in_queue_role_to_players(channel, players):
        for player in players:
            if player.get("discord_id") is None:
                continue

            try:
                member = await find_member(
                    bot,
                    player["discord_id"],
                    channel.guild.id
                )

                if member is None:
                    continue

                await add_in_queue_role(member)
            except (discord.Forbidden, discord.HTTPException, ValueError):
                await send_bot_report(
                    f"Could not add the in-queue role to {player['username']}.",
                    channel
                )

    async def remove_in_queue_role_from_players(channel, players):
        for player in players:
            if player.get("discord_id") is None:
                continue

            try:
                member = await find_member(
                    bot,
                    player["discord_id"],
                    channel.guild.id
                )

                if member is None:
                    continue

                await remove_in_queue_role(member)
            except (discord.Forbidden, discord.HTTPException, ValueError):
                await send_bot_report(
                    f"Could not remove the in-queue role from {player['username']}.",
                    channel
                )

    async def add_in_game_role_to_match(channel, match):
        for player in match["team_1"] + match["team_2"]:
            if player.discord_id is None:
                continue

            try:
                member = await find_member(
                    bot,
                    player.discord_id,
                    channel.guild.id
                )

                if member is None:
                    continue

                await add_in_game_role(member)
            except (
                discord.Forbidden,
                discord.HTTPException,
                ValueError
            ):
                await send_bot_report(
                    f"Could not add the in-game role to {player.name}.",
                    channel
                )

    async def add_in_game_role_to_players(channel, players):
        for player in players:
            if player.get("discord_id") is None:
                continue

            try:
                member = await find_member(
                    bot,
                    player["discord_id"],
                    channel.guild.id
                )

                if member is None:
                    continue

                await add_in_game_role(member)
            except (
                discord.Forbidden,
                discord.HTTPException,
                ValueError
            ):
                await send_bot_report(
                    f"Could not add the in-game role to {player['username']}.",
                    channel
                )

    async def add_in_game_role_to_players_then_release(channel, players):
        try:
            await add_in_game_role_to_players(channel, players)
        finally:
            release_players_from_match_flow(players)

    def match_player_mentions(match):
        mentions = []

        for player in match["team_1"] + match["team_2"]:
            if player.discord_id is not None:
                mentions.append(f"<@{player.discord_id}>")

        return " ".join(mentions)

    def queued_player_mentions(players):
        mentions = []

        for player in players:
            if player.get("discord_id") is not None:
                mentions.append(f"<@{player['discord_id']}>")

        return " ".join(mentions)

    def match_player_discord_ids(match):
        return {
            int(player.discord_id)
            for player in match["team_1"] + match["team_2"]
            if player.discord_id is not None
        }

    def map_image_path(map_choice):
        for extension in MAP_IMAGE_EXTENSIONS:
            path = MAP_IMAGE_DIRECTORY / f"{map_choice['key']}{extension}"

            if path.exists():
                return path

        return None

    def image_for_map_choice(map_choice):
        path = map_image_path(map_choice)

        if path is None:
            return None

        with Image.open(path) as image:
            return image.convert("RGB")

    def draw_centered_text(draw, box, text, font, fill):
        left, top, right, bottom = box
        text_box = draw.textbbox((0, 0), text, font=font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        x = left + ((right - left - text_width) / 2)
        y = top + ((bottom - top - text_height) / 2)
        draw.text((x, y), text, font=font, fill=fill)

    def map_vote_preview_file(visible_maps):
        cards = [
            *[
                image_for_map_choice(map_choice)
                for map_choice in visible_maps
            ],
            image_for_map_choice({"key": "random"}),
        ]
        card_width = 230
        card_height = 130
        gap = 12
        padding = 16
        preview_width = (
            (card_width * len(cards))
            + (gap * (len(cards) - 1))
            + (padding * 2)
        )
        preview_height = card_height + (padding * 2)
        preview = Image.new("RGB", (preview_width, preview_height), "#2f3136")
        draw = ImageDraw.Draw(preview)
        font = ImageFont.load_default()

        for index, image in enumerate(cards):
            x = padding + (index * (card_width + gap))
            y = padding
            draw.rounded_rectangle(
                (x, y, x + card_width, y + card_height),
                radius=10,
                fill="#202225",
                outline="#f1c40f",
                width=3
            )

            image_box = (
                x + 8,
                y + 8,
                x + card_width - 8,
                y + card_height - 8
            )
            image_width = image_box[2] - image_box[0]
            image_height = image_box[3] - image_box[1]

            if image is None:
                draw.rectangle(image_box, fill="#3a3d42")
                draw_centered_text(draw, image_box, "?", font, "#ffffff")
                continue

            image = ImageOps.fit(
                image,
                (image_width, image_height),
                method=Image.Resampling.LANCZOS
            )
            preview.paste(image, (image_box[0], image_box[1]))

        buffer = BytesIO()
        preview.save(buffer, format="PNG")
        buffer.seek(0)
        return discord.File(buffer, filename="map_vote_preview.png")

    def leaderboard_font(size):
        font_paths = [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]

        for font_path in font_paths:
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue

        return ImageFont.load_default()

    def fit_text(draw, text, font, max_width):
        if draw.textlength(text, font=font) <= max_width:
            return text

        while text and draw.textlength(f"{text}...", font=font) > max_width:
            text = text[:-1]

        return f"{text}..." if text else "..."

    def leaderboard_display_name(entry):
        return entry.get("ign") or entry.get("username") or "Unknown"

    def leaderboard_value_text(entry, sort_by):
        if sort_by == "winrate":
            return f"({float(entry['winrate']):.1f}%)"

        if sort_by == "wins":
            return f"({entry['wins']}W)"

        if sort_by == "games":
            return f"({entry['total_games']}G)"

        if sort_by == "streak":
            return f"({entry['streak']:+d})"

        if sort_by == "peak_streak":
            return f"({entry['peak_streak']})"

        return f"({int(float(entry['mmr']))})"

    def leaderboard_avatar_image(entry):
        avatar_url = entry.get("avatar_url")

        if not avatar_url:
            return None

        try:
            response = requests.get(avatar_url, timeout=5)
            response.raise_for_status()

            with Image.open(BytesIO(response.content)) as avatar:
                return avatar.convert("RGB")
        except (requests.RequestException, OSError):
            return None

    def leaderboard_image_file(entries, page, total_pages, sort_by):
        row_height = 43
        row_gap = 4
        row_width = 548
        current_entries = entries[
            page * LEADERBOARD_PAGE_SIZE:
            (page + 1) * LEADERBOARD_PAGE_SIZE
        ]
        visible_rows = max(1, len(current_entries))
        width = row_width
        height = (visible_rows * row_height) + (
            (visible_rows - 1) * row_gap
        )
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        row_font = leaderboard_font(28)
        small_font = leaderboard_font(17)

        table_x = 0
        table_y = 0

        rank_colors = {
            1: ("#f4d000", "#352f05"),
            2: ("#d9dcdf", "#2e3032"),
            3: ("#a3470a", "#301704"),
        }

        for index, entry in enumerate(current_entries):
            y = table_y + index * (row_height + row_gap)
            position = entry["position"]
            accent, fill = rank_colors.get(position, ("#5865f2", "#292b2f"))

            draw.rounded_rectangle(
                (table_x, y, table_x + row_width, y + row_height),
                radius=4,
                fill=fill,
                outline="#111214",
                width=2
            )
            draw.rounded_rectangle(
                (table_x, y, table_x + 12, y + row_height),
                radius=4,
                fill=accent
            )

            rank_text = f"{position}."
            name = fit_text(
                draw,
                leaderboard_display_name(entry),
                row_font,
                260
            )
            value_text = leaderboard_value_text(entry, sort_by)
            record_text = f"({entry['wins']}-{entry['losses']})"
            avatar = leaderboard_avatar_image(entry)

            draw.text((table_x + 48, y + 4), rank_text, fill="#f2f3f5", font=row_font)
            avatar_box = (table_x + 94, y + 5, table_x + 128, y + 39)

            if avatar is None:
                draw.rectangle(avatar_box, fill="#111214")
            else:
                avatar = ImageOps.fit(
                    avatar,
                    (34, 34),
                    method=Image.Resampling.LANCZOS
                )
                image.paste(avatar, (avatar_box[0], avatar_box[1]))

            draw.text((table_x + 132, y + 4), name, fill="#f2f3f5", font=row_font)
            draw.text((table_x + 355, y + 4), value_text, fill="#f2f3f5", font=row_font)
            draw.text((table_x + 445, y + 4), record_text, fill="#f2f3f5", font=row_font)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return discord.File(buffer, filename="leaderboard.png")

    def stats_percent_label(value, prefix="TOP"):
        if value is None:
            return ""

        return f"{prefix} {float(value):.1f}%"

    def compact_number(value):
        value = float(value)

        if abs(value) >= 1000:
            return f"{value / 1000:.1f}K"

        return f"{value:.0f}"

    def parse_backend_datetime(value):
        if not value:
            return None

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def relative_time_text(value):
        played_at = parse_backend_datetime(value)

        if played_at is None:
            return ""

        now = datetime.now(timezone.utc)

        if played_at.tzinfo is None:
            played_at = played_at.replace(tzinfo=timezone.utc)

        seconds = max(0, int((now - played_at).total_seconds()))

        if seconds < 60:
            return "just now"

        minutes = seconds // 60

        if minutes < 60:
            return f"{minutes}m ago"

        hours = minutes // 60

        if hours < 24:
            return f"{hours}h ago"

        days = hours // 24
        return f"{days} days ago"

    def draw_stats_tile(
        draw,
        box,
        title,
        value,
        subtitle,
        title_font,
        value_font,
        subtitle_font,
    ):
        x1, y1, x2, y2 = box
        draw.rectangle(box, fill="#292b2f")
        draw.rectangle((x1, y1, x1 + 7, y2), fill="#5865f2")
        draw.text((x1 + 24, y1 + 15), title, fill="#f2f3f5", font=title_font)
        draw.text((x1 + 24, y1 + 48), value, fill="#ffffff", font=value_font)

        if subtitle:
            draw.text(
                (x1 + 24, y2 - 28),
                subtitle,
                fill="#d4d7dc",
                font=subtitle_font
            )

    def draw_rating_chart(draw, box, history, label_font):
        x1, y1, x2, y2 = box
        draw.rectangle(box, fill="#292b2f")
        draw.rectangle((x1, y1, x1 + 7, y2), fill="#5865f2")

        chart_x1 = x1 + 130
        chart_y1 = y1 + 28
        chart_x2 = x2 - 24
        chart_y2 = y2 - 48

        draw.rectangle(
            (chart_x1, chart_y1, chart_x2, chart_y2),
            outline="#d7d9dd",
            width=2
        )

        mmr_values = [
            float(point["mmr"])
            for point in history
            if point.get("mmr") is not None
        ]

        if not mmr_values:
            draw.text(
                (chart_x1 + 24, chart_y1 + 28),
                "No completed games yet",
                fill="#f2f3f5",
                font=label_font
            )
            return

        min_mmr = min(mmr_values)
        max_mmr = max(mmr_values)
        padding = max(50, (max_mmr - min_mmr) * 0.15)
        min_mmr -= padding
        max_mmr += padding

        if max_mmr == min_mmr:
            max_mmr += 50
            min_mmr -= 50

        for index in range(1, 6):
            x = chart_x1 + ((chart_x2 - chart_x1) * index / 6)
            draw.line((x, chart_y1, x, chart_y2), fill="#44474d", width=2)

        for index in range(1, 5):
            y = chart_y1 + ((chart_y2 - chart_y1) * index / 5)
            draw.line((chart_x1, y, chart_x2, y), fill="#44474d", width=2)

        for index in range(5):
            value = max_mmr - ((max_mmr - min_mmr) * index / 4)
            y = chart_y1 + ((chart_y2 - chart_y1) * index / 4)
            draw.text(
                (x1 + 72, y - 9),
                f"{value:.0f}",
                fill="#d4d7dc",
                font=label_font
            )

        total_points = len(mmr_values)
        tick_count = min(8, total_points)

        if tick_count == 1:
            game_ticks = [1]
        else:
            game_ticks = []

            for index in range(tick_count):
                game_number = 1 + round(
                    index * (total_points - 1) / (tick_count - 1)
                )

                if game_number not in game_ticks:
                    game_ticks.append(game_number)

        for game_number in game_ticks:
            if total_points == 1:
                x = (chart_x1 + chart_x2) / 2
            else:
                x = chart_x1 + (
                    (chart_x2 - chart_x1)
                    * (game_number - 1)
                    / (total_points - 1)
                )

            label = str(game_number)
            label_width = draw.textlength(label, font=label_font)
            draw.text(
                (x - (label_width / 2), chart_y2 + 9),
                label,
                fill="#d4d7dc",
                font=label_font
            )

        points = []

        for index, mmr in enumerate(mmr_values):
            if total_points == 1:
                x = (chart_x1 + chart_x2) / 2
            else:
                x = chart_x1 + ((chart_x2 - chart_x1) * index / (total_points - 1))

            y = chart_y2 - (
                (mmr - min_mmr) / (max_mmr - min_mmr)
            ) * (chart_y2 - chart_y1)
            points.append((x, y))

        if len(points) > 1:
            draw.line(points, fill="#ff2424", width=4)

        for x, y in points:
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#ff2424")

        draw.text(
            (x1 + 38, y1 + 92),
            "R\nA\nT\nI\nN\nG",
            fill="#f2f3f5",
            font=leaderboard_font(24),
            spacing=-3,
            align="center"
        )
        draw.text(
            ((chart_x1 + chart_x2) / 2 - 55, y2 - 38),
            "GAMES",
            fill="#f2f3f5",
            font=leaderboard_font(32)
        )

    def stats_image_file(stats):
        player = stats["player"]
        recent_games = stats["recent_games"]
        history = stats["mmr_history"]

        width = 1018
        height = 760
        image = Image.new("RGB", (width, height), "#1f2024")
        draw = ImageDraw.Draw(image)

        header_font = leaderboard_font(28)
        name_font = leaderboard_font(82)
        mmr_font = leaderboard_font(66)
        tile_title_font = leaderboard_font(27)
        tile_value_font = leaderboard_font(57)
        tile_subtitle_font = leaderboard_font(18)
        recent_font = leaderboard_font(23)

        accent = "#5865f2"
        panel = "#292b2f"
        divider = "#151619"
        white = "#ffffff"
        muted = "#d4d7dc"

        draw.rectangle((0, 0, width, height), fill=panel)
        draw.rectangle((0, 0, 8, height), fill=accent)

        draw.text((32, 20), "PLAYER", fill=white, font=header_font)
        draw.text((724, 20), "MMR", fill=white, font=header_font)

        avatar = leaderboard_avatar_image(player)
        avatar_box = (34, 60, 103, 129)

        if avatar is None:
            draw.rectangle(avatar_box, fill="#111214")
        else:
            avatar = ImageOps.fit(
                avatar,
                (69, 69),
                method=Image.Resampling.LANCZOS
            )
            image.paste(avatar, (avatar_box[0], avatar_box[1]))

        player_name = player.get("ign") or player.get("username") or "Unknown"
        name_font_size = 82

        while name_font_size > 42:
            name_font = leaderboard_font(name_font_size)

            if draw.textlength(player_name, font=name_font) <= 560:
                break

            name_font_size -= 2

        player_name = fit_text(draw, player_name, name_font, 560)
        draw.text((130, 38), player_name, fill=white, font=name_font)
        draw.text((724, 43), f"{float(player['mmr']):.1f}", fill=white, font=mmr_font)

        draw.rectangle((0, 139, width, 148), fill=divider)

        rank_position = stats.get("rank_position") or 0
        winrate = float(player["winrate"])
        mmr = float(player["mmr"])
        wins = int(player["wins"])
        losses = int(player["losses"])
        games = int(player["total_games"])

        tile_w = 226
        tile_h = 134
        left_x = 0
        mid_x = 234
        right_x = 462
        y1 = 148
        y2 = 291

        draw_stats_tile(
            draw,
            (left_x, y1, left_x + tile_w, y1 + tile_h),
            "RANK",
            f"#{rank_position}",
            stats_percent_label(stats.get("rank_top_percent")),
            tile_title_font,
            tile_value_font,
            tile_subtitle_font,
        )
        draw_stats_tile(
            draw,
            (mid_x, y1, mid_x + tile_w, y1 + tile_h),
            "WINRATE",
            f"{winrate:.0f}%",
            stats_percent_label(stats.get("winrate_top_percent")),
            tile_title_font,
            tile_value_font,
            tile_subtitle_font,
        )
        draw_stats_tile(
            draw,
            (right_x, y1, right_x + tile_w, y1 + tile_h),
            "POINTS",
            compact_number(mmr),
            stats_percent_label(stats.get("rank_top_percent")),
            tile_title_font,
            tile_value_font,
            tile_subtitle_font,
        )

        draw_stats_tile(
            draw,
            (left_x, y2, left_x + tile_w, y2 + tile_h),
            "WINS",
            str(wins),
            stats_percent_label(stats.get("wins_top_percent")),
            tile_title_font,
            tile_value_font,
            tile_subtitle_font,
        )
        draw_stats_tile(
            draw,
            (mid_x, y2, mid_x + tile_w, y2 + tile_h),
            "LOSSES",
            str(losses),
            "",
            tile_title_font,
            tile_value_font,
            tile_subtitle_font,
        )
        draw_stats_tile(
            draw,
            (right_x, y2, right_x + tile_w, y2 + tile_h),
            "GAMES",
            str(games),
            stats_percent_label(stats.get("games_top_percent")),
            tile_title_font,
            tile_value_font,
            tile_subtitle_font,
        )

        recent_box = (690, 148, width, 425)
        draw.rectangle(recent_box, fill=panel)
        draw.rectangle((recent_box[0], recent_box[1], recent_box[0] + 7, recent_box[3]), fill=accent)
        draw.text((716, 165), "PREVIOUS GAMES", fill=white, font=tile_title_font)

        if not recent_games:
            draw.text((716, 211), "No completed games yet", fill=muted, font=recent_font)
        else:
            for index, game in enumerate(recent_games, start=1):
                y = 207 + ((index - 1) * 24)
                result = "WIN" if game["won"] else "LOSE"
                color = "#20ff34" if game["won"] else "#ff2a2a"
                change = float(game["mmr_change"])
                time_text = relative_time_text(game["played_at"])

                draw.text((716, y), f"{index}.", fill=white, font=recent_font)
                draw.text((752, y), result, fill=color, font=recent_font)
                draw.text((817, y), f"{change:+.1f}", fill=color, font=recent_font)
                draw.text((897, y), time_text, fill=muted, font=recent_font)

        draw.rectangle((0, 425, width, 433), fill=divider)
        draw_rating_chart(draw, (0, 433, width, height), history, leaderboard_font(17))

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return discord.File(buffer, filename="stats.png")

    def match_map_file_and_match(match):
        path = map_image_path(match["map"])

        if path is None:
            return None, match

        match_for_embed = match.copy()
        map_for_embed = match["map"].copy()
        filename = f"match_map{path.suffix.lower()}"
        map_for_embed["image_url"] = f"attachment://{filename}"
        match_for_embed["map"] = map_for_embed

        return discord.File(path, filename=filename), match_for_embed

    async def send_match_panel(channel, match):
        file, match_for_embed = match_map_file_and_match(match)
        view = MatchResultView(match)
        active_matches_by_thread_id[channel.id] = match

        if file is None:
            message = await channel.send(
                embed=build_match_embed(match_for_embed),
                view=view
            )
            view.message = message
            bot.loop.create_task(view.unlock_after_delay())
            return

        message = await channel.send(
            embed=build_match_embed(match_for_embed),
            view=view,
            file=file
        )
        view.message = message
        bot.loop.create_task(view.unlock_after_delay())

    async def send_admin_match_panel(match, match_thread):
        match_id = match.get("backend_match_id")

        if match_id is None:
            return

        try:
            backend_match = fetch_backend_match(match_id)
            match["backend_match"] = backend_match
            channel = await get_channel(
                admin_match_panel_channel_id(match_thread.guild.id)
            )
            view = AdminMatchControlView(match, match_thread)
            message = await channel.send(
                embed=build_admin_match_embed(match, backend_match),
                view=view
            )
            view.message = message
            active_admin_match_views_by_match_id[match_id] = view
        except (requests.RequestException, discord.HTTPException) as error:
            await send_bot_report(
                f"Could not create admin panel for queue {match_id}: {error}",
                match_thread
            )

    async def delete_thread_after_delay(thread, delay_seconds, reason):
        await asyncio.sleep(delay_seconds)

        try:
            await thread.delete(reason=reason)
        except discord.HTTPException:
            pass

    async def create_match_thread(channel, match):
        thread_name = f"queue{display_match_number(match)}"
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            reason="Round Table match found"
        )

        mentions = match_player_mentions(match)

        if mentions:
            await thread.send(f"Match ready: {mentions}")

        await send_match_panel(thread, match)
        await send_admin_match_panel(match, thread)

        return thread

    async def remove_in_game_role_entries(channel, player_entries):
        failures = []
        seen_discord_ids = set()
        tasks = []

        async def remove_one(discord_id, player_name):
            try:
                discord_id = int(discord_id)
                member = await find_member(
                    bot,
                    discord_id,
                    channel.guild.id
                )

                if member is None:
                    failures.append(player_name)
                    return

                await remove_in_game_role(member)
            except (
                discord.Forbidden,
                discord.HTTPException,
                TypeError,
                ValueError
            ):
                failures.append(player_name)

        for discord_id, player_name in player_entries:
            if discord_id is None:
                continue

            discord_id_key = str(discord_id)

            if discord_id_key in seen_discord_ids:
                continue

            seen_discord_ids.add(discord_id_key)
            tasks.append(remove_one(discord_id, player_name))

        if tasks:
            await asyncio.gather(*tasks)

        if failures:
            failed_names = ", ".join(failures[:10])

            if len(failures) > 10:
                failed_names += f", and {len(failures) - 10} more"

            await send_bot_report(
                f"Could not remove the in-game role from: {failed_names}.",
                channel
            )

    async def remove_in_game_role_from_match(channel, match):
        await remove_in_game_role_entries(
            channel,
            [
                (player.discord_id, player.name)
                for player in match["team_1"] + match["team_2"]
            ]
        )

    async def remove_in_game_role_from_players(channel, players):
        await remove_in_game_role_entries(
            channel,
            [
                (player.get("discord_id"), player.get("username", "Unknown"))
                for player in players
            ]
        )

    async def role_sync_error_message(error):
        if isinstance(error, ValueError):
            return str(error)

        if isinstance(error, discord.Forbidden):
            return (
                "Discord blocked the role update. Check that my highest role "
                "is above the queue roles and that I have Manage Roles."
            )

        return "Discord returned an error while updating the role."

    async def sync_completed_match_elo_nicknames(match, guild_id=None):
        backend_match = match.get("backend_match")

        if backend_match is None:
            return

        for match_player in backend_match["match_players"]:
            player = None

            try:
                player = find_backend_player_by_username(
                    match_player["player_username"]
                )
            except requests.RequestException:
                await send_bot_report(
                    "Could not load backend player after match for "
                    f"{match_player['player_username']}."
                )
                continue

            if player is None or player.get("discord_id") is None:
                continue

            try:
                member = await find_member(bot, player["discord_id"], guild_id)
            except (discord.HTTPException, ValueError):
                await send_bot_report(
                    "Could not find Discord member after match for "
                    f"{match_player['player_username']}."
                )
                continue

            if member is None:
                continue

            try:
                await sync_member_elo_nickname(member, player)
            except (discord.Forbidden, discord.HTTPException, ValueError):
                await send_bot_report(
                    "Could not update Elo nickname for "
                    f"{player['username']}."
                )

            try:
                await sync_member_rank_role(member, player)
            except (discord.Forbidden, discord.HTTPException, ValueError) as error:
                await send_bot_report(
                    f"Could not update rank role for {player['username']}: {error}"
                )

            try:
                await sync_member_ultra_boss_instinct_role(member, player)
            except (discord.Forbidden, discord.HTTPException, ValueError) as error:
                await send_bot_report(
                    "Could not update Ultra Boss Instinct role for "
                    f"{player['username']}: {error}"
                )

            try:
                await sync_member_losing_streak_role(member, player)
            except (discord.Forbidden, discord.HTTPException, ValueError) as error:
                await send_bot_report(
                    "Could not update Losing Streak role for "
                    f"{player['username']}: {error}"
                )

    async def sync_player_discord_profile(player, guild_id=None):
        if player is None or player.get("discord_id") is None:
            return

        try:
            member = await find_member(bot, player["discord_id"], guild_id)
        except (discord.HTTPException, ValueError):
            await send_bot_report(
                f"Could not find Discord member for {player['username']}."
            )
            return

        if member is None:
            return

        try:
            await sync_member_elo_nickname(member, player)
        except (discord.Forbidden, discord.HTTPException, ValueError):
            await send_bot_report(
                f"Could not update Elo nickname for {player['username']}."
            )

        try:
            await sync_member_rank_role(member, player)
        except (discord.Forbidden, discord.HTTPException, ValueError) as error:
            await send_bot_report(
                f"Could not update rank role for {player['username']}: {error}"
            )

        try:
            await sync_member_ultra_boss_instinct_role(member, player)
        except (discord.Forbidden, discord.HTTPException, ValueError) as error:
            await send_bot_report(
                "Could not update Ultra Boss Instinct role for "
                f"{player['username']}: {error}"
            )

        try:
            await sync_member_losing_streak_role(member, player)
        except (discord.Forbidden, discord.HTTPException, ValueError) as error:
            await send_bot_report(
                "Could not update Losing Streak role for "
                f"{player['username']}: {error}"
            )

    async def sync_affected_backend_players(backend_match, guild_id=None):
        for player in backend_match.get("affected_players", []):
            await sync_player_discord_profile(player, guild_id)

    def winner_label(winner_key):
        return "Team 1" if winner_key == "team_1" else "Team 2"

    class AdminMatchControlView(discord.ui.View):
        def __init__(self, match, match_thread):
            super().__init__(timeout=None)
            self.match = match
            self.match_thread = match_thread
            self.message = None

        @property
        def match_id(self):
            return self.match.get("backend_match_id")

        async def interaction_check(self, interaction):
            if interaction.user.guild_permissions.administrator:
                return True

            await interaction.response.send_message(
                "Only administrators can use this panel.",
                ephemeral=True
            )
            return False

        async def refresh_panel(self, backend_match=None):
            if self.message is None:
                return

            if backend_match is None and self.match_id is not None:
                try:
                    backend_match = fetch_backend_match(self.match_id)
                except requests.RequestException:
                    backend_match = self.match.get("backend_match")

            if backend_match is not None:
                self.match["backend_match"] = backend_match

            try:
                await self.message.edit(
                    embed=build_admin_match_embed(self.match, backend_match),
                    view=self
                )
            except (discord.NotFound, discord.HTTPException):
                pass

        async def finish_admin_action(self, interaction, backend_match, message):
            self.match["backend_match"] = backend_match
            await sync_affected_backend_players(
                backend_match,
                self.match_thread.guild.id
            )
            await self.refresh_panel(backend_match)
            await interaction.followup.send(message, ephemeral=True)

        async def close_match_thread(self, reason):
            if isinstance(self.match_thread, discord.Thread):
                active_matches_by_thread_id.pop(self.match_thread.id, None)
                active_cancel_votes_by_thread_id.pop(self.match_thread.id, None)

                try:
                    await self.match_thread.delete(reason=reason)
                except (discord.NotFound, discord.HTTPException):
                    pass

        @discord.ui.button(
            label="Revert/Cancel",
            style=discord.ButtonStyle.danger
        )
        async def cancel_match(self, interaction, button):
            if self.match_id is None:
                await interaction.response.send_message(
                    "This match does not have a backend ID.",
                    ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)

            try:
                current_backend_match = fetch_backend_match(self.match_id)
            except requests.RequestException:
                await interaction.followup.send(
                    f"Could not load queue `{self.match_id}`.",
                    ephemeral=True
                )
                return

            if current_backend_match.get("status") == "cancelled":
                self.match["backend_match"] = current_backend_match
                await self.refresh_panel(current_backend_match)
                await interaction.followup.send(
                    f"Queue `{self.match_id}` is already cancelled.",
                    ephemeral=True
                )
                return

            try:
                backend_match = revoke_backend_match(self.match_id)
            except requests.RequestException:
                await interaction.followup.send(
                    f"Could not cancel queue `{self.match_id}`.",
                    ephemeral=True
                )
                return

            await self.finish_admin_action(
                interaction,
                backend_match,
                f"Queue `{self.match_id}` was cancelled/reverted."
            )

            if self.match_thread is not None:
                await remove_in_game_role_from_match(
                    self.match_thread,
                    self.match
                )
                await self.close_match_thread("Round Table admin cancelled match")

        async def force_winner(self, interaction, winner_key):
            if self.match_id is None:
                await interaction.response.send_message(
                    "This match does not have a backend ID.",
                    ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)

            try:
                current_backend_match = fetch_backend_match(self.match_id)
            except requests.RequestException:
                await interaction.followup.send(
                    f"Could not load queue `{self.match_id}`.",
                    ephemeral=True
                )
                return

            if (
                current_backend_match.get("status") == "completed"
                and current_backend_match.get("winner") == winner_key
            ):
                self.match["backend_match"] = current_backend_match
                await self.refresh_panel(current_backend_match)
                await interaction.followup.send(
                    f"Queue `{self.match_id}` already has `{winner_label(winner_key)}` as winner.",
                    ephemeral=True
                )
                return

            if self.match_thread is not None:
                await remove_in_game_role_from_match(
                    self.match_thread,
                    self.match
                )

            try:
                backend_match = set_backend_match_winner(self.match_id, winner_key)
            except requests.RequestException:
                await interaction.followup.send(
                    f"Could not set the winner for queue `{self.match_id}`.",
                    ephemeral=True
                )
                return

            winner = winner_label(winner_key)
            self.match["backend_match"] = backend_match

            await send_match_result(
                embed=build_match_result_embed(self.match, winner),
                context=interaction
            )
            await self.finish_admin_action(
                interaction,
                backend_match,
                f"Queue `{self.match_id}` was set to `{winner}`."
            )

            if self.match_thread is not None:
                await self.close_match_thread("Round Table admin finished match")

        @discord.ui.button(
            label="Team 1",
            style=discord.ButtonStyle.primary
        )
        async def team_1_wins(self, interaction, button):
            await self.force_winner(interaction, "team_1")

        @discord.ui.button(
            label="Team 2",
            style=discord.ButtonStyle.primary
        )
        async def team_2_wins(self, interaction, button):
            await self.force_winner(interaction, "team_2")

    class MatchResultView(discord.ui.View):
        def __init__(self, match):
            super().__init__(timeout=None)
            self.match = match
            self.completed = False
            self.message = None

            for item in self.children:
                item.disabled = True

        def match_discord_ids(self):
            return {
                str(player.discord_id)
                for player in self.match["team_1"] + self.match["team_2"]
                if player.discord_id is not None
            }

        def can_submit_result(self, member):
            if member.guild_permissions.administrator:
                return True

            return str(member.id) in self.match_discord_ids()

        async def unlock_after_delay(self):
            await asyncio.sleep(MATCH_RESULT_BUTTON_DELAY_SECONDS)

            if self.completed:
                return

            for item in self.children:
                item.disabled = False

            if self.message is None:
                return

            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

        async def finish_match(self, interaction, winner):
            if not self.can_submit_result(interaction.user):
                await interaction.response.send_message(
                    "Only players in this match or administrators can submit the result.",
                    ephemeral=True
                )
                return

            if any(item.disabled for item in self.children):
                await interaction.response.send_message(
                    "Match results can be submitted in a few seconds.",
                    ephemeral=True
                )
                return

            if self.completed:
                await interaction.response.send_message(
                    "This match result was already submitted.",
                    ephemeral=True
                )
                return

            self.completed = True

            try:
                await interaction.response.defer()
            except (discord.NotFound, discord.HTTPException):
                pass

            await remove_in_game_role_from_match(
                interaction.channel,
                self.match
            )

            for item in self.children:
                item.disabled = True

            try:
                await interaction.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException, AttributeError):
                pass

            try:
                complete_backend_match(self.match, winner)
                await sync_completed_match_elo_nicknames(
                    self.match,
                    interaction.guild.id
                )
                admin_view = active_admin_match_views_by_match_id.get(
                    self.match.get("backend_match_id")
                )

                if admin_view is not None:
                    await admin_view.refresh_panel(self.match.get("backend_match"))
            except requests.RequestException:
                await send_bot_report(
                    "Match result was clicked in Discord, but the backend "
                    f"update failed for queue {display_match_number(self.match)}."
                )

            await send_match_result(
                embed=build_match_result_embed(self.match, winner),
                context=interaction.channel
            )

            if isinstance(interaction.channel, discord.Thread):
                active_matches_by_thread_id.pop(interaction.channel.id, None)
                active_cancel_votes_by_thread_id.pop(interaction.channel.id, None)
                await interaction.channel.delete(
                    reason="Round Table match completed"
                )

        @discord.ui.button(
            label="Team 1 Won",
            style=discord.ButtonStyle.primary
        )
        async def team_1_won(self, interaction, button):
            await self.finish_match(interaction, "Team 1")

        @discord.ui.button(
            label="Team 2 Won",
            style=discord.ButtonStyle.primary
        )
        async def team_2_won(self, interaction, button):
            await self.finish_match(interaction, "Team 2")

    class LeaderboardView(discord.ui.View):
        def __init__(self, entries, sort_by="mmr"):
            super().__init__(timeout=180)
            self.entries = entries
            self.sort_by = sort_by
            self.page = 0
            self.total_pages = max(
                1,
                (len(entries) + LEADERBOARD_PAGE_SIZE - 1)
                // LEADERBOARD_PAGE_SIZE
            )
            self.metric_select = LeaderboardMetricSelect(self)
            self.page_select = LeaderboardPageSelect(self)
            self.add_item(self.metric_select)
            self.add_item(self.page_select)
            self.update_button_states()

        def update_button_states(self):
            self.first_page.disabled = self.page == 0
            self.previous_page.disabled = self.page == 0
            self.refresh_page.disabled = False
            self.next_page.disabled = self.page >= self.total_pages - 1
            self.last_page.disabled = self.page >= self.total_pages - 1
            self.metric_select.refresh_options()
            self.page_select.refresh_options()

        def current_file(self):
            return leaderboard_image_file(
                self.entries,
                self.page,
                self.total_pages,
                self.sort_by
            )

        def current_embed(self):
            embed = discord.Embed(color=discord.Color.red())
            embed.set_image(url="attachment://leaderboard.png")
            return embed

        async def update_message(self, interaction):
            self.update_button_states()

            if not interaction.response.is_done():
                await interaction.response.defer()

            await interaction.edit_original_response(
                embed=self.current_embed(),
                attachments=[self.current_file()],
                view=self
            )

        async def reload_entries(self):
            leaderboard = fetch_leaderboard(self.sort_by)
            self.entries = leaderboard.get("entries", [])
            self.total_pages = max(
                1,
                (len(self.entries) + LEADERBOARD_PAGE_SIZE - 1)
                // LEADERBOARD_PAGE_SIZE
            )
            self.page = min(self.page, self.total_pages - 1)

        @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary)
        async def first_page(self, interaction, button):
            self.page = 0
            await self.update_message(interaction)

        @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
        async def previous_page(self, interaction, button):
            self.page = max(0, self.page - 1)
            await self.update_message(interaction)

        @discord.ui.button(label="↻", style=discord.ButtonStyle.secondary)
        async def refresh_page(self, interaction, button):
            if not interaction.response.is_done():
                await interaction.response.defer()

            try:
                await self.reload_entries()
            except requests.RequestException:
                await interaction.followup.send(
                    "Could not refresh the leaderboard.",
                    ephemeral=True
                )
                return

            await self.update_message(interaction)

        @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
        async def next_page(self, interaction, button):
            self.page = min(self.total_pages - 1, self.page + 1)
            await self.update_message(interaction)

        @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary)
        async def last_page(self, interaction, button):
            self.page = self.total_pages - 1
            await self.update_message(interaction)

    class LeaderboardMetricSelect(discord.ui.Select):
        def __init__(self, leaderboard_view):
            self.leaderboard_view = leaderboard_view
            super().__init__(
                placeholder="MMR",
                min_values=1,
                max_values=1,
                row=1
            )
            self.refresh_options()

        def refresh_options(self):
            self.options = [
                discord.SelectOption(
                    label=label,
                    value=value,
                    default=value == self.leaderboard_view.sort_by
                )
                for value, label in LEADERBOARD_METRICS.items()
            ]
            self.placeholder = LEADERBOARD_METRICS[
                self.leaderboard_view.sort_by
            ]

        async def callback(self, interaction):
            self.leaderboard_view.sort_by = self.values[0]
            self.leaderboard_view.page = 0

            if not interaction.response.is_done():
                await interaction.response.defer()

            try:
                await self.leaderboard_view.reload_entries()
            except requests.RequestException:
                await interaction.followup.send(
                    "Could not load that leaderboard.",
                    ephemeral=True
                )
                return

            await self.leaderboard_view.update_message(interaction)

    class LeaderboardPageSelect(discord.ui.Select):
        def __init__(self, leaderboard_view):
            self.leaderboard_view = leaderboard_view
            super().__init__(
                placeholder="Page 1",
                min_values=1,
                max_values=1,
                row=2
            )
            self.refresh_options()

        def refresh_options(self):
            self.options = [
                discord.SelectOption(
                    label=f"Page {page_number}",
                    value=str(page_number - 1),
                    default=(page_number - 1) == self.leaderboard_view.page
                )
                for page_number in range(
                    1,
                    min(self.leaderboard_view.total_pages, 25) + 1
                )
            ]
            self.placeholder = f"Page {self.leaderboard_view.page + 1}"

        async def callback(self, interaction):
            self.leaderboard_view.page = int(self.values[0])
            await self.leaderboard_view.update_message(interaction)

    class CancelMatchVoteView(discord.ui.View):
        def __init__(self, match, thread):
            super().__init__(timeout=None)
            self.match = match
            self.thread = thread
            self.yes_votes = set()
            self.no_votes = set()
            self.completed = False
            self.message = None

        def player_discord_ids(self):
            return match_player_discord_ids(self.match)

        def vote_count_text(self):
            return (
                f"Yes: {len(self.yes_votes)}/{CANCEL_VOTE_YES_THRESHOLD}\n"
                f"No: {len(self.no_votes)}"
            )

        def build_embed(self):
            embed = discord.Embed(
                title=f"Cancel Vote For Queue #{display_match_number(self.match)}",
                description=(
                    f"At least {CANCEL_VOTE_YES_THRESHOLD} match players "
                    "must vote yes to cancel this match."
                ),
                color=discord.Color.red()
            )
            embed.add_field(
                name="Votes",
                value=self.vote_count_text(),
                inline=False
            )
            return embed

        async def complete_cancel(self, channel):
            self.completed = True

            for item in self.children:
                item.disabled = True

            active_matches_by_thread_id.pop(self.thread.id, None)
            active_cancel_votes_by_thread_id.pop(self.thread.id, None)

            if self.message is not None:
                try:
                    await self.message.edit(embed=self.build_embed(), view=self)
                except discord.HTTPException:
                    pass

            try:
                cancel_backend_match(self.match)
            except requests.RequestException:
                await channel.send(
                    "Cancel vote passed, but I could not cancel the match in the backend."
                )
                return False

            await channel.send(
                "Cancel vote passed. This match has been cancelled."
            )

            bot.loop.create_task(
                remove_in_game_role_from_match(self.thread, self.match)
            )
            bot.loop.create_task(
                delete_thread_after_delay(
                    self.thread,
                    3,
                    "Round Table match cancelled by vote"
                )
            )
            return True

        async def finish_vote_passed(self, interaction):
            await interaction.response.edit_message(
                embed=self.build_embed(),
                view=self
            )
            await self.complete_cancel(interaction.channel)

        async def force_yes_test_players(self):
            if self.completed:
                return 0

            yes_count_before = len(self.yes_votes)

            for player in self.match["team_1"] + self.match["team_2"]:
                if not player.name.lower().startswith("test"):
                    continue

                if player.discord_id is None:
                    continue

                self.no_votes.discard(int(player.discord_id))
                self.yes_votes.add(int(player.discord_id))

            if len(self.yes_votes) >= CANCEL_VOTE_YES_THRESHOLD:
                await self.complete_cancel(self.thread)
            elif self.message is not None:
                await self.message.edit(embed=self.build_embed(), view=self)

            return len(self.yes_votes) - yes_count_before

        async def record_vote(self, interaction, vote):
            if self.completed:
                await interaction.response.send_message(
                    "This cancel vote is already finished.",
                    ephemeral=True
                )
                return

            if interaction.user.id not in self.player_discord_ids():
                await interaction.response.send_message(
                    "Only players in this match can vote to cancel it.",
                    ephemeral=True
                )
                return

            self.yes_votes.discard(interaction.user.id)
            self.no_votes.discard(interaction.user.id)

            if vote == "yes":
                self.yes_votes.add(interaction.user.id)
            else:
                self.no_votes.add(interaction.user.id)

            if len(self.yes_votes) >= CANCEL_VOTE_YES_THRESHOLD:
                await self.finish_vote_passed(interaction)
                return

            if len(self.yes_votes) + len(self.no_votes) == len(
                self.player_discord_ids()
            ):
                self.completed = True
                active_cancel_votes_by_thread_id.pop(self.thread.id, None)

                for item in self.children:
                    item.disabled = True

            await interaction.response.edit_message(
                embed=self.build_embed(),
                view=self
            )

        @discord.ui.button(
            label="Yes, Cancel",
            style=discord.ButtonStyle.danger
        )
        async def vote_yes(self, interaction, button):
            await self.record_vote(interaction, "yes")

        @discord.ui.button(
            label="No",
            style=discord.ButtonStyle.secondary
        )
        async def vote_no(self, interaction, button):
            await self.record_vote(interaction, "no")

    def available_maps_for_keys(map_pool, keys, excluded_keys=None):
        excluded_keys = excluded_keys or set()

        return [
            map_choice
            for map_choice in map_pool
            if (
                map_choice["key"] in keys
                and map_choice["key"] not in excluded_keys
            )
        ]

    def available_maps_not_excluded(map_pool, excluded_keys=None):
        excluded_keys = excluded_keys or set()

        return [
            map_choice
            for map_choice in map_pool
            if map_choice["key"] not in excluded_keys
        ]

    def choose_map_from_keys(map_pool, keys, selected_keys, blocked_keys=None):
        unavailable_keys = selected_keys | (blocked_keys or set())
        choices = available_maps_for_keys(map_pool, keys, unavailable_keys)

        if not choices:
            choices = available_maps_not_excluded(map_pool, unavailable_keys)

        if not choices:
            return None

        return random.choice(choices)

    def choose_weighted_second_map(map_pool, selected_keys, blocked_keys=None):
        preferred_keys = MAP_VOTE_POOL_1_KEYS
        fallback_keys = MAP_VOTE_POOL_2_KEYS
        unavailable_keys = selected_keys | (blocked_keys or set())

        if random.random() >= MAP_VOTE_SECOND_OPTION_POOL_1_CHANCE:
            preferred_keys = MAP_VOTE_POOL_2_KEYS
            fallback_keys = MAP_VOTE_POOL_1_KEYS

        choices = available_maps_for_keys(
            map_pool,
            preferred_keys,
            unavailable_keys
        )

        if not choices:
            choices = available_maps_for_keys(
                map_pool,
                fallback_keys,
                unavailable_keys
            )

        if not choices:
            choices = available_maps_not_excluded(map_pool, unavailable_keys)

        if not choices:
            return None

        return random.choice(choices)

    def choose_visible_map_vote_options(map_pool, blocked_map_key=None):
        selected_maps = []
        selected_keys = set()
        blocked_keys = {blocked_map_key} if blocked_map_key else set()

        map_choice = choose_map_from_keys(
            map_pool,
            MAP_VOTE_POOL_1_KEYS,
            selected_keys,
            blocked_keys
        )

        if map_choice is not None:
            selected_maps.append(map_choice)
            selected_keys.add(map_choice["key"])

        map_choice = choose_weighted_second_map(
            map_pool,
            selected_keys,
            blocked_keys
        )

        if map_choice is not None:
            selected_maps.append(map_choice)
            selected_keys.add(map_choice["key"])

        map_choice = choose_map_from_keys(
            map_pool,
            MAP_VOTE_POOL_2_KEYS,
            selected_keys,
            blocked_keys
        )

        if map_choice is not None:
            selected_maps.append(map_choice)
            selected_keys.add(map_choice["key"])

        for map_choice in available_maps_not_excluded(
            map_pool,
            selected_keys | blocked_keys
        ):
            if len(selected_maps) >= MAP_VOTE_VISIBLE_MAP_COUNT:
                break

            if map_choice is None or map_choice["key"] in selected_keys:
                continue

            selected_maps.append(map_choice)
            selected_keys.add(map_choice["key"])

        return selected_maps

    class MapVoteView(discord.ui.View):
        def __init__(
            self,
            match,
            players,
            parent_channel,
            thread,
            map_pool,
            blocked_map_key=None
        ):
            super().__init__(timeout=MAP_VOTE_TIMEOUT_SECONDS)
            self.match = match
            self.players = players
            self.parent_channel = parent_channel
            self.thread = thread
            self.map_pool = map_pool
            self.blocked_map_key = blocked_map_key
            self.visible_maps = choose_visible_map_vote_options(
                self.map_pool,
                self.blocked_map_key
            )
            self.preview_image_url = "attachment://map_vote_preview.png"
            self.votes = {}
            self.completed = False
            self.message = None
            self.deadline_task = None
            self.deadline = (
                discord.utils.utcnow()
                + timedelta(seconds=MAP_VOTE_TIMEOUT_SECONDS)
            )

            for index, map_choice in enumerate(self.visible_maps):
                self.children[index].label = map_choice["name"]

            for index in range(len(self.visible_maps), MAP_VOTE_VISIBLE_MAP_COUNT):
                self.children[index].label = "Map unavailable"
                self.children[index].disabled = True

        def start_deadline_task(self):
            self.deadline_task = bot.loop.create_task(
                self.finish_at_deadline()
            )

        async def finish_at_deadline(self):
            await discord.utils.sleep_until(self.deadline)
            await self.finish_vote()

        def player_discord_ids(self):
            return {
                int(player["discord_id"])
                for player in self.players
                if player.get("discord_id") is not None
            }

        def vote_counts(self):
            counts = {
                map_choice["key"]: 0
                for map_choice in self.visible_maps
            }
            counts.update({
                "random": 0,
            })

            for vote in self.votes.values():
                counts[vote] += 1

            return counts

        async def update_vote_message(self):
            if self.message is None:
                return

            await self.message.edit(
                embed=build_map_vote_embed(
                    self.visible_maps,
                    self.vote_counts(),
                    self.deadline,
                    self.preview_image_url,
                    self.completed
                ),
                view=self
            )

        async def record_vote(self, interaction, map_key):
            if interaction.user.id not in self.player_discord_ids():
                await interaction.response.send_message(
                    "Only players in this match can vote for the map.",
                    ephemeral=True
                )
                return

            self.votes[interaction.user.id] = map_key

            await interaction.response.edit_message(
                embed=build_map_vote_embed(
                    self.visible_maps,
                    self.vote_counts(),
                    self.deadline,
                    self.preview_image_url
                ),
                view=self
            )

            if len(self.votes) == len(self.players):
                await self.finish_vote()

        def selected_map(self):
            counts = self.vote_counts()
            highest_vote_count = max(counts.values())
            winning_keys = [
                key
                for key, count in counts.items()
                if count == highest_vote_count
            ]
            winning_key = random.choice(winning_keys)

            if winning_key == "random":
                random_pool = available_maps_not_excluded(
                    self.map_pool,
                    {self.blocked_map_key} if self.blocked_map_key else set()
                )

                if not random_pool:
                    random_pool = self.map_pool

                return random.choice(random_pool)

            return next(
                map_choice
                for map_choice in self.visible_maps
                if map_choice["key"] == winning_key
            )

        async def finish_vote(self):
            if self.completed:
                return

            self.completed = True

            if (
                self.deadline_task is not None
                and self.deadline_task is not asyncio.current_task()
            ):
                self.deadline_task.cancel()

            for item in self.children:
                item.disabled = True

            self.match["map"] = self.selected_map()

            try:
                await self.update_vote_message()
            except discord.HTTPException:
                pass

            try:
                create_backend_match(self.match)
            except requests.RequestException:
                bot.loop.create_task(
                    remove_in_game_role_from_players(
                        self.parent_channel,
                        self.players
                    )
                )
                async with queue_state.queue_flow_lock:
                    requeue_players_at_front(self.players)
                    bot.loop.create_task(
                        add_in_queue_role_to_players(
                            self.parent_channel,
                            self.players
                        )
                    )
                    clear_last_queue_action()
                    await send_queue_panel(self.parent_channel)
                    await create_match_if_queue_ready(
                        self.parent_channel,
                        lock_already_held=True
                    )
                await self.thread.send(
                    "The map was selected, but the match could not be saved to the backend."
                )
                self.stop()
                return

            try:
                await self.thread.edit(
                    name=f"queue{display_match_number(self.match)}",
                    reason="Round Table match ready"
                )
            except discord.HTTPException:
                pass

            await send_match_panel(self.thread, self.match)
            await send_admin_match_panel(self.match, self.thread)
            self.stop()

        async def on_timeout(self):
            await self.finish_vote()

        @discord.ui.button(
            label="Map 1",
            style=discord.ButtonStyle.primary
        )
        async def map_one(self, interaction, button):
            await self.record_vote(interaction, self.visible_maps[0]["key"])

        @discord.ui.button(
            label="Map 2",
            style=discord.ButtonStyle.primary
        )
        async def map_two(self, interaction, button):
            await self.record_vote(interaction, self.visible_maps[1]["key"])

        @discord.ui.button(
            label="Map 3",
            style=discord.ButtonStyle.primary
        )
        async def map_three(self, interaction, button):
            if len(self.visible_maps) < 3:
                await interaction.response.send_message(
                    "A third map is not available in the active rotation.",
                    ephemeral=True
                )
                return

            await self.record_vote(interaction, self.visible_maps[2]["key"])

        @discord.ui.button(
            label="? Random",
            style=discord.ButtonStyle.secondary
        )
        async def random_map(self, interaction, button):
            await self.record_vote(interaction, "random")

    class ReadyCheckView(discord.ui.View):
        def __init__(self, players, parent_channel, thread):
            super().__init__(timeout=READY_CHECK_TIMEOUT_SECONDS)
            self.players = players
            self.parent_channel = parent_channel
            self.thread = thread
            self.ready_discord_ids = set()
            self.completed = False
            self.message = None
            self.deadline_task = None
            self.deadline = (
                discord.utils.utcnow()
                + timedelta(seconds=READY_CHECK_TIMEOUT_SECONDS)
            )

        def start_deadline_task(self):
            self.deadline_task = bot.loop.create_task(
                self.finish_at_deadline()
            )

        async def finish_at_deadline(self):
            await discord.utils.sleep_until(self.deadline)
            await self.on_timeout()

        def close(self):
            if (
                self.deadline_task is not None
                and self.deadline_task is not asyncio.current_task()
            ):
                self.deadline_task.cancel()

            if self in active_ready_checks:
                active_ready_checks.remove(self)

            self.stop()

        def player_discord_ids(self):
            return {
                int(player["discord_id"])
                for player in self.players
                if player.get("discord_id") is not None
            }

        async def update_ready_message(self):
            if self.message is None:
                return

            await self.message.edit(
                embed=build_ready_check_embed(
                    self.players,
                    len(self.ready_discord_ids),
                    self.deadline,
                    self.ready_discord_ids
                ),
                view=self
            )

        async def delete_ready_message(self):
            if self.message is None:
                return

            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

        def ready_players(self):
            return [
                player
                for player in self.players
                if (
                    player.get("discord_id") is not None
                    and int(player["discord_id"]) in self.ready_discord_ids
                )
            ]

        def unready_players(self):
            return [
                player
                for player in self.players
                if not (
                    player.get("discord_id") is not None
                    and int(player["discord_id"]) in self.ready_discord_ids
                )
            ]

        async def penalize_unready_players(self):
            players = self.unready_players()

            if not players:
                return

            try:
                settings = rating_settings()
                min_rating = 1400 if settings.get("lock_min_rating") else 0
            except requests.RequestException:
                min_rating = 0
                await send_bot_report(
                    "Could not check rating settings before ready-check penalties."
                )

            penalized_players = []

            for player in players:
                try:
                    new_mmr = max(
                        min_rating,
                        float(player["mmr"]) - READY_CHECK_MISSED_PENALTY
                    )
                    updated_player = update_player_mmr(player["id"], new_mmr)
                    penalized_players.append(updated_player)
                    await sync_player_discord_profile(
                        updated_player,
                        self.parent_channel.guild.id
                    )
                except (KeyError, TypeError, ValueError, requests.RequestException):
                    await send_bot_report(
                        (
                            "Could not apply ready-check penalty to "
                            f"{player.get('username', 'unknown player')}."
                        )
                    )

            if penalized_players:
                names = ", ".join(
                    player["username"]
                    for player in penalized_players
                )
                await send_bot_report(
                    (
                        "Applied ready-check missed penalty "
                        f"(-{READY_CHECK_MISSED_PENALTY} MMR) to: {names}."
                    )
                )

        async def return_players_to_queue(self, players=None):
            players_to_return = self.players if players is None else players

            async with queue_state.queue_flow_lock:
                if players_to_return:
                    requeue_players_at_front(players_to_return)
                    bot.loop.create_task(
                        add_in_queue_role_to_players(
                            self.parent_channel,
                            players_to_return
                        )
                    )

                clear_last_queue_action()
                await send_queue_panel(self.parent_channel)

        async def send_map_vote(self, match):
            try:
                map_pool = active_map_pool()
            except requests.RequestException:
                map_pool = PALADINS_MAPS
                await send_bot_report(
                    "Could not fetch active map rotation, so the full map pool "
                    "was used for map select."
                )

            try:
                blocked_map_key = latest_completed_match_map_key()
            except requests.RequestException:
                blocked_map_key = None
                await send_bot_report(
                    "Could not fetch the latest completed match, so no map "
                    "was excluded from map select."
                )

            view = MapVoteView(
                match,
                self.players,
                self.parent_channel,
                self.thread,
                map_pool,
                blocked_map_key
            )
            file = map_vote_preview_file(view.visible_maps)
            view.message = await self.thread.send(
                embed=build_map_vote_embed(
                    view.visible_maps,
                    view.vote_counts(),
                    view.deadline,
                    view.preview_image_url
                ),
                view=view,
                file=file
            )
            view.start_deadline_task()

        async def on_timeout(self):
            if self.completed:
                return

            self.completed = True
            self.close()
            ready_players = self.ready_players()

            for item in self.children:
                item.disabled = True

            try:
                await self.update_ready_message()
            except discord.HTTPException:
                pass

            await self.penalize_unready_players()
            await self.return_players_to_queue(ready_players)

            try:
                await self.thread.delete(
                    reason="Round Table ready check timed out"
                )
            except discord.HTTPException:
                pass

            new_ready_check_players = await create_match_if_queue_ready(
                self.parent_channel
            )
            new_ready_check_player_ids = {
                player["id"]
                for player in new_ready_check_players
            }
            players_to_unlock = [
                player
                for player in self.players
                if player["id"] not in new_ready_check_player_ids
            ]

            bot.loop.create_task(
                remove_in_game_role_from_players(
                    self.parent_channel,
                    players_to_unlock
                )
            )

        async def start_match(self, interaction):
            self.completed = True
            self.close()

            for item in self.children:
                item.disabled = True

            await interaction.response.defer()
            await self.delete_ready_message()

            match = find_best_match_for_queue(self.players)

            if match is None:
                bot.loop.create_task(
                    remove_in_game_role_from_players(
                        self.parent_channel,
                        self.players
                    )
                )
                await self.return_players_to_queue()
                await self.thread.send(
                    "Everyone readied up, but I could not create a valid match."
                )
                return

            await self.send_map_vote(match)

        async def force_ready_test_players(self):
            if self.completed:
                return 0

            ready_count_before = len(self.ready_discord_ids)

            for player in self.players:
                username = player.get("username", "")

                if not username.lower().startswith("test"):
                    continue

                if player.get("discord_id") is None:
                    continue

                self.ready_discord_ids.add(int(player["discord_id"]))

            if len(self.ready_discord_ids) == len(self.players):
                self.completed = True
                self.close()

                for item in self.children:
                    item.disabled = True

                await self.delete_ready_message()

                match = find_best_match_for_queue(self.players)

                if match is None:
                    bot.loop.create_task(
                        remove_in_game_role_from_players(
                            self.parent_channel,
                            self.players
                        )
                    )
                    await self.return_players_to_queue()
                    await self.thread.send(
                        "Everyone readied up, but I could not create a valid match."
                    )
                    return len(self.ready_discord_ids) - ready_count_before

                await self.send_map_vote(match)
                return len(self.ready_discord_ids) - ready_count_before

            await self.update_ready_message()
            return len(self.ready_discord_ids) - ready_count_before

        @discord.ui.button(
            label="Ready Up!",
            style=discord.ButtonStyle.success
        )
        async def ready_up(self, interaction, button):
            allowed_discord_ids = self.player_discord_ids()

            if interaction.user.id not in allowed_discord_ids:
                await interaction.response.send_message(
                    "Only players in this queue can ready up.",
                    ephemeral=True
                )
                return

            if interaction.user.id in self.ready_discord_ids:
                await interaction.response.send_message(
                    "You are already ready.",
                    ephemeral=True
                )
                return

            self.ready_discord_ids.add(interaction.user.id)

            if len(self.ready_discord_ids) == len(self.players):
                await self.start_match(interaction)
                return

            await interaction.response.edit_message(
                embed=build_ready_check_embed(
                    self.players,
                    len(self.ready_discord_ids),
                    self.deadline,
                    self.ready_discord_ids
                ),
                view=self
            )

    async def create_ready_check_thread(channel, players):
        thread = await channel.create_thread(
            name="queue-ready",
            type=discord.ChannelType.private_thread,
            reason="Round Table ready check"
        )

        bot.loop.create_task(
            dm_ready_check_players(players, thread)
        )

        mentions = queued_player_mentions(players)

        if mentions:
            await thread.send(f"Ready check: {mentions}")

        view = ReadyCheckView(players, channel, thread)
        view.message = await thread.send(
            embed=build_ready_check_embed(
                players,
                0,
                view.deadline,
                set()
            ),
            view=view
        )
        active_ready_checks.append(view)
        view.start_deadline_task()
        bot.loop.create_task(
            add_in_game_role_to_players_then_release(channel, players)
        )

        return thread

    async def dm_ready_check_players(players, thread):
        failed_players = []

        for player in players:
            discord_id = player.get("discord_id")

            if discord_id is None:
                continue

            try:
                member = await find_member(bot, discord_id, thread.guild.id)

                if member is None:
                    failed_players.append(player.get("username", str(discord_id)))
                    continue

                await member.send(
                    "Queue ready-check is ready. Please accept the queue."
                )
            except (discord.Forbidden, discord.HTTPException, ValueError):
                failed_players.append(player.get("username", str(discord_id)))

        if failed_players:
            await send_bot_report(
                (
                    "Could not DM ready-check notification to: "
                    f"{', '.join(failed_players)}."
                ),
                thread,
            )

    async def create_match_if_queue_ready(channel, lock_already_held=False):
        if lock_already_held:
            return await create_match_if_queue_ready_unlocked(channel)

        async with queue_state.queue_flow_lock:
            return await create_match_if_queue_ready_unlocked(channel)

    async def create_match_if_queue_ready_unlocked(channel):
        if len(current_queue) < 10:
            return []

        queued_players_for_match = queued_players_for_next_match()
        remove_players_from_current_queue(queued_players_for_match)
        reserve_players_for_match_flow(queued_players_for_match)
        bot.loop.create_task(
            remove_in_queue_role_from_players(
                channel,
                queued_players_for_match
            )
        )
        clear_last_queue_action()

        await send_queue_panel(channel)

        try:
            await create_ready_check_thread(channel, queued_players_for_match)
            return queued_players_for_match
        except discord.HTTPException:
            requeue_players_at_front(queued_players_for_match)
            release_players_from_match_flow(queued_players_for_match)
            bot.loop.create_task(
                add_in_queue_role_to_players(
                    channel,
                    queued_players_for_match
                )
            )
            await send_queue_panel(channel)
            await send_bot_report(
                "Could not create a ready-check thread, so the players were "
                "returned to the queue."
            )
            return []

    @bot.tree.command(
        name="help",
        description="Show BossQueue commands"
    )
    async def help_command(interaction: Interaction):
        embed = discord.Embed(
            title="BossQueue Commands",
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="Player",
            value=(
                "`/setupplayer ign region role` - Register your IGN, region, and role.\n"
                "`/leaderboard` - Show the MMR leaderboard.\n"
                "`/stats [member]` - Show a player stats card.\n"
                "`/checkelo` - Check your current MMR and rank.\n"
                "`/changerole role` - Change your queue role preference.\n"
                "`/changeign ign` - Change your in-game name.\n"
                "`/changeregion region` - Change your region."
            ),
            inline=False
        )

        embed.add_field(
            name="Queue And Match",
            value=(
                "`Join Queue` button - Join the main queue.\n"
                "`Leave Queue` button - Leave the main queue.\n"
                "`/startcancelvote` - Start a match cancel vote inside an active match thread."
            ),
            inline=False
        )

        embed.add_field(
            name="Admin Setup",
            value=(
                "`/adminsetupboss ...` - Configure queue/results/admin/report channels, "
                "queue/game/access roles, rank roles, and Elo names.\n"
                "`/admintoggleeloign state` - Turn Elo nicknames on or off for this server.\n"
                "`/admintogglerankroles state` - Turn visual rank/streak role syncing on or off.\n"
                "`/adminsyncranks` - Sync rank and streak visual roles for all players."
            ),
            inline=False
        )

        embed.add_field(
            name="Admin Player Tools",
            value=(
                "`/adminchangerole member role` - Change another player's queue role preference.\n"
                "`/adminchangeign member ign` - Change another player's in-game name.\n"
                "`/adminsetmmr member mmr` - Set a player's MMR.\n"
                "`/adminaddplayer member` - Add a registered Discord member to the queue.\n"
                "`/adminremoveplayer member` - Remove a Discord member from the current queue.\n"
                "`/adminlockminrating state` - Toggle the 1400 minimum rating lock.\n"
                "`/adminchangedecay start repeat loss` - Change decay settings.\n"
                "`/adminelochanges ...` - Change Elo formula settings.\n"
                "`/adminrundecay` - Run rating decay manually."
            ),
            inline=False
        )

        embed.add_field(
            name="Admin Match Tools",
            value=(
                "`/adminrestartque` - Empty the current main queue.\n"
                "`/adminlockque` - Stop players from joining the main queue.\n"
                "`/adminunlockque` - Allow players to join the main queue again.\n"
                "`/adminclearreservations` - Clear temporary match-flow queue reservations.\n"
                "`/adminsyncingameroles` - Remove stale IN GAME roles from players not in pending matches.\n"
                "`/admincancelpendingmatches` - Cancel matches still waiting for a result.\n"
                "`/admincancelgame match_number` - Revoke a match and reverse stored MMR changes.\n"
                "`/adminchangewinner match_number` - Flip a completed match winner and recalculate Elo.\n"
                "`/admincancelbypunish match_number member` - Cancel/refund a match and punish one player.\n"
                "`/adminwinbypunish match_number member` - Cancel a match and award the other team."
            ),
            inline=False
        )

        embed.add_field(
            name="Admin Maps",
            value=(
                "`/adminaddmap map_name` - Add a map to active rotation.\n"
                "`/adminremovemap map_name` - Remove a map from active rotation.\n"
                "`/adminmaprotation` - List current active map rotation."
            ),
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(
        name="neatqueuetest",
        description="Admin: fetch NeatQueue players and write them to a test file"
    )
    async def neatqueuetest(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        players_url = neatqueue_players_url()

        if not players_url:
            await interaction.followup.send(
                (
                    "Missing NeatQueue players endpoint. Add either "
                    "`NEATQUEUE_PLAYERS_URL` as the exact player/export URL, "
                    "or `NEATQUEUE_API_URL` if `/players` is the correct path."
                ),
                ephemeral=True
            )
            return

        if not NEATQUEUE_AUTHORIZATION:
            await interaction.followup.send(
                (
                    "Missing NeatQueue authorization token. Add it to `.env` as "
                    "`Authorization=...` or `NEATQUEUE_AUTHORIZATION=...`."
                ),
                ephemeral=True
            )
            return

        try:
            response = requests.get(
                players_url,
                headers={"Authorization": NEATQUEUE_AUTHORIZATION},
                timeout=30,
            )
            response.raise_for_status()
        except requests.HTTPError as error:
            status_code = (
                error.response.status_code
                if error.response is not None
                else "unknown"
            )
            await interaction.followup.send(
                (
                    "NeatQueue request failed with HTTP "
                    f"`{status_code}`. Check the endpoint URL and token."
                ),
                ephemeral=True
            )
            return
        except requests.RequestException as error:
            await interaction.followup.send(
                f"Could not reach NeatQueue: `{error}`",
                ephemeral=True
            )
            return

        try:
            payload = response.json()
            output_text = json.dumps(payload, indent=2, ensure_ascii=False)
        except ValueError:
            output_text = response.text

        NEATQUEUE_TEST_OUTPUT_PATH.write_text(
            output_text,
            encoding="utf-8"
        )

        await interaction.followup.send(
            (
                "NeatQueue response saved to "
                f"`{NEATQUEUE_TEST_OUTPUT_PATH}`."
            ),
            file=discord.File(NEATQUEUE_TEST_OUTPUT_PATH),
            ephemeral=True,
        )

    async def load_test_players_into_queue(interaction, amount):
        response = requests.get(f"{DJANGO_API_URL}/players/")

        if response.status_code != 200:
            await interaction.followup.send(
                "Failed to fetch players from backend.",
                ephemeral=True
            )
            return

        backend_players = response.json()

        async with queue_state.queue_flow_lock:
            existing_player_ids = {
                player["id"]
                for player in current_queue
            }
            open_slots = max(0, 10 - len(current_queue))
            load_limit = min(amount, open_slots)
            test_players = [
                player
                for player in backend_players
                if re.fullmatch(r"Test[0-9]+", player.get("username", ""))
                and player["id"] not in existing_player_ids
                and not discord_id_reserved_for_match(player.get("discord_id"))
            ][:load_limit]

            current_queue.extend(test_players)
            bot.loop.create_task(
                add_in_queue_role_to_players(interaction.channel, test_players)
            )

            if test_players:
                last_player = test_players[-1]
                last_queue_action["type"] = "joined"
                last_queue_action["player"] = last_player["username"]
                last_queue_action["discord_id"] = last_player["discord_id"]
                last_queue_action["mmr"] = last_player["mmr"]
                last_queue_action["message"] = None
                mark_queue_activity()
            elif not current_queue:
                last_queue_action["type"] = None
                last_queue_action["player"] = None
                last_queue_action["discord_id"] = None
                last_queue_action["mmr"] = None
                last_queue_action["message"] = None

            queue_channel = await refresh_configured_queue_panel(interaction)

            if queue_channel is not None:
                await create_match_if_queue_ready(
                    queue_channel,
                    lock_already_held=True
                )

        await interaction.followup.send(
            (
                f"Loaded {len(test_players)} test player(s). "
                f"Queue has {len(current_queue)} player(s)."
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="loadtestqueue",
        description="Load all test players from the backend into the queue"
    )
    async def loadtestqueue(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        await load_test_players_into_queue(interaction, 10)

    @bot.tree.command(
        name="loadintoque",
        description="Test: load a selected number of backend test players into the queue"
    )
    async def loadintoque(interaction: Interaction, amount: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        if amount < 1 or amount > 10:
            await interaction.response.send_message(
                "Choose a number between 1 and 10.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await load_test_players_into_queue(interaction, amount)

    @bot.tree.command(
        name="readytestque",
        description="Test: mark Test1-Test10 ready in the active ready check"
    )
    async def readytestque(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        ready_check = next(
            (
                view
                for view in active_ready_checks
                if not view.completed
            ),
            None
        )

        if ready_check is None:
            await interaction.followup.send(
                "There is no active ready check to accept.",
                ephemeral=True
            )
            return

        accepted_count = await ready_check.force_ready_test_players()

        await interaction.followup.send(
            f"Marked {accepted_count} test player(s) as ready.",
            ephemeral=True
        )

    @bot.tree.command(
        name="startcancelvote",
        description="Start a vote to cancel the active match"
    )
    async def startcancelvote(interaction: Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This command can only be used inside an active match thread.",
                ephemeral=True
            )
            return

        match = active_matches_by_thread_id.get(interaction.channel.id)

        if match is None:
            await interaction.response.send_message(
                "There is no active match going on in this thread.",
                ephemeral=True
            )
            return

        if interaction.user.id not in match_player_discord_ids(match):
            await interaction.response.send_message(
                "Only players in this match can start a cancel vote.",
                ephemeral=True
            )
            return

        if interaction.channel.id in active_cancel_votes_by_thread_id:
            await interaction.response.send_message(
                "A cancel vote is already active for this match.",
                ephemeral=True
            )
            return

        view = CancelMatchVoteView(match, interaction.channel)
        active_cancel_votes_by_thread_id[interaction.channel.id] = view

        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view
        )
        view.message = await interaction.original_response()

    @bot.tree.command(
        name="canceltest",
        description="Test: make Test players vote yes on the active cancel vote"
    )
    async def canceltest(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.followup.send(
                "This command can only be used inside an active match thread.",
                ephemeral=True
            )
            return

        cancel_vote = active_cancel_votes_by_thread_id.get(
            interaction.channel.id
        )

        if cancel_vote is None:
            await interaction.followup.send(
                "There is no active cancel vote in this thread.",
                ephemeral=True
            )
            return

        accepted_count = await cancel_vote.force_yes_test_players()

        await interaction.followup.send(
            f"Added {accepted_count} test yes vote(s).",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminaddplayer",
        description="Admin: add a registered Discord member to the queue"
    )
    async def adminaddplayer(
        interaction: Interaction,
        member: discord.Member
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        response = requests.get(f"{DJANGO_API_URL}/players/")

        if response.status_code != 200:
            await interaction.followup.send(
                "Failed to fetch players from backend.",
                ephemeral=True
            )
            return

        players = response.json()

        player = next(
            (
                p for p in players
                if str(p.get("discord_id")) == str(member.id)
            ),
            None
        )

        if player is None:
            await interaction.followup.send(
                f"{member.mention} is not registered in the backend.",
                ephemeral=True
            )
            return

        configured_in_game_role_id = in_game_role_id(interaction.guild.id)
        has_in_game_role = any(
            role.id == configured_in_game_role_id
            for role in member.roles
        ) if configured_in_game_role_id else False

        if has_in_game_role:
            await interaction.followup.send(
                f"{member.mention} is currently marked as in game.",
                ephemeral=True
            )
            return

        if discord_id_reserved_for_match(member.id):
            await interaction.followup.send(
                f"{member.mention} is already in an active match flow.",
                ephemeral=True
            )
            return

        already_in_queue = any(
            str(p.get("discord_id")) == str(member.id)
            for p in current_queue
        )

        if already_in_queue:
            await interaction.followup.send(
                f"{member.mention} is already in queue.",
                ephemeral=True
            )
            return

        async with queue_state.queue_flow_lock:
            if discord_id_reserved_for_match(member.id):
                await interaction.followup.send(
                    f"{member.mention} is already in an active match flow.",
                    ephemeral=True
                )
                return

            already_in_queue = any(
                str(p.get("discord_id")) == str(member.id)
                for p in current_queue
            )

            if already_in_queue:
                await interaction.followup.send(
                    f"{member.mention} is already in queue.",
                    ephemeral=True
                )
                return

            current_queue.append(player)

            bot.loop.create_task(
                add_in_queue_role_to_players(interaction.channel, [player])
            )

            last_queue_action["type"] = "joined"
            last_queue_action["player"] = player["username"]
            last_queue_action["discord_id"] = player["discord_id"]
            last_queue_action["mmr"] = player["mmr"]
            last_queue_action["message"] = None
            mark_queue_activity()

            queue_channel = await refresh_configured_queue_panel(interaction)

            if queue_channel is not None:
                await create_match_if_queue_ready(
                    queue_channel,
                    lock_already_held=True
                )

        await interaction.followup.send(
            f"Added {member.mention} to queue.",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminremoveplayer",
        description="Admin: remove a Discord member from the current queue"
    )
    async def adminremoveplayer(
        interaction: Interaction,
        member: discord.Member
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        queued_player = next(
            (
                player
                for player in current_queue
                if str(player.get("discord_id")) == str(member.id)
            ),
            None
        )

        if queued_player is None:
            await interaction.followup.send(
                f"{member.mention} is not in the queue.",
                ephemeral=True
            )
            return

        async with queue_state.queue_flow_lock:
            if queued_player not in current_queue:
                await interaction.followup.send(
                    f"{member.mention} is not in the queue.",
                    ephemeral=True
                )
                return

            current_queue.remove(queued_player)

            bot.loop.create_task(
                remove_in_queue_role_from_players(
                    interaction.channel,
                    [queued_player]
                )
            )

            last_queue_action["type"] = "left"
            last_queue_action["player"] = queued_player["username"]
            last_queue_action["discord_id"] = queued_player["discord_id"]
            last_queue_action["mmr"] = queued_player["mmr"]
            last_queue_action["message"] = None
            mark_queue_activity()

            await refresh_configured_queue_panel(interaction)

        await interaction.followup.send(
            f"Removed {member.mention} from queue.",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminaddmap",
        description="Admin: add a map to the active map rotation"
    )
    async def adminaddmap(interaction: Interaction, map_name: MapChoice):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        try:
            active_map = add_active_map(map_name)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not add that map to the active rotation.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"`{active_map['map_display_name']}` is now in the active map rotation.",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminremovemap",
        description="Admin: remove a map from the active map rotation"
    )
    async def adminremovemap(interaction: Interaction, map_name: MapChoice):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        try:
            remove_active_map(map_name)
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                await interaction.response.send_message(
                    "That map is not currently in the active rotation.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                "Could not remove that map from the active rotation.",
                ephemeral=True
            )
            return
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not remove that map from the active rotation.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"`{MAPS_BY_KEY[map_name]['name']}` was removed from the active map rotation.",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminmaprotation",
        description="Admin: list maps in the active map rotation"
    )
    async def adminmaprotation(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        try:
            active_maps = list_active_maps()
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not load the active map rotation.",
                ephemeral=True
            )
            return

        if not active_maps:
            await interaction.response.send_message(
                "No maps are active. Map select will use the full map pool until at least 3 maps are active.",
                ephemeral=True
            )
            return

        map_names = [
            f"- {active_map['map_display_name']}"
            for active_map in active_maps
        ]

        await interaction.response.send_message(
            "Active map rotation:\n" + "\n".join(map_names),
            ephemeral=True
        )

    @bot.tree.command(
        name="adminsetupboss",
        description="Admin: configure BossQueue channels and Discord roles"
    )
    async def adminsetupboss(
        interaction: Interaction,
        queue_channel: discord.TextChannel,
        match_results_channel: discord.TextChannel,
        admin_match_panel_channel: discord.TextChannel,
        bot_report_channel: discord.TextChannel,
        in_game_role: discord.Role,
        in_queue_role: discord.Role,
        associate_role: discord.Role,
        sent_home_role: discord.Role,
        visitor_role: discord.Role,
        ultra_boss_instinct_role: discord.Role,
        losing_streak_role: discord.Role,
        mustard_gas_role: discord.Role,
        woodhuman_role: discord.Role,
        goodmaster_role: discord.Role,
        greatmaster_role: discord.Role,
        grandmaster_role: discord.Role,
        super_grandmaster_role: discord.Role,
        super_grandmaster_god_role: discord.Role,
        elo_names: ToggleChoice,
        rank_roles: ToggleChoice,
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        setup = upsert_guild_config(
            interaction.guild.id,
            {
                "queue_channel_id": queue_channel.id,
                "match_results_channel_id": match_results_channel.id,
                "admin_match_panel_channel_id": admin_match_panel_channel.id,
                "bot_report_channel_id": bot_report_channel.id,
                "in_game_role_id": in_game_role.id,
                "in_queue_role_id": in_queue_role.id,
                "associate_role_id": associate_role.id,
                "sent_home_role_id": sent_home_role.id,
                "visitor_role_id": visitor_role.id,
                "ultra_boss_instinct_role_id": ultra_boss_instinct_role.id,
                "losing_streak_role_id": losing_streak_role.id,
                "elo_nickname_enabled": elo_names == "on",
                "rank_role_sync_enabled": rank_roles == "on",
                "queue_locked": False,
                "rank_role_ids": {
                    "mustard_gas": mustard_gas_role.id,
                    "woodhuman": woodhuman_role.id,
                    "goodmaster": goodmaster_role.id,
                    "greatmaster": greatmaster_role.id,
                    "grandmaster": grandmaster_role.id,
                    "super_grandmaster": super_grandmaster_role.id,
                    "super_grandmaster_god": super_grandmaster_god_role.id,
                },
            }
        )

        await send_queue_panel(queue_channel)

        rank_sync_text = ""

        if rank_roles == "on":
            try:
                players = all_backend_players()
                synced, failed = await sync_all_rank_roles(
                    bot,
                    players,
                    send_bot_report,
                    guild_id=interaction.guild.id
                )
                ultra_synced, ultra_failed = (
                    await sync_all_ultra_boss_instinct_roles(
                        bot,
                        players,
                        send_bot_report,
                        guild_id=interaction.guild.id
                    )
                )
                losing_synced, losing_failed = (
                    await sync_all_losing_streak_roles(
                        bot,
                        players,
                        send_bot_report,
                        guild_id=interaction.guild.id
                    )
                )
                rank_sync_text = (
                    f"\nRank role sync: `{synced}` updated, `{failed}` failed."
                    "\nUltra Boss Instinct sync: "
                    f"`{ultra_synced}` updated, `{ultra_failed}` failed."
                    "\nLosing streak sync: "
                    f"`{losing_synced}` updated, `{losing_failed}` failed."
                )
            except requests.RequestException:
                rank_sync_text = (
                    "\nRank role sync: could not load backend players."
                )

        await interaction.followup.send(
            (
                "BossQueue setup saved for this server.\n"
                f"Queue panel: {queue_channel.mention}\n"
                f"Match results: {match_results_channel.mention}\n"
                f"Admin match panels: {admin_match_panel_channel.mention}\n"
                f"Bot reports: {bot_report_channel.mention}\n"
                f"In game role: {in_game_role.mention}\n"
                f"In queue role: {in_queue_role.mention}\n"
                f"Associate role: {associate_role.mention}\n"
                f"Sent home role: {sent_home_role.mention}\n"
                f"Visitor role: {visitor_role.mention}\n"
                f"Ultra Boss Instinct role: {ultra_boss_instinct_role.mention}\n"
                f"Losing streak role: {losing_streak_role.mention}\n"
                f"Elo nicknames: `{elo_names}`\n"
                f"Rank role syncing: `{rank_roles}`\n"
                "Rank roles were saved too. You can run this command again "
                "any time to replace the setup."
                f"{rank_sync_text}"
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="admintoggleeloign",
        description="Admin: turn Elo nicknames on or off"
    )
    async def admintoggleeloign(
        interaction: Interaction,
        state: ToggleChoice
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        enabled = state == "on"
        upsert_guild_config(
            interaction.guild.id,
            {"elo_nickname_enabled": enabled}
        )
        save_elo_nickname_enabled(enabled)

        try:
            players = all_backend_players()
        except requests.RequestException:
            await interaction.followup.send(
                "The toggle was saved, but I could not load players from the backend.",
                ephemeral=True
            )
            return

        synced, failed = await sync_all_elo_nicknames(
            bot,
            players,
            enabled,
            send_bot_report,
            interaction.guild.id
        )

        status_text = "enabled" if enabled else "disabled"
        await interaction.followup.send(
            f"Elo nicknames are now {status_text}. Updated {synced} member(s); {failed} failed.",
            ephemeral=True
        )

    @bot.tree.command(
        name="admintogglerankroles",
        description="Admin: turn rank and Ultra Boss role syncing on or off"
    )
    async def admintogglerankroles(
        interaction: Interaction,
        state: ToggleChoice
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        enabled = state == "on"
        upsert_guild_config(
            interaction.guild.id,
            {"rank_role_sync_enabled": enabled}
        )

        if not enabled:
            await interaction.followup.send(
                "Rank, Ultra Boss, and losing streak role syncing is now disabled.",
                ephemeral=True
            )
            return

        try:
            players = all_backend_players()
        except requests.RequestException:
            await interaction.followup.send(
                "The toggle was saved, but I could not load players from the backend.",
                ephemeral=True
            )
            return

        synced, failed = await sync_all_rank_roles(
            bot,
            players,
            send_bot_report,
            guild_id=interaction.guild.id
        )
        ultra_synced, ultra_failed = await sync_all_ultra_boss_instinct_roles(
            bot,
            players,
            send_bot_report,
            guild_id=interaction.guild.id
        )
        losing_synced, losing_failed = await sync_all_losing_streak_roles(
            bot,
            players,
            send_bot_report,
            guild_id=interaction.guild.id
        )

        await interaction.followup.send(
            (
                "Rank, Ultra Boss, and losing streak role syncing is now enabled.\n"
                f"Synced {synced} rank role(s); {failed} failed.\n"
                "Synced "
                f"{ultra_synced} Ultra Boss Instinct role state(s); "
                f"{ultra_failed} failed.\n"
                f"Synced {losing_synced} losing streak role state(s); "
                f"{losing_failed} failed."
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="adminsyncranks",
        description="Admin: sync Discord rank roles from backend MMR"
    )
    async def adminsyncranks(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if not rank_role_sync_enabled(interaction.guild.id):
            await interaction.followup.send(
                "Rank role syncing is disabled. Use `/admintogglerankroles on` first.",
                ephemeral=True
            )
            return

        try:
            players = all_backend_players()
        except requests.RequestException:
            await interaction.followup.send(
                "Could not load players from the backend.",
                ephemeral=True
            )
            return

        synced, failed = await sync_all_rank_roles(
            bot,
            players,
            send_bot_report,
            guild_id=interaction.guild.id
        )
        ultra_synced, ultra_failed = await sync_all_ultra_boss_instinct_roles(
            bot,
            players,
            send_bot_report,
            guild_id=interaction.guild.id
        )
        losing_synced, losing_failed = await sync_all_losing_streak_roles(
            bot,
            players,
            send_bot_report,
            guild_id=interaction.guild.id
        )

        await interaction.followup.send(
            (
                f"Synced {synced} rank role(s); {failed} failed.\n"
                f"Synced {ultra_synced} Ultra Boss Instinct role state(s); "
                f"{ultra_failed} failed.\n"
                f"Synced {losing_synced} losing streak role state(s); "
                f"{losing_failed} failed."
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="adminrundecay",
        description="Admin: run rating decay for inactive players"
    )
    async def adminrundecay(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            decay_result = run_backend_rating_decay()
        except requests.RequestException:
            await interaction.followup.send(
                "Could not run rating decay.",
                ephemeral=True
            )
            return

        decayed_players = decay_result.get("decayed_players", [])

        for player in decayed_players:
            await sync_player_discord_profile(player, interaction.guild.id)

        player_lines = [
            (
                f"- {player['username']}: "
                f"{player['mmr_before']:.1f} -> {player['mmr']:.1f}"
            )
            for player in decayed_players[:10]
        ]

        message = (
            f"Decayed {len(decayed_players)} player(s). "
            f"Settings: starts after "
            f"{decay_result.get('decay_start_after_days')} day(s), "
            f"repeats every "
            f"{decay_result.get('decay_repeat_every_days')} day(s), "
            f"loss {decay_result.get('decay_mmr_loss')} MMR."
        )

        if player_lines:
            message += "\n" + "\n".join(player_lines)

        if len(decayed_players) > 10:
            message += f"\n...and {len(decayed_players) - 10} more."

        await interaction.followup.send(message, ephemeral=True)

    @bot.tree.command(
        name="adminchangedecay",
        description="Admin: change rating decay timing and MMR loss"
    )
    async def adminchangedecay(
        interaction: Interaction,
        start_after_days: int,
        repeat_every_days: int,
        mmr_loss: int
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        if start_after_days < 0 or repeat_every_days < 0 or mmr_loss < 0:
            await interaction.response.send_message(
                "Decay values must be non-negative numbers.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            settings = update_decay_settings(
                start_after_days,
                repeat_every_days,
                mmr_loss
            )
        except requests.RequestException:
            await interaction.followup.send(
                "Could not update decay settings.",
                ephemeral=True
            )
            return

        await interaction.followup.send(
            (
                "Decay settings updated.\n"
                f"Starts after: `{settings['decay_start_after_days']}` day(s)\n"
                f"Repeats every: `{settings['decay_repeat_every_days']}` day(s)\n"
                f"MMR loss: `{settings['decay_mmr_loss']}`"
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="adminelochanges",
        description="Admin: change Elo formula values"
    )
    async def adminelochanges(
        interaction: Interaction,
        win_base: float = 16.75,
        loss_base: float = 14.5,
        win_team_cap: float = 1.625,
        win_player_cap: float = 1.625,
        loss_team_cap: float = 1.25,
        loss_relief_cap: float = 1.25,
        loss_penalty_cap: float = 1.25,
        tier_2_bonus_percent: float = 5.0,
        tier_1_bonus_percent: float = 7.5,
        ultra_bonus_percent: float = 40.0
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        values = [
            win_base,
            loss_base,
            win_team_cap,
            win_player_cap,
            loss_team_cap,
            loss_relief_cap,
            loss_penalty_cap,
            tier_2_bonus_percent,
            tier_1_bonus_percent,
            ultra_bonus_percent,
        ]

        if any(value < 0 for value in values):
            await interaction.response.send_message(
                "Elo change values must be non-negative numbers.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            settings = update_elo_change_settings(
                win_base,
                loss_base,
                win_team_cap,
                win_player_cap,
                loss_team_cap,
                loss_relief_cap,
                loss_penalty_cap,
                tier_2_bonus_percent,
                tier_1_bonus_percent,
                ultra_bonus_percent
            )
        except requests.RequestException:
            await interaction.followup.send(
                "Could not update Elo change settings.",
                ephemeral=True
            )
            return

        await interaction.followup.send(
            (
                "Elo change settings updated.\n"
                f"Win base: `{settings['win_base_mmr_change']}`\n"
                f"Loss base: `{settings['loss_base_mmr_change']}`\n"
                f"Win team cap: `{settings['win_team_diff_mmr_cap']}`\n"
                f"Win player cap: `{settings['win_player_average_mmr_cap']}`\n"
                f"Loss team cap: `{settings['loss_team_diff_mmr_cap']}`\n"
                "Loss player relief cap: "
                f"`{settings['loss_player_average_mmr_relief_cap']}`\n"
                "Loss player penalty cap: "
                f"`{settings['loss_player_average_mmr_penalty_cap']}`\n"
                "Tier 2 role bonus: "
                f"`{settings['role_tier_2_win_bonus_percent'] * 100:.2f}%`\n"
                "Tier 1 role bonus: "
                f"`{settings['role_tier_1_win_bonus_percent'] * 100:.2f}%`\n"
                "Ultra Boss Instinct bonus: "
                f"`{settings['ultra_boss_instinct_win_bonus_percent'] * 100:.2f}%`"
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="adminlockminrating",
        description="Admin: turn the 1400 minimum rating lock on or off"
    )
    async def adminlockminrating(
        interaction: Interaction,
        state: ToggleChoice
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        lock_min_rating = state == "on"

        try:
            settings = update_rating_settings(lock_min_rating)
        except requests.RequestException:
            await interaction.followup.send(
                "Could not update the minimum rating lock.",
                ephemeral=True
            )
            return

        synced = 0
        failed = 0

        if lock_min_rating and settings.get("clamped_players", 0) > 0:
            try:
                players = all_backend_players()
                synced, failed = await sync_all_elo_nicknames(
                    bot,
                    players,
                    guild_elo_nickname_enabled(interaction.guild.id),
                    send_bot_report,
                    interaction.guild.id
                )
            except requests.RequestException:
                failed = settings.get("clamped_players", 0)

        status_text = "enabled" if lock_min_rating else "disabled"
        min_rating = settings.get("min_rating", 1400 if lock_min_rating else 0)
        clamped_players = settings.get("clamped_players", 0)

        message = (
            f"Minimum rating lock is now {status_text}. "
            f"Current floor: {min_rating}. "
            f"Clamped {clamped_players} player(s)."
        )

        if clamped_players:
            message += f" Refreshed {synced} nickname(s); {failed} failed."

        await interaction.followup.send(
            message,
            ephemeral=True
        )

    @bot.tree.command(
        name="adminsetmmr",
        description="Admin: set a player's MMR"
    )
    async def adminsetmmr(
        interaction: Interaction,
        member: discord.Member,
        mmr: int
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        if mmr < 0:
            await interaction.response.send_message(
                "MMR cannot be below 0.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            settings = rating_settings()
        except requests.RequestException:
            await interaction.followup.send(
                "Could not check the minimum rating lock.",
                ephemeral=True
            )
            return

        min_rating = 1400 if settings.get("lock_min_rating") else 0

        if mmr < min_rating:
            await interaction.followup.send(
                f"The minimum rating lock is on, so MMR cannot be set below {min_rating}.",
                ephemeral=True
            )
            return

        try:
            player = find_backend_player_by_discord_id(member.id)
        except requests.RequestException:
            await interaction.followup.send(
                "Could not load players from the backend.",
                ephemeral=True
            )
            return

        if player is None:
            await interaction.followup.send(
                f"{member.mention} is not registered yet.",
                ephemeral=True
            )
            return

        try:
            updated_player = update_player_mmr(player["id"], mmr)
        except requests.RequestException:
            await interaction.followup.send(
                "Could not update that player's MMR.",
                ephemeral=True
            )
            return

        was_in_queue = sync_queued_player(updated_player)

        if was_in_queue:
            await refresh_configured_queue_panel(interaction)

        try:
            await sync_member_elo_nickname(member, updated_player)
        except (discord.Forbidden, discord.HTTPException, ValueError):
            await send_bot_report(
                f"Could not update Elo nickname for {updated_player['username']}."
            )

        try:
            await sync_member_rank_role(member, updated_player)
        except (discord.Forbidden, discord.HTTPException, ValueError):
            await send_bot_report(
                f"Could not update rank role for {updated_player['username']}."
            )

        await interaction.followup.send(
            f"{member.mention}'s MMR is now `{int(updated_player['mmr'])}`.",
            ephemeral=True
        )

    @bot.tree.command(
        name="admincancelgame",
        description="Admin: revoke a queue and reverse its Elo changes"
    )
    async def admincancelgame(interaction: Interaction, match_number: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        active_thread_id = None
        active_match = None

        try:
            backend_match = fetch_backend_match_by_number(match_number)
        except requests.RequestException:
            await interaction.followup.send(
                f"Could not look up queue `{match_number}`.",
                ephemeral=True
            )
            return

        if backend_match is None:
            await interaction.followup.send(
                f"Queue `{match_number}` was not found.",
                ephemeral=True
            )
            return

        game_id = backend_match["id"]

        for thread_id, match in active_matches_by_thread_id.items():
            if match.get("backend_match_id") == game_id:
                active_thread_id = thread_id
                active_match = match
                break

        try:
            revoked_match = revoke_backend_match(game_id)
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                await interaction.followup.send(
                    f"Queue `{match_number}` was not found.",
                    ephemeral=True
                )
                return

            await interaction.followup.send(
                f"Could not revoke queue `{match_number}`.",
                ephemeral=True
            )
            return
        except requests.RequestException:
            await interaction.followup.send(
                f"Could not revoke queue `{match_number}`.",
                ephemeral=True
            )
            return

        affected_players = revoked_match.get("affected_players", [])

        for player in affected_players:
            await sync_player_discord_profile(player, interaction.guild.id)

        if active_thread_id is not None:
            active_matches_by_thread_id.pop(active_thread_id, None)
            active_cancel_votes_by_thread_id.pop(active_thread_id, None)

            thread = bot.get_channel(active_thread_id)

            if active_match is not None:
                bot.loop.create_task(
                    remove_in_game_role_from_match(
                        interaction.channel,
                        active_match
                    )
                )

            if isinstance(thread, discord.Thread):
                await thread.delete(
                    reason="Round Table match cancelled by admin"
                )

        await interaction.followup.send(
            (
                f"Queue `{match_number}` was revoked. "
                f"Reversed Elo for {len(affected_players)} player(s)."
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="adminchangewinner",
        description="Admin: flip a completed match winner and recalculate Elo"
    )
    async def adminchangewinner(interaction: Interaction, match_number: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            backend_match = fetch_backend_match_by_number(match_number)
        except requests.RequestException:
            await interaction.followup.send(
                f"Could not look up queue `{match_number}`.",
                ephemeral=True
            )
            return

        if backend_match is None:
            await interaction.followup.send(
                f"Queue `{match_number}` was not found.",
                ephemeral=True
            )
            return

        game_id = backend_match["id"]

        try:
            changed_match = change_backend_match_winner(game_id)
        except requests.HTTPError as error:
            message = f"Could not change winner for queue `{match_number}`."

            if error.response is not None:
                if error.response.status_code == 404:
                    message = f"Queue `{match_number}` was not found."
                else:
                    try:
                        error_data = error.response.json()
                        message = error_data.get("error", message)
                    except ValueError:
                        pass

            await interaction.followup.send(message, ephemeral=True)
            return
        except requests.RequestException:
            await interaction.followup.send(
                f"Could not change winner for queue `{match_number}`.",
                ephemeral=True
            )
            return

        affected_players = changed_match.get("affected_players", [])

        for player in affected_players:
            await sync_player_discord_profile(player, interaction.guild.id)

        old_winner = changed_match.get("old_winner", "unknown")
        new_winner = changed_match.get("new_winner", "unknown")

        await interaction.followup.send(
            (
                f"Queue `{match_number}` winner was changed from `{old_winner}` "
                f"to `{new_winner}`. Recalculated Elo/streaks for "
                f"{len(affected_players)} player(s)."
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="admincancelbypunish",
        description="Admin: cancel a queue, refund it, and punish one match player"
    )
    async def admincancelbypunish(
        interaction: Interaction,
        match_number: int,
        member: discord.Member
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        active_thread_id = None
        active_match = None

        try:
            backend_match = fetch_backend_match_by_number(match_number)
        except requests.RequestException:
            await interaction.followup.send(
                f"Could not look up queue `{match_number}`.",
                ephemeral=True
            )
            return

        if backend_match is None:
            await interaction.followup.send(
                f"Queue `{match_number}` was not found.",
                ephemeral=True
            )
            return

        game_id = backend_match["id"]

        for thread_id, match in active_matches_by_thread_id.items():
            if match.get("backend_match_id") == game_id:
                active_thread_id = thread_id
                active_match = match
                break

        try:
            cancelled_match = punish_cancel_backend_match(
                game_id,
                member.id
            )
        except requests.HTTPError as error:
            message = f"Could not cancel queue `{match_number}` by punishment."

            if error.response is not None:
                if error.response.status_code == 404:
                    message = f"Queue `{match_number}` was not found."
                else:
                    try:
                        error_data = error.response.json()
                        message = error_data.get("error", message)
                    except ValueError:
                        pass

            await interaction.followup.send(message, ephemeral=True)
            return
        except requests.RequestException:
            await interaction.followup.send(
                f"Could not cancel queue `{match_number}` by punishment.",
                ephemeral=True
            )
            return

        affected_players = cancelled_match.get("affected_players", [])

        for player in affected_players:
            await sync_player_discord_profile(player, interaction.guild.id)

        if active_thread_id is not None:
            active_matches_by_thread_id.pop(active_thread_id, None)
            active_cancel_votes_by_thread_id.pop(active_thread_id, None)

            thread = bot.get_channel(active_thread_id)

            if active_match is not None:
                bot.loop.create_task(
                    remove_in_game_role_from_match(
                        interaction.channel,
                        active_match
                    )
                )

            if isinstance(thread, discord.Thread):
                await thread.delete(
                    reason="Round Table match cancelled by admin punishment"
                )

        punished_player = cancelled_match.get("punished_player", {})
        punished_name = punished_player.get("username", member.display_name)

        await interaction.followup.send(
            (
                f"Queue `{match_number}` was cancelled. "
                f"Reversed match Elo for {len(affected_players)} player(s), "
                f"then punished `{punished_name}` by 20 MMR."
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="adminwinbypunish",
        description="Admin: cancel a queue and award the opposite team by punishment"
    )
    async def adminwinbypunish(
        interaction: Interaction,
        match_number: int,
        member: discord.Member
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        active_thread_id = None
        active_match = None

        try:
            backend_match = fetch_backend_match_by_number(match_number)
        except requests.RequestException:
            await interaction.followup.send(
                f"Could not look up queue `{match_number}`.",
                ephemeral=True
            )
            return

        if backend_match is None:
            await interaction.followup.send(
                f"Queue `{match_number}` was not found.",
                ephemeral=True
            )
            return

        game_id = backend_match["id"]

        for thread_id, match in active_matches_by_thread_id.items():
            if match.get("backend_match_id") == game_id:
                active_thread_id = thread_id
                active_match = match
                break

        try:
            punished_win_match = win_by_punish_backend_match(
                game_id,
                member.id
            )
        except requests.HTTPError as error:
            message = f"Could not award queue `{match_number}` by punishment."

            if error.response is not None:
                if error.response.status_code == 404:
                    message = f"Queue `{match_number}` was not found."
                else:
                    try:
                        error_data = error.response.json()
                        message = error_data.get("error", message)
                    except ValueError:
                        pass

            await interaction.followup.send(message, ephemeral=True)
            return
        except requests.RequestException:
            await interaction.followup.send(
                f"Could not award queue `{match_number}` by punishment.",
                ephemeral=True
            )
            return

        affected_players = punished_win_match.get("affected_players", [])
        awarded_players = punished_win_match.get("awarded_players", [])

        for player in affected_players:
            await sync_player_discord_profile(player, interaction.guild.id)

        if active_thread_id is not None:
            active_matches_by_thread_id.pop(active_thread_id, None)
            active_cancel_votes_by_thread_id.pop(active_thread_id, None)

            thread = bot.get_channel(active_thread_id)

            if active_match is not None:
                bot.loop.create_task(
                    remove_in_game_role_from_match(
                        interaction.channel,
                        active_match
                    )
                )

            if isinstance(thread, discord.Thread):
                await thread.delete(
                    reason="Round Table match awarded by admin punishment"
                )

        punished_player = punished_win_match.get("punished_player", {})
        punished_name = punished_player.get("username", member.display_name)
        winning_team = punished_win_match.get("winning_team", "opposite team")
        award = punished_win_match.get("award_mmr_change", 15)
        punishment = abs(punished_win_match.get("punishment_mmr_change", -20))

        await interaction.followup.send(
            (
                f"Queue `{match_number}` was cancelled by punishment. "
                f"`{punished_name}` was on the punished side, so `{winning_team}` "
                f"received +{award} MMR each. "
                f"`{punished_name}` lost {punishment} MMR. "
                f"Awarded {len(awarded_players)} player(s). "
                "No loss/streak penalty was applied to the punished player's teammates."
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="adminrestartque",
        description="Admin: empty the current main queue"
    )
    async def adminrestartque(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        async with queue_state.queue_flow_lock:
            queued_players = list(current_queue)
            removed_count = len(current_queue)
            bot.loop.create_task(
                remove_in_queue_role_from_players(
                    interaction.channel,
                    queued_players
                )
            )
            current_queue.clear()
            last_queue_action["type"] = "inactive"
            last_queue_action["player"] = None
            last_queue_action["discord_id"] = None
            last_queue_action["mmr"] = None
            last_queue_action["message"] = (
                "Queue restarted by an admin.\n"
                "Re-enter the queue if you are still looking to play!"
            )
            queue_state.last_queue_activity_at = None

            await refresh_configured_queue_panel(interaction)

        await interaction.response.send_message(
            f"Restarted the main queue. Removed {removed_count} player(s).",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminlockque",
        description="Admin: stop players from joining the main queue"
    )
    async def adminlockque(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.",
                ephemeral=True
            )
            return

        upsert_guild_config(
            interaction.guild.id,
            {
                "queue_locked": True,
            }
        )

        async with queue_state.queue_flow_lock:
            queued_players = list(current_queue)
            removed_count = len(current_queue)
            bot.loop.create_task(
                remove_in_queue_role_from_players(
                    interaction.channel,
                    queued_players
                )
            )
            current_queue.clear()
            clear_last_queue_action()
            queue_state.last_queue_activity_at = None

            await refresh_configured_queue_panel(interaction)

        await interaction.response.send_message(
            (
                "Queue locked. The main panel now shows that queues have been "
                f"stopped. Removed {removed_count} queued player(s)."
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="adminunlockque",
        description="Admin: allow players to join the main queue again"
    )
    async def adminunlockque(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.",
                ephemeral=True
            )
            return

        upsert_guild_config(
            interaction.guild.id,
            {
                "queue_locked": False,
            }
        )

        await refresh_configured_queue_panel(interaction)

        await interaction.response.send_message(
            "Queue unlocked. Players can join the queue again.",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminclearreservations",
        description="Admin: clear temporary match-flow queue reservations"
    )
    async def adminclearreservations(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        cleared_count = len(queue_state.active_match_discord_ids)
        queue_state.active_match_discord_ids.clear()

        await interaction.response.send_message(
            f"Cleared {cleared_count} temporary match-flow reservation(s).",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminsyncingameroles",
        description="Admin: remove stale IN GAME roles from players not in pending matches"
    )
    async def adminsyncingameroles(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used inside a server.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        role_id = in_game_role_id(interaction.guild.id)
        in_game_role = interaction.guild.get_role(role_id) if role_id else None

        if in_game_role is None:
            await interaction.followup.send(
                "The IN GAME role is not configured or could not be found.",
                ephemeral=True
            )
            return

        try:
            active_discord_ids = set()

            for match in pending_backend_matches():
                for match_player in match["match_players"]:
                    player = find_backend_player_by_id(match_player["player"])
                    discord_id = player.get("discord_id")

                    if discord_id is not None:
                        active_discord_ids.add(str(discord_id))
        except requests.RequestException:
            await interaction.followup.send(
                "Could not load pending matches from the backend.",
                ephemeral=True
            )
            return

        kept_count = 0
        removed_count = 0
        failed_count = 0

        for member in list(in_game_role.members):
            if str(member.id) in active_discord_ids:
                kept_count += 1
                continue

            try:
                await remove_in_game_role(member)
                removed_count += 1
            except (discord.Forbidden, discord.HTTPException):
                failed_count += 1
                await send_bot_report(
                    f"Could not remove stale IN GAME role from {member.display_name}.",
                    interaction
                )

        await interaction.followup.send(
            (
                f"Checked {len(in_game_role.members)} member(s) with IN GAME.\n"
                f"Kept {kept_count} active member(s).\n"
                f"Removed {removed_count} stale role(s).\n"
                f"Failed {failed_count} removal(s)."
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="admincancelpendingmatches",
        description="Admin: cancel all backend matches still waiting for a result"
    )
    async def admincancelpendingmatches(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            cancelled_matches = cancel_pending_backend_matches()
        except requests.RequestException:
            await interaction.followup.send(
                "Failed to cancel pending matches in the backend.",
                ephemeral=True
            )
            return

        removed_role_player_ids = set()

        for match in cancelled_matches:
            for match_player in match["match_players"]:
                player_id = match_player["player"]

                if player_id in removed_role_player_ids:
                    continue

                removed_role_player_ids.add(player_id)

                try:
                    player_response = requests.get(
                        f"{DJANGO_API_URL}/players/{player_id}/"
                    )
                    player_response.raise_for_status()
                    player = player_response.json()
                    member = await find_member(
                        bot,
                        player["discord_id"],
                        interaction.guild.id
                    )

                    if member is None:
                        raise ValueError("Discord member was not found.")

                    await remove_in_game_role(member)
                except (
                    requests.RequestException,
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                    ValueError
                ):
                    await send_bot_report(
                        "Could not remove the in-game role from "
                        f"player id {player_id}."
                    )

        async with queue_state.queue_flow_lock:
            reset_local_queue_state()
            await refresh_configured_queue_panel(interaction)

        await interaction.followup.send(
            f"Cancelled {len(cancelled_matches)} pending match(es).",
            ephemeral=True
        )

    @bot.tree.command(
        name="checkelo",
        description="Check your current MMR"
    )
    async def checkelo(interaction: Interaction):
        try:
            player = find_backend_player_by_discord_id(interaction.user.id)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not load your player profile.",
                ephemeral=True
            )
            return

        if player is None:
            await interaction.response.send_message(
                "You are not registered yet. Join the queue once first.",
                ephemeral=True
            )
            return

        rank_display = player.get("rank_display", player.get("rank", "Unknown"))

        await interaction.response.send_message(
            (
                f"Your current MMR is `{player['mmr']:.1f}`.\n"
                f"Rank: `{rank_display}`"
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="leaderboard",
        description="Show the MMR leaderboard"
    )
    async def leaderboard(interaction: Interaction):
        await interaction.response.defer()

        try:
            leaderboard_data = fetch_leaderboard("mmr")
        except requests.RequestException:
            await interaction.followup.send(
                "Could not load the leaderboard.",
                ephemeral=True
            )
            return

        entries = leaderboard_data.get("entries", [])

        if not entries:
            await interaction.followup.send(
                "The leaderboard is empty.",
                ephemeral=True
            )
            return

        view = LeaderboardView(entries, "mmr")

        await interaction.followup.send(
            embed=view.current_embed(),
            file=view.current_file(),
            view=view
        )

    @bot.tree.command(
        name="stats",
        description="Show your player stats"
    )
    async def stats(
        interaction: Interaction,
        member: Optional[discord.Member] = None
    ):
        target_member = member or interaction.user
        await interaction.response.defer()

        try:
            stats_data = fetch_player_stats(target_member.id)
        except requests.RequestException:
            await interaction.followup.send(
                "Could not load player stats.",
                ephemeral=True
            )
            return

        if stats_data is None:
            await interaction.followup.send(
                "That player is not registered yet.",
                ephemeral=True
            )
            return

        await interaction.followup.send(file=stats_image_file(stats_data))

    @bot.tree.command(
        name="setupplayer",
        description="Set up your IGN, region, and preferred role"
    )
    async def setupplayer(
        interaction: Interaction,
        ign: str,
        region: RegionChoice,
        role: RoleChoice
    ):
        if role in SELF_CHANGE_BLOCKED_ROLES:
            await interaction.response.send_message(
                "You cannot set yourself to mono support.",
                ephemeral=True
            )
            return

        if interaction.guild and member_has_in_game_role(interaction.user):
            await interaction.response.send_message(
                "You cannot change your player setup while marked as in game.",
                ephemeral=True
            )
            return

        try:
            player = find_backend_player_by_discord_id(interaction.user.id)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not load your player profile.",
                ephemeral=True
            )
            return

        if player is not None:
            await interaction.response.send_message(
                "You are already registered. Use `/changeign`, `/changeregion`, or `/changerole` instead.",
                ephemeral=True
            )
            return

        starting_mmr = None
        imported_from_neatqueue = False

        try:
            neatqueue_player = fetch_neatqueue_player_by_discord_id(
                interaction.user.id
            )
        except requests.RequestException:
            neatqueue_player = None
            await send_bot_report(
                (
                    "Could not check NeatQueue data while registering "
                    f"{interaction.user.display_name}."
                ),
                interaction,
            )

        neatqueue_source_mmr = (
            neatqueue_player_mmr(neatqueue_player)
            if neatqueue_player is not None
            else None
        )

        if neatqueue_source_mmr is not None:
            starting_mmr = convert_neatqueue_mmr(neatqueue_source_mmr)
            imported_from_neatqueue = True
            await send_bot_report(
                (
                    "Imported NeatQueue MMR for "
                    f"{interaction.user.display_name}: "
                    f"{float(neatqueue_source_mmr):.1f} -> "
                    f"{starting_mmr:.1f}."
                ),
                interaction,
            )

        try:
            player = create_backend_player_from_user(
                interaction.user,
                starting_mmr
            )
            updated_player = update_player_ign(player["id"], ign)
            updated_player = update_player_region(updated_player["id"], region)
            updated_player = update_player_role(updated_player["id"], role)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not update your player setup.",
                ephemeral=True
            )
            return

        was_in_queue = sync_queued_player(updated_player)

        if was_in_queue:
            await refresh_configured_queue_panel(interaction)

        try:
            await sync_discord_role(interaction.user, role)
        except (discord.Forbidden, discord.HTTPException, ValueError) as error:
            await interaction.response.send_message(
                (
                    "Your backend profile was updated, but I could not update "
                    "your Discord role. "
                    f"{await role_sync_error_message(error)}"
                ),
                ephemeral=True
            )
            return

        try:
            await sync_member_elo_nickname(interaction.user, updated_player)
        except (discord.Forbidden, discord.HTTPException, ValueError):
            await send_bot_report(
                f"Could not update Elo nickname for {updated_player['username']}."
            )

        try:
            await sync_member_rank_role(interaction.user, updated_player)
        except (discord.Forbidden, discord.HTTPException, ValueError):
            await send_bot_report(
                f"Could not update rank role for {updated_player['username']}."
            )

        await interaction.response.send_message(
            (
                "Player setup saved.\n"
                f"Starting MMR: `{updated_player['mmr']:.1f}`"
                f"{' (imported from NeatQueue)' if imported_from_neatqueue else ''}\n"
                f"IGN: `{updated_player['ign']}`\n"
                f"Region: `{updated_player['region']}`\n"
                f"Role: `{updated_player['role_preference']}`"
            ),
            ephemeral=True
        )

    @bot.tree.command(
        name="changerole",
        description="Change your preferred queue role"
    )
    async def changerole(interaction: Interaction, role: RoleChoice):
        if role in SELF_CHANGE_BLOCKED_ROLES:
            await interaction.response.send_message(
                "You cannot change yourself to mono support.",
                ephemeral=True
            )
            return

        if interaction.guild and member_has_in_game_role(interaction.user):
            await interaction.response.send_message(
                "You cannot change your role while marked as in game.",
                ephemeral=True
            )
            return

        try:
            player = find_backend_player_by_discord_id(interaction.user.id)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not load your player profile.",
                ephemeral=True
            )
            return

        if player is None:
            await interaction.response.send_message(
                "You are not registered yet. Join the queue once first.",
                ephemeral=True
            )
            return

        cooldown_remaining = role_change_cooldown_remaining(player)

        if cooldown_remaining is not None:
            await interaction.response.send_message(
                (
                    "You can only change your role once every 8 hours. "
                    f"Try again in `{format_timedelta(cooldown_remaining)}`."
                ),
                ephemeral=True
            )
            return

        try:
            updated_player = update_player_role(
                player["id"],
                role,
                record_role_change=True
            )
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not update your role.",
                ephemeral=True
            )
            return

        was_in_queue = sync_queued_player(updated_player)

        if was_in_queue:
            await refresh_configured_queue_panel(interaction)

        try:
            await sync_discord_role(interaction.user, role)
        except (discord.Forbidden, discord.HTTPException, ValueError) as error:
            await interaction.response.send_message(
                "Your backend role was updated, but I could not update your "
                f"Discord role. {await role_sync_error_message(error)}",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Your preferred role is now `{role}`.",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminchangerole",
        description="Admin: change another player's preferred queue role"
    )
    async def adminchangerole(
        interaction: Interaction,
        member: discord.Member,
        role: RoleChoice
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        try:
            player = find_backend_player_by_discord_id(member.id)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not load players from the backend.",
                ephemeral=True
            )
            return

        if player is None:
            await interaction.response.send_message(
                f"{member.mention} is not registered yet.",
                ephemeral=True
            )
            return

        try:
            updated_player = update_player_role(player["id"], role)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not update that player's role.",
                ephemeral=True
            )
            return

        was_in_queue = sync_queued_player(updated_player)

        if was_in_queue:
            await refresh_configured_queue_panel(interaction)

        if member is not None:
            try:
                await sync_discord_role(member, role)
            except (
                discord.Forbidden,
                discord.HTTPException,
                ValueError
            ) as error:
                await interaction.response.send_message(
                    "Backend role was updated, but I could not update their "
                    f"Discord role. {await role_sync_error_message(error)}",
                    ephemeral=True
                )
                return

        await interaction.response.send_message(
            f"`{updated_player['username']}` preferred role is now `{role}`.",
            ephemeral=True
        )

    @bot.tree.command(
        name="changeign",
        description="Change your in-game name"
    )
    async def changeign(interaction: Interaction, ign: str):
        try:
            player = find_backend_player_by_discord_id(interaction.user.id)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not load your player profile.",
                ephemeral=True
            )
            return

        if player is None:
            await interaction.response.send_message(
                "You are not registered yet. Join the queue once first.",
                ephemeral=True
            )
            return

        try:
            updated_player = update_player_ign(player["id"], ign)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not update your IGN.",
                ephemeral=True
            )
            return

        was_in_queue = sync_queued_player(updated_player)

        if was_in_queue:
            await refresh_configured_queue_panel(interaction)

        await interaction.response.send_message(
            f"Your IGN is now `{updated_player['ign']}`.",
            ephemeral=True
        )

    @bot.tree.command(
        name="changeregion",
        description="Change your region"
    )
    async def changeregion(interaction: Interaction, region: RegionChoice):
        try:
            player = find_backend_player_by_discord_id(interaction.user.id)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not load your player profile.",
                ephemeral=True
            )
            return

        if player is None:
            await interaction.response.send_message(
                "You are not registered yet. Join the queue once first.",
                ephemeral=True
            )
            return

        try:
            updated_player = update_player_region(player["id"], region)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not update your region.",
                ephemeral=True
            )
            return

        was_in_queue = sync_queued_player(updated_player)

        if was_in_queue:
            await refresh_configured_queue_panel(interaction)

        await interaction.response.send_message(
            f"Your region is now `{updated_player['region']}`.",
            ephemeral=True
        )

    @bot.tree.command(
        name="adminchangeign",
        description="Admin: change another player's in-game name"
    )
    async def adminchangeign(
        interaction: Interaction,
        member: discord.Member,
        ign: str
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only administrators can use this command.",
                ephemeral=True
            )
            return

        try:
            player = find_backend_player_by_discord_id(member.id)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not load players from the backend.",
                ephemeral=True
            )
            return

        if player is None:
            await interaction.response.send_message(
                f"{member.mention} is not registered yet.",
                ephemeral=True
            )
            return

        try:
            updated_player = update_player_ign(player["id"], ign)
        except requests.RequestException:
            await interaction.response.send_message(
                "Could not update that player's IGN.",
                ephemeral=True
            )
            return

        was_in_queue = sync_queued_player(updated_player)

        if was_in_queue:
            await refresh_configured_queue_panel(interaction)

        await interaction.response.send_message(
            f"`{updated_player['username']}` IGN is now `{updated_player['ign']}`.",
            ephemeral=True
        )

    return create_match_if_queue_ready
