import json
from pathlib import Path

from config import (
    ASSOCIATE_ROLE_ID,
    QUEUE_CHANNEL_ID,
    RANK_ROLE_IDS,
    SENT_HOME_ROLE_ID,
    VISITOR_ROLE_ID,
    LOSING_STREAK_ROLE_ID,
    ULTRA_BOSS_INSTINCT_ROLE_ID,
)


GUILD_CONFIG_FILE = Path(__file__).with_name("guild_config.json")

DEFAULT_BOT_REPORT_CHANNEL_ID = 1512447506766626816
DEFAULT_MATCH_RESULTS_CHANNEL_ID = 1512236684090146828


def load_guild_configs():
    if not GUILD_CONFIG_FILE.exists():
        return {}

    try:
        return json.loads(GUILD_CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_guild_configs(configs):
    GUILD_CONFIG_FILE.write_text(
        json.dumps(configs, indent=2, sort_keys=True),
        encoding="utf-8"
    )


def guild_key(guild_id):
    return str(guild_id)


def get_guild_config(guild_id):
    if guild_id is None:
        return {}

    return load_guild_configs().get(guild_key(guild_id), {})


def upsert_guild_config(guild_id, data):
    configs = load_guild_configs()
    key = guild_key(guild_id)
    current = configs.get(key, {})
    current.update(data)
    configs[key] = current
    save_guild_configs(configs)
    return current


def configured_guild_ids():
    return [
        int(guild_id)
        for guild_id in load_guild_configs().keys()
    ]


def configured_value(guild_id, name, fallback=None):
    return get_guild_config(guild_id).get(name, fallback)


def queue_channel_id(guild_id):
    return configured_value(guild_id, "queue_channel_id", QUEUE_CHANNEL_ID)


def match_results_channel_id(guild_id):
    return configured_value(
        guild_id,
        "match_results_channel_id",
        DEFAULT_MATCH_RESULTS_CHANNEL_ID
    )


def bot_report_channel_id(guild_id):
    return configured_value(
        guild_id,
        "bot_report_channel_id",
        DEFAULT_BOT_REPORT_CHANNEL_ID
    )


def admin_match_panel_channel_id(guild_id):
    return configured_value(
        guild_id,
        "admin_match_panel_channel_id",
        bot_report_channel_id(guild_id)
    )


def associate_role_id(guild_id):
    return configured_value(guild_id, "associate_role_id", ASSOCIATE_ROLE_ID)


def sent_home_role_id(guild_id):
    return configured_value(guild_id, "sent_home_role_id", SENT_HOME_ROLE_ID)


def visitor_role_id(guild_id):
    return configured_value(guild_id, "visitor_role_id", VISITOR_ROLE_ID)


def ultra_boss_instinct_role_id(guild_id):
    return configured_value(
        guild_id,
        "ultra_boss_instinct_role_id",
        ULTRA_BOSS_INSTINCT_ROLE_ID
    )


def losing_streak_role_id(guild_id):
    return configured_value(
        guild_id,
        "losing_streak_role_id",
        LOSING_STREAK_ROLE_ID
    )


def in_game_role_id(guild_id):
    return configured_value(guild_id, "in_game_role_id")


def in_queue_role_id(guild_id):
    return configured_value(guild_id, "in_queue_role_id")


def elo_nickname_enabled(guild_id, fallback=False):
    return bool(configured_value(guild_id, "elo_nickname_enabled", fallback))


def rank_role_sync_enabled(guild_id, fallback=False):
    return bool(configured_value(guild_id, "rank_role_sync_enabled", fallback))


def queue_locked(guild_id, fallback=False):
    return bool(configured_value(guild_id, "queue_locked", fallback))


def rank_role_ids(guild_id):
    configured = get_guild_config(guild_id).get("rank_role_ids", {})

    return {
        rank: configured.get(rank, fallback_role_id)
        for rank, fallback_role_id in RANK_ROLE_IDS.items()
    }
