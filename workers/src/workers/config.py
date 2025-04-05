import os

from workers import utils

DEBUG = bool(int(os.getenv("DEBUG", "0")))
MOVIE_PATH = os.getenv("MOVIE_PATH", "/data/media/movies")
STANDUP_PATH = os.getenv("STANDUP_PATH", "/data/media/stand-up")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
TEMP_DIR = os.getenv("TEMP_DIR", "/data/tmp")
WORKER_ENV = os.getenv("WORKER_ENV", "development")

# Jellyfin configuration
# TODO: Add Jellyfin Host
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
JELLYFIN_PORT = int(os.getenv("JELLYFIN_PORT", "8096"))

# Genre configuration
GENRE_CONFIG_PATH = os.getenv("GENRE_CONFIG_PATH", "/config/genre_mappings.json")

# Default minimal genre configuration
DEFAULT_GENRE_CONFIG = {
    "paths": [{"path": STANDUP_PATH, "genres": ["Stand-Up"], "replace_existing": True}],
    "rules": [],
}

GENRE_MAPPINGS = utils.load_config_file(GENRE_CONFIG_PATH, default=DEFAULT_GENRE_CONFIG)
