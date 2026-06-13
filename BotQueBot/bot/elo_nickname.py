import json
import re
from pathlib import Path

import discord

from guild_config import elo_nickname_enabled


ELO_NICKNAME_STATE_FILE = Path(__file__).with_name("elo_nickname_state.json")
ELO_SUFFIX_PATTERN = re.compile(r"\s+-\s+\(\d+(?:\.\d+)?\)$")
DISCORD_NICKNAME_LIMIT = 32
MIN_DISCORD_SNOWFLAKE_LENGTH = 15


def load_elo_nickname_enabled():
    if not ELO_NICKNAME_STATE_FILE.exists():
        return False

    try:
        data = json.loads(ELO_NICKNAME_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    return bool(data.get("enabled", False))


def save_elo_nickname_enabled(enabled):
    ELO_NICKNAME_STATE_FILE.write_text(
        json.dumps({"enabled": enabled}),
        encoding="utf-8"
    )


def is_real_discord_id(discord_id):
    discord_id = str(discord_id)
    return (
        discord_id.isdigit()
        and len(discord_id) >= MIN_DISCORD_SNOWFLAKE_LENGTH
    )


async def find_member(bot, discord_id, guild_id=None):
    if not is_real_discord_id(discord_id):
        return None

    member_id = int(discord_id)
    guilds = bot.guilds

    if guild_id is not None:
        guild = bot.get_guild(int(guild_id))
        guilds = [guild] if guild is not None else []

    for guild in guilds:
        member = guild.get_member(member_id)

        if member is not None:
            return member

        try:
            return await guild.fetch_member(member_id)
        except discord.NotFound:
            continue

    return None


def base_display_name(member):
    return ELO_SUFFIX_PATTERN.sub("", member.display_name).strip()


def nickname_with_elo(member, player):
    suffix = f" - ({int(float(player['mmr']))})"
    base_name = base_display_name(member)
    max_base_length = DISCORD_NICKNAME_LIMIT - len(suffix)

    if max_base_length < 1:
        return suffix[-DISCORD_NICKNAME_LIMIT:]

    trimmed_base = base_name[:max_base_length].rstrip()
    return f"{trimmed_base}{suffix}"


async def sync_member_elo_nickname(member, player, enabled=None):
    if enabled is None:
        enabled = elo_nickname_enabled(
            member.guild.id,
            load_elo_nickname_enabled()
        )

    nickname = nickname_with_elo(member, player) if enabled else base_display_name(member)

    if member.display_name == nickname:
        return True

    await member.edit(
        nick=nickname,
        reason="Round Table Elo nickname toggle"
    )
    return True


async def sync_all_elo_nicknames(
    bot,
    players,
    enabled=None,
    report_error=None,
    guild_id=None,
):
    synced = 0
    failed = 0

    for player in players:
        discord_id = player.get("discord_id")

        if discord_id is None or not is_real_discord_id(discord_id):
            continue

        try:
            member = await find_member(bot, discord_id, guild_id)

            if member is None:
                continue

            await sync_member_elo_nickname(member, player, enabled)
            synced += 1
        except (discord.Forbidden, discord.HTTPException, ValueError) as error:
            failed += 1

            if report_error is not None:
                await report_error(
                    f"Could not update Elo nickname for {player['username']}: {error}"
                )

    return synced, failed
