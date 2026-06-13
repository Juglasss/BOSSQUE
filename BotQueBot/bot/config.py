import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DJANGO_API_URL = os.getenv("DJANGO_API_URL")
NEATQUEUE_API_URL = os.getenv("NEATQUEUE_API_URL")
NEATQUEUE_PLAYERS_URL = os.getenv("NEATQUEUE_PLAYERS_URL")
NEATQUEUE_AUTHORIZATION = (
    os.getenv("NEATQUEUE_AUTHORIZATION")
    or os.getenv("NEATQUEUE_TOKEN")
    or os.getenv("Authorization")
)

queue_channel_id = os.getenv("QUEUE_CHANNEL_ID")
QUEUE_CHANNEL_ID = int(queue_channel_id) if queue_channel_id else None


def optional_int_env(name):
    value = os.getenv(name)
    return int(value) if value else None


ASSOCIATE_ROLE_ID = optional_int_env("ASSOCIATE_ROLE_ID")
SENT_HOME_ROLE_ID = optional_int_env("SENT_HOME_ROLE_ID")
VISITOR_ROLE_ID = optional_int_env("VISITOR_ROLE_ID")
ULTRA_BOSS_INSTINCT_ROLE_ID = (
    optional_int_env("ULTRA_BOSS_INSTINCT_ROLE_ID")
    or 1512569254967251116
)
LOSING_STREAK_ROLE_ID = optional_int_env("LOSING_STREAK_ROLE_ID")

RANK_ROLE_IDS = {
    "mustard_gas": optional_int_env("RANK_ROLE_MUSTARD_GAS_ID"),
    "woodhuman": optional_int_env("RANK_ROLE_WOODHUMAN_ID"),
    "goodmaster": optional_int_env("RANK_ROLE_GOODMASTER_ID"),
    "greatmaster": optional_int_env("RANK_ROLE_GREATMASTER_ID"),
    "grandmaster": optional_int_env("RANK_ROLE_GRANDMASTER_ID"),
    "super_grandmaster": optional_int_env("RANK_ROLE_SUPER_GRANDMASTER_ID"),
    "super_grandmaster_god": optional_int_env(
        "RANK_ROLE_SUPER_GRANDMASTER_GOD_ID"
    ),
}
