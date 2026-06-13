import discord
from datetime import datetime
from queue_state import last_queue_action


QUEUE_PANEL_WIDTH_PAD = "\u2800" * 40
QUEUE_PANEL_SPACER = "\u200b\n\u200b\n\u200b"
QUEUE_PANEL_FOOTER_SPACER = "\u200b\n\u200b"
MATCH_PANEL_WIDTH_PAD = "\u2800" * 24


def display_match_number(match, backend_match=None):
    backend_match = backend_match or match.get("backend_match") or {}

    return (
        match.get("backend_match_number")
        or backend_match.get("match_number")
        or match.get("backend_match_id")
        or backend_match.get("id")
    )


def format_queue_player(player):
    if player.get("discord_id"):
        return f"**<@{player['discord_id']}>**"

    return f"**{player['username']}**"


def format_match_player(player):
    ign = getattr(player, "ign", "") or player.name

    if getattr(player, "discord_id", None):
        return f"<@{player.discord_id}> ({ign})"

    return f"{player.name} ({ign})"


def format_result_player(player, backend_results):
    result = backend_results.get(player.name)

    if result is not None and result.get("player_discord_id"):
        return f"<@{result['player_discord_id']}>"

    if getattr(player, "discord_id", None):
        return f"<@{player.discord_id}>"

    return player.name


def build_queue_embed(current_queue, queue_locked=False):
    embed = discord.Embed(
        title="Round Table Queue",
        color=discord.Color.gold()
    )

    if queue_locked:
        embed.description = (
            f"{QUEUE_PANEL_WIDTH_PAD}\n"
            "**Queues Have Been Stopped!**\n"
            f"{QUEUE_PANEL_SPACER}\n"
            f"{QUEUE_PANEL_FOOTER_SPACER}\n"
            f"**Last Updated {discord.utils.utcnow().strftime('%I:%M %p')}**"
        )
        return embed

    description_parts = []

    if last_queue_action["type"] == "joined":
        last_player = last_queue_action.get("player")

        if last_queue_action.get("discord_id"):
            last_player = f"<@{last_queue_action['discord_id']}>"

        description_parts.append(
            "**Player Joined Queue!**\n"
            f"**{last_player}**"
        )

    elif last_queue_action["type"] == "left":
        last_player = last_queue_action.get("player")

        if last_queue_action.get("discord_id"):
            last_player = f"<@{last_queue_action['discord_id']}>"

        description_parts.append(
            "**Player Left Queue!**\n"
            f"**{last_player}**"
        )

    elif last_queue_action["type"] == "inactive":
        description_parts.append(
            f"**{last_queue_action['message']}**"
        )

    description_parts.append(QUEUE_PANEL_SPACER)

    if current_queue:
        queue_text = ", ".join(
            format_queue_player(player)
            for player in current_queue
        )
    else:
        queue_text = QUEUE_PANEL_SPACER

    description_parts.append(
        f"**Players In Queue: {len(current_queue)}**\n"
        f"{queue_text}"
    )

    description_parts.append(
        f"{QUEUE_PANEL_FOOTER_SPACER}\n"
        f"**Last Updated {discord.utils.utcnow().strftime('%I:%M %p')}**"
    )

    embed.description = f"{QUEUE_PANEL_WIDTH_PAD}\n" + "\n".join(
        description_parts
    )

    return embed


def format_team(team, assigned_roles):
    lines = []

    for player in team:
        lines.append(
            f"* {format_match_player(player)}"
        )

    return "\n".join(lines)


def result_lookup_for_team(match, team_key):
    backend_match = match.get("backend_match")

    if backend_match is None:
        return {}

    return {
        match_player["player_username"]: match_player
        for match_player in backend_match["match_players"]
        if match_player["team"] == team_key
    }


def format_result_team(team, backend_results):
    lines = []

    for player in team:
        result = backend_results.get(player.name)
        player_name = format_result_player(player, backend_results)

        if result is None:
            lines.append(f"* {player_name}")
            continue

        mmr_change = result.get("mmr_change")
        sign = "+" if mmr_change and mmr_change > 0 else ""

        lines.append(
            f"* {player_name} {sign}{mmr_change:.1f} "
            f"**({result['mmr_after']:.1f})**"
        )

    return "\n".join(lines)


def build_match_embed(match):
    match_number = display_match_number(match)
    title = "⚔️ Queue"

    if match_number is not None:
        title = f"⚔️ Queue#{match_number}"

    embed = discord.Embed(
        title=title,
        color=discord.Color.green()
    )

    if match["map"].get("image_url"):
        embed.set_image(url=match["map"]["image_url"])

    team_1_avg = sum(p.elo for p in match["team_1"]) / 5
    team_2_avg = sum(p.elo for p in match["team_2"]) / 5

    embed.add_field(
        name=f"Team 1 - {team_1_avg:.1f} avg",
        value=format_team(match["team_1"], match.get("team_1_roles", {})),
        inline=True
    )

    embed.add_field(
        name=f"Team 2 - {team_2_avg:.1f} avg",
        value=format_team(match["team_2"], match.get("team_2_roles", {})),
        inline=True
    )

    embed.add_field(
        name="Match Details",
        value=f"Map: {match['map']['name']}\n{MATCH_PANEL_WIDTH_PAD}",
        inline=False
    )

    return embed


def admin_match_player_line(match_player, match_cancelled=False):
    discord_id = match_player.get("player_discord_id")
    name = match_player.get("player_username", "Unknown")
    player_name = f"<@{discord_id}>" if discord_id else name
    mmr_before = match_player.get("mmr_before")
    mmr_after = match_player.get("mmr_after")
    mmr_change = match_player.get("mmr_change") or 0

    if match_cancelled:
        return f"{player_name} ({mmr_before:.1f}) -> **+0.0** -> {mmr_before:.1f}"

    if mmr_after is None:
        return f"{player_name} ({mmr_before:.1f}) -> pending"

    sign = "+" if mmr_change > 0 else ""
    return (
        f"{player_name} ({mmr_before:.1f}) -> "
        f"**{sign}{mmr_change:.1f}** -> {mmr_after:.1f}"
    )


def admin_match_team_lines(backend_match, team_key):
    match_cancelled = backend_match.get("status") == "cancelled"
    lines = [
        admin_match_player_line(match_player, match_cancelled)
        for match_player in backend_match.get("match_players", [])
        if match_player.get("team") == team_key
    ]

    return "\n".join(lines) or "No players found."


def format_backend_timestamp(timestamp):
    if not timestamp:
        return "Unknown"

    try:
        parsed_timestamp = datetime.fromisoformat(
            timestamp.replace("Z", "+00:00")
        ).astimezone()
    except ValueError:
        return timestamp

    hour = parsed_timestamp.strftime("%I").lstrip("0") or "0"
    return (
        f"{parsed_timestamp.strftime('%B')} {parsed_timestamp.day}, "
        f"{parsed_timestamp.year} {hour}:{parsed_timestamp.strftime('%M')} "
        f"{parsed_timestamp.strftime('%p')}"
    )


def build_admin_match_embed(match, backend_match=None):
    backend_match = backend_match or match.get("backend_match") or {}
    match_number = display_match_number(match, backend_match)
    status_text = backend_match.get("status", "pending")
    winner_text = backend_match.get("winner") or "None"
    map_name = match.get("map", {}).get("name") or backend_match.get("map_name", "")
    created_at = format_backend_timestamp(backend_match.get("created_at"))

    embed = discord.Embed(
        title=f"Results for Queue#{match_number}",
        color=discord.Color.red()
    )

    embed.add_field(
        name="Match Info",
        value=(
            f"Queue: `{status_text}`\n"
            f"Winner: `{winner_text}`\n"
            f"Map: {map_name or 'Unknown'}\n"
            "Lobby Details:\n"
            f"Timestamp: {created_at}"
        ),
        inline=False
    )

    embed.add_field(
        name="Team 1",
        value=admin_match_team_lines(backend_match, "team_1"),
        inline=False
    )

    embed.add_field(
        name="Team 2",
        value=admin_match_team_lines(backend_match, "team_2"),
        inline=False
    )

    embed.set_footer(
        text="Use the buttons below to cancel or force the winner."
    )

    return embed


def build_ready_check_embed(players, ready_count, deadline, ready_discord_ids=None):
    ready_discord_ids = ready_discord_ids or set()
    not_ready_players = [
        player
        for player in players
        if (
            player.get("discord_id") is not None
            and int(player["discord_id"]) not in ready_discord_ids
        )
    ]
    not_ready_mentions = [
        f"<@{player['discord_id']}>"
        for player in not_ready_players
    ]
    not_ready_text = "Everyone has readied up."

    if not_ready_mentions:
        not_ready_text = (
            f"{', '.join(not_ready_mentions)} is not ready."
        )

    embed = discord.Embed(
        title="Round Table Queue",
        color=discord.Color.red()
    )

    embed.description = (
        f"**Ready Players: {ready_count}/{len(players)}**\n\n"
        f"{not_ready_text}\n\n"
        "\u200b\n\u200b\n"
        "Inactive players will be returned to queue "
        f"{discord.utils.format_dt(deadline, style='R')}."
    )

    return embed


def build_map_vote_embed(
    visible_maps,
    vote_counts,
    deadline,
    preview_image_url=None,
    closed=False
):
    embed = discord.Embed(
        title="Map Select",
        color=discord.Color.gold()
    )

    lines = []

    for index, map_choice in enumerate(visible_maps, start=1):
        lines.append(
            f"**{index}. {map_choice['name']}** - "
            f"{vote_counts.get(map_choice['key'], 0)} vote(s)"
        )

    lines.append(
        f"**{len(visible_maps) + 1}. ? Random** - "
        f"{vote_counts.get('random', 0)} vote(s)"
    )

    footer_text = "Voting ended."

    if not closed:
        footer_text = (
            "Voting ends "
            + f"{discord.utils.format_dt(deadline, style='R')}."
        )

    embed.description = "\n".join(lines) + "\n\n" + footer_text

    if preview_image_url:
        embed.set_image(url=preview_image_url)

    return embed


def build_match_result_embed(match, winner):
    match_number = display_match_number(match)
    title = f"Winner For Queue - {winner}"

    if match_number is not None:
        title = f"Winner For Queue #{match_number} - {winner}"

    embed = discord.Embed(
        title=title,
        color=discord.Color.red()
    )

    embed.add_field(
        name="Team 1 🏆" if winner == "Team 1" else "Team 1",
        value=format_result_team(
            match["team_1"],
            result_lookup_for_team(match, "team_1")
        ),
        inline=True
    )

    embed.add_field(
        name="Team 2 🏆" if winner == "Team 2" else "Team 2",
        value=format_result_team(
            match["team_2"],
            result_lookup_for_team(match, "team_2")
        ),
        inline=True
    )

    embed.add_field(
        name="Map",
        value=match["map"]["name"],
        inline=False
    )

    embed.add_field(
        name="Result",
        value=f"{winner} won the match.",
        inline=False
    )

    embed.add_field(
        name="Completed",
        value=discord.utils.format_dt(
            discord.utils.utcnow(),
            style="f"
        ),
        inline=False
    )

    return embed
