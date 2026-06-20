import asyncio


last_queue_action = {
    "type": None,
    "player": None,
    "discord_id": None,
    "mmr": None,
    "message": None,
}
current_queue = []
last_panel_message_id = None
panel_message_ids = {}
match_result_pending = False
last_queue_activity_at = None
queue_flow_lock = asyncio.Lock()
active_match_discord_ids = set()
in_queue_role_locks = {}


def in_queue_role_lock(guild_id, member_id):
    key = (int(guild_id), int(member_id))

    if key not in in_queue_role_locks:
        in_queue_role_locks[key] = asyncio.Lock()

    return in_queue_role_locks[key]
