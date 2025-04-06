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

# Metadata rules
DEFAULT_METADATA_RULES = {"paths": [], "rules": []}
METADATA_CONFIG_PATH = os.getenv("METADATA_CONFIG_PATH", "/config/genre_mappings.json")
METADATA_RULES = utils.load_config_file(METADATA_CONFIG_PATH, default=DEFAULT_METADATA_RULES)
