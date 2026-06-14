import discord
import asyncio
import json
import requests
from pathlib import Path
from discord.ext import commands

from config import (
    DISCORD_TOKEN,
    DJANGO_API_URL,
)
from commands.queue_commands import register_queue_commands
from guild_config import (
    associate_role_id,
    configured_guild_ids,
    in_game_role_id,
    in_queue_role_id,
    queue_locked,
    queue_channel_id,
    sent_home_role_id,
    visitor_role_id,
)
from elo_nickname import (
    find_member,
    sync_all_elo_nicknames,
    sync_member_elo_nickname,
    warm_member_cache,
)
from rank_roles import (
    sync_all_losing_streak_roles,
    sync_all_rank_roles,
    sync_all_ultra_boss_instinct_roles,
    sync_member_losing_streak_role,
    sync_member_rank_role,
    sync_member_ultra_boss_instinct_role,
)
from queue_state import current_queue
import queue_state
from embeds.queue_embed import build_queue_embed

PANEL_STATE_FILE = Path(__file__).with_name("panel_state.json")
QUEUE_INACTIVITY_MINUTES = 60
QUEUE_INACTIVITY_CHECK_SECONDS = 15
DECAY_CHECK_INTERVAL_SECONDS = 60
QUEUE_PANEL_CLEANUP_HISTORY_LIMIT = 200
STARTUP_QUEUE_PANEL_RETRY_SECONDS = 3
queue_inactivity_task = None
rating_decay_task = None
startup_queue_panel_retry_task = None
queue_panel_locks = {}


def load_panel_message_id():
    if not PANEL_STATE_FILE.exists():
        return None

    try:
        data = json.loads(PANEL_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    return data.get("queue_panel_message_id")


def load_panel_message_ids():
    if not PANEL_STATE_FILE.exists():
        return {}

    try:
        data = json.loads(PANEL_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    if "queue_panel_message_id" in data:
        return {"default": data["queue_panel_message_id"]}

    return data


def save_panel_message_id(message_id):
    PANEL_STATE_FILE.write_text(
        json.dumps({"queue_panel_message_id": message_id}),
        encoding="utf-8"
    )


def save_panel_message_ids(message_ids):
    PANEL_STATE_FILE.write_text(
        json.dumps(message_ids, indent=2, sort_keys=True),
        encoding="utf-8"
    )

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)
create_match_if_queue_ready = None


def find_queue_player_by_discord_id(discord_id):
    return next(
        (
            player
            for player in current_queue
            if str(player.get("discord_id")) == str(discord_id)
        ),
        None
    )


def is_reserved_for_match(discord_id):
    return str(discord_id) in queue_state.active_match_discord_ids


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


def all_backend_players():
    response = requests.get(f"{DJANGO_API_URL}/players/")
    response.raise_for_status()
    return response.json()


def run_backend_rating_decay():
    response = requests.post(f"{DJANGO_API_URL}/rating-decay/run/")
    response.raise_for_status()
    return response.json()


async def sync_player_discord_profile(player, guild_id=None):
    if player is None or player.get("discord_id") is None:
        return

    try:
        member = await find_member(bot, player["discord_id"], guild_id)
    except (discord.HTTPException, ValueError):
        return

    if member is None:
        return

    try:
        await sync_member_elo_nickname(member, player)
    except (discord.Forbidden, discord.HTTPException, ValueError):
        pass

    try:
        await sync_member_rank_role(member, player)
    except (discord.Forbidden, discord.HTTPException, ValueError):
        pass

    try:
        await sync_member_ultra_boss_instinct_role(member, player)
    except (discord.Forbidden, discord.HTTPException, ValueError):
        pass

    try:
        await sync_member_losing_streak_role(member, player)
    except (discord.Forbidden, discord.HTTPException, ValueError):
        pass


def member_has_in_game_role(member):
    role_id = in_game_role_id(member.guild.id)
    return member_has_role_id(member, role_id)


def member_has_role_id(member, role_id):
    if role_id is None:
        return False

    return any(role.id == role_id for role in member.roles)


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


async def add_in_queue_role_background(member):
    try:
        await add_in_queue_role(member)
    except discord.HTTPException:
        pass


async def remove_in_queue_role_background(member):
    try:
        await remove_in_queue_role(member)
    except discord.HTTPException:
        pass


def mark_queue_activity():
    queue_state.last_queue_activity_at = discord.utils.utcnow()


def queue_panel_lock_for(channel):
    lock_key = (
        channel.guild.id
        if getattr(channel, "guild", None)
        else channel.id
    )

    if lock_key not in queue_panel_locks:
        queue_panel_locks[lock_key] = asyncio.Lock()

    return queue_panel_locks[lock_key]


async def configured_queue_channel_for_guild(guild_id):
    if guild_id is not None and not any(
        guild.id == guild_id
        for guild in bot.guilds
    ):
        return None

    configured_queue_channel_id = queue_channel_id(guild_id)

    if configured_queue_channel_id is None:
        return None

    channel = bot.get_channel(configured_queue_channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(configured_queue_channel_id)
        except (discord.Forbidden, discord.NotFound):
            return None

    return channel


async def refresh_configured_queue_panel(context):
    guild_id = context.guild.id if context.guild else None
    channel = await configured_queue_channel_for_guild(guild_id)

    if channel is None:
        return None

    await send_queue_panel(channel)
    return channel


async def refresh_startup_queue_panels():
    active_configured_guild_ids = [
        guild.id
        for guild in bot.guilds
        if queue_channel_id(guild.id) is not None
    ]

    for guild_id in active_configured_guild_ids:
        channel = await configured_queue_channel_for_guild(guild_id)

        if channel is None:
            print(f"Queue panel startup skipped for guild {guild_id}: channel not found.")
            continue

        try:
            await send_queue_panel(channel)
        except discord.HTTPException as error:
            print(
                "Queue panel startup failed for "
                f"guild {guild_id}, channel {channel.id}: {error}"
            )


async def retry_startup_queue_panels():
    await asyncio.sleep(STARTUP_QUEUE_PANEL_RETRY_SECONDS)
    await refresh_startup_queue_panels()


class QueuePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Join Queue",
        style=discord.ButtonStyle.success
    )
    async def join_queue(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id if interaction.guild else None

        if queue_locked(guild_id):
            await interaction.followup.send(
                "Queues are currently stopped.",
                ephemeral=True
            )
            return

        if member_has_role_id(interaction.user, sent_home_role_id(guild_id)):
            await interaction.followup.send(
                "You are currently marked as SENT HOME and cannot join the queue.",
                ephemeral=True
            )
            return

        if member_has_role_id(interaction.user, visitor_role_id(guild_id)):
            await interaction.followup.send(
                "Visitors cannot join the queue.",
                ephemeral=True
            )
            return

        required_associate_role_id = associate_role_id(guild_id)

        if required_associate_role_id is None:
            await interaction.followup.send(
                "The Associate role is not configured yet, so queue joining is locked.",
                ephemeral=True
            )
            return

        if not member_has_role_id(interaction.user, required_associate_role_id):
            await interaction.followup.send(
                "You need the Associate role to join the queue.",
                ephemeral=True
            )
            return

        if member_has_in_game_role(interaction.user):
            await interaction.followup.send(
                "You are currently marked as in game and cannot join the queue yet.",
                ephemeral=True
            )
            return

        if is_reserved_for_match(interaction.user.id):
            await interaction.followup.send(
                "You are already in an active match flow.",
                ephemeral=True
            )
            return

        if find_queue_player_by_discord_id(interaction.user.id) is not None:
            await interaction.followup.send(
                "You are already in the queue.",
                ephemeral=True
            )
            return

        try:
            player = find_backend_player_by_discord_id(interaction.user.id)
        except requests.RequestException:
            await interaction.followup.send(
                "Could not load your player profile.",
                ephemeral=True
            )
            return

        if player is None:
            await interaction.followup.send(
                "You are not registered yet. Use `/setupplayer` before joining the queue.",
                ephemeral=True
            )
            return

        try:
            await sync_member_elo_nickname(interaction.user, player)
        except (discord.Forbidden, discord.HTTPException, ValueError):
            pass

        try:
            await sync_member_rank_role(interaction.user, player)
        except (discord.Forbidden, discord.HTTPException, ValueError):
            pass

        async with queue_state.queue_flow_lock:
            if is_reserved_for_match(interaction.user.id):
                await interaction.followup.send(
                    "You are already in an active match flow.",
                    ephemeral=True
                )
                return

            if find_queue_player_by_discord_id(interaction.user.id) is not None:
                await interaction.followup.send(
                    "You are already in the queue.",
                    ephemeral=True
                )
                return

            current_queue.append(player)
            bot.loop.create_task(add_in_queue_role_background(interaction.user))

            queue_state.last_queue_action["type"] = "joined"
            queue_state.last_queue_action["player"] = player["username"]
            queue_state.last_queue_action["discord_id"] = player["discord_id"]
            queue_state.last_queue_action["mmr"] = player["mmr"]
            queue_state.last_queue_action["message"] = None
            mark_queue_activity()

            queue_channel = await refresh_configured_queue_panel(interaction)

            if create_match_if_queue_ready is not None and queue_channel is not None:
                await create_match_if_queue_ready(
                    queue_channel,
                    lock_already_held=True
                )

        await interaction.followup.send(
            "You joined the queue.",
            ephemeral=True
        )

    @discord.ui.button(
        label="Leave Queue",
        style=discord.ButtonStyle.danger
    )
    async def leave_queue(self, interaction, button):
        await interaction.response.defer(ephemeral=True)

        player = find_queue_player_by_discord_id(interaction.user.id)

        if player is None:
            await interaction.followup.send(
                "You are not in the queue.",
                ephemeral=True
            )
            return

        async with queue_state.queue_flow_lock:
            if player not in current_queue:
                await interaction.followup.send(
                    "You are not in the queue.",
                    ephemeral=True
                )
                return

            current_queue.remove(player)
            bot.loop.create_task(remove_in_queue_role_background(interaction.user))

            queue_state.last_queue_action["type"] = "left"
            queue_state.last_queue_action["player"] = player["username"]
            queue_state.last_queue_action["discord_id"] = player["discord_id"]
            queue_state.last_queue_action["mmr"] = player["mmr"]
            queue_state.last_queue_action["message"] = None
            mark_queue_activity()

            await refresh_configured_queue_panel(interaction)

        await interaction.followup.send(
            "You left the queue.",
            ephemeral=True
        )


@bot.event
async def on_ready():
    global queue_inactivity_task, rating_decay_task, startup_queue_panel_retry_task

    await bot.tree.sync()
    queue_state.panel_message_ids = load_panel_message_ids()

    active_configured_guild_ids = [
        guild.id
        for guild in bot.guilds
        if queue_channel_id(guild.id) is not None
    ]

    await refresh_startup_queue_panels()

    if startup_queue_panel_retry_task is None or startup_queue_panel_retry_task.done():
        startup_queue_panel_retry_task = bot.loop.create_task(
            retry_startup_queue_panels()
        )

    if queue_inactivity_task is None or queue_inactivity_task.done():
        queue_inactivity_task = bot.loop.create_task(watch_queue_inactivity())

    if rating_decay_task is None or rating_decay_task.done():
        rating_decay_task = bot.loop.create_task(watch_rating_decay())

    await warm_member_cache(bot, active_configured_guild_ids)

    try:
        players = all_backend_players()
    except requests.RequestException:
        players = []

    for guild_id in active_configured_guild_ids:
        await sync_all_elo_nicknames(
            bot,
            players,
            guild_id=guild_id
        )

        await sync_all_rank_roles(bot, players, guild_id=guild_id)
        await sync_all_ultra_boss_instinct_roles(
            bot,
            players,
            guild_id=guild_id
        )
        await sync_all_losing_streak_roles(
            bot,
            players,
            guild_id=guild_id
        )

    print(f"Logged in as {bot.user}")


async def watch_queue_inactivity():
    await bot.wait_until_ready()

    while not bot.is_closed():
        await asyncio.sleep(QUEUE_INACTIVITY_CHECK_SECONDS)

        if not current_queue:
            continue

        if queue_state.last_queue_activity_at is None:
            mark_queue_activity()
            continue

        inactive_for = discord.utils.utcnow() - queue_state.last_queue_activity_at

        if inactive_for.total_seconds() < QUEUE_INACTIVITY_MINUTES * 60:
            continue

        guild_id = None

        if bot.guilds:
            guild_id = bot.guilds[0].id

        channel = await configured_queue_channel_for_guild(guild_id)

        async with queue_state.queue_flow_lock:
            queued_players = list(current_queue)
            current_queue.clear()
            queue_state.last_queue_activity_at = None
            queue_state.last_queue_action["type"] = "inactive"
            queue_state.last_queue_action["player"] = None
            queue_state.last_queue_action["discord_id"] = None
            queue_state.last_queue_action["mmr"] = None
            queue_state.last_queue_action["message"] = (
                "Emptying queue due to 60 minutes of inactivity\n"
                "Re-enter the queue if you are still looking to play!"
            )

            configured_queue_channel_id = queue_channel_id(guild_id)

            if configured_queue_channel_id is not None and channel is not None:
                await send_queue_panel(channel)

        if channel is not None:
            for player in queued_players:
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
                except (discord.HTTPException, ValueError):
                    pass


async def watch_rating_decay():
    await bot.wait_until_ready()

    while not bot.is_closed():
        await asyncio.sleep(DECAY_CHECK_INTERVAL_SECONDS)

        try:
            decay_result = run_backend_rating_decay()
        except requests.RequestException:
            continue

        for player in decay_result.get("decayed_players", []):
            for guild_id in configured_guild_ids():
                await sync_player_discord_profile(player, guild_id)


async def send_queue_panel(channel):
    async with queue_panel_lock_for(channel):
        guild_key = str(channel.guild.id) if getattr(channel, "guild", None) else "default"
        previous_panel_message_id = queue_state.panel_message_ids.get(guild_key)

        guild_id = channel.guild.id if getattr(channel, "guild", None) else None
        embed = build_queue_embed(current_queue, queue_locked(guild_id))

        panel_message = await channel.send(embed=embed, view=QueuePanelView())
        queue_state.panel_message_ids[guild_key] = panel_message.id
        save_panel_message_ids(queue_state.panel_message_ids)

        if previous_panel_message_id is not None:
            try:
                message = await channel.fetch_message(previous_panel_message_id)
                await message.delete()
            except (discord.NotFound, discord.HTTPException):
                pass

        await cleanup_extra_queue_panels(channel, panel_message.id)


async def cleanup_extra_queue_panels(channel, keep_message_id):
    try:
        async for message in channel.history(
            limit=QUEUE_PANEL_CLEANUP_HISTORY_LIMIT
        ):
            if message.id == keep_message_id:
                continue

            if message.author.id != bot.user.id:
                continue

            if not message.embeds:
                continue

            if message.embeds[0].title != "Round Table Queue":
                continue

            try:
                await message.delete()
            except discord.HTTPException:
                pass
    except discord.HTTPException:
        pass


# Register all slash commands AFTER send_queue_panel exists
create_match_if_queue_ready = register_queue_commands(bot, send_queue_panel)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    guild_id = message.guild.id if message.guild else None

    if message.channel.id != queue_channel_id(guild_id):
        return

    await send_queue_panel(message.channel)

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)
