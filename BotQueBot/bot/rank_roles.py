import discord

from elo_nickname import find_member, is_real_discord_id
from guild_config import (
    losing_streak_role_id,
    rank_role_ids,
    rank_role_sync_enabled,
    ultra_boss_instinct_role_id,
)


ULTRA_BOSS_INSTINCT_STREAK = 4
LOSING_STREAK_ROLE_STREAK = -4


def configured_rank_role_ids(guild_id):
    return {
        role_id
        for role_id in rank_role_ids(guild_id).values()
        if role_id is not None
    }


async def sync_member_rank_role(member, player):
    if not rank_role_sync_enabled(member.guild.id):
        return False

    rank = player.get("rank")

    if not rank:
        raise ValueError(
            f"No backend rank was provided for {player.get('username', 'player')}."
        )

    role_id = rank_role_ids(member.guild.id).get(rank)

    if role_id is None:
        raise ValueError(
            f"No Discord role id is configured for backend rank `{rank}`."
        )

    configured_role_ids = configured_rank_role_ids(member.guild.id)
    roles_to_remove = [
        role
        for role in member.roles
        if role.id in configured_role_ids and role.id != role_id
    ]
    role_to_add = member.guild.get_role(role_id)

    if role_to_add is None:
        raise ValueError(f"Discord rank role id `{role_id}` was not found.")

    if roles_to_remove:
        await member.remove_roles(
            *roles_to_remove,
            reason="Round Table rank changed"
        )

    if role_to_add not in member.roles:
        await member.add_roles(
            role_to_add,
            reason="Round Table rank changed"
        )

    return True


async def sync_member_ultra_boss_instinct_role(member, player):
    if not rank_role_sync_enabled(member.guild.id):
        return False

    role_id = ultra_boss_instinct_role_id(member.guild.id)
    role = member.guild.get_role(role_id)

    if role is None:
        raise ValueError(
            f"Discord Ultra Boss Instinct role id "
            f"`{role_id}` was not found."
        )

    streak = int(player.get("streak") or 0)
    should_have_role = streak >= ULTRA_BOSS_INSTINCT_STREAK

    if should_have_role and role not in member.roles:
        await member.add_roles(
            role,
            reason="Round Table Ultra Boss Instinct streak reached"
        )
    elif not should_have_role and role in member.roles:
        await member.remove_roles(
            role,
            reason="Round Table Ultra Boss Instinct streak ended"
        )

    return True


async def sync_member_losing_streak_role(member, player):
    if not rank_role_sync_enabled(member.guild.id):
        return False

    role_id = losing_streak_role_id(member.guild.id)

    if role_id is None:
        return False

    role = member.guild.get_role(role_id)

    if role is None:
        raise ValueError(
            f"Discord Losing Streak role id `{role_id}` was not found."
        )

    streak = int(player.get("streak") or 0)
    should_have_role = streak <= LOSING_STREAK_ROLE_STREAK

    if should_have_role and role not in member.roles:
        await member.add_roles(
            role,
            reason="Round Table losing streak reached"
        )
    elif not should_have_role and role in member.roles:
        await member.remove_roles(
            role,
            reason="Round Table losing streak ended"
        )

    return True


async def sync_all_rank_roles(bot, players, report_error=None, guild_id=None):
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

            if not rank_role_sync_enabled(member.guild.id):
                continue

            configured_role_ids = configured_rank_role_ids(member.guild.id)

            if not configured_role_ids:
                continue

            await sync_member_rank_role(member, player)
            synced += 1
        except (discord.Forbidden, discord.HTTPException, ValueError):
            failed += 1

            if report_error is not None:
                await report_error(
                    f"Could not update rank role for {player['username']}."
                )

    return synced, failed


async def sync_all_ultra_boss_instinct_roles(
    bot,
    players,
    report_error=None,
    guild_id=None
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

            if not rank_role_sync_enabled(member.guild.id):
                continue

            await sync_member_ultra_boss_instinct_role(member, player)
            synced += 1
        except (discord.Forbidden, discord.HTTPException, ValueError):
            failed += 1

            if report_error is not None:
                await report_error(
                    "Could not update Ultra Boss Instinct role for "
                    f"{player['username']}."
                )

    return synced, failed


async def sync_all_losing_streak_roles(
    bot,
    players,
    report_error=None,
    guild_id=None
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

            if not rank_role_sync_enabled(member.guild.id):
                continue

            did_sync = await sync_member_losing_streak_role(member, player)

            if did_sync:
                synced += 1
        except (discord.Forbidden, discord.HTTPException, ValueError):
            failed += 1

            if report_error is not None:
                await report_error(
                    "Could not update Losing Streak role for "
                    f"{player['username']}."
                )

    return synced, failed
