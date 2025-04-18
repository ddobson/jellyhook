import os

DEBUG = bool(int(os.getenv("DEBUG", "0")))
MOVIE_PATH = os.getenv("MOVIE_PATH", "/data/media/movies")
STANDUP_PATH = os.getenv("STANDUP_PATH", "/data/media/stand-up")
MEDIA_PATHS = (MOVIE_PATH, STANDUP_PATH)
JELLYHOOK_CONFIG_PATH = os.getenv("JELLYHOOK_CONFIG_PATH", "/config/jellyhook.json")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")
TEMP_DIR = os.getenv("TEMP_DIR", "/data/tmp")
WORKER_ENV = os.getenv("WORKER_ENV", "development")

# Jellyfin configuration
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
JELLYFIN_HOST = os.getenv("JELLYFIN_HOST")
JELLYFIN_PORT = int(os.getenv("JELLYFIN_PORT", "8096"))

# Used by the Jellyfin API to identify the client
APP_DEVICE_ID = os.getenv("APP_DEVICE_ID", "jellyhook-worker")
APP_DEVICE_NAME = os.getenv("APP_DEVICE", "JellyhookWorkerServer")
APP_NAME = "jellyhook_api"
APP_VERSION = os.getenv("APP_VERSION", "0.0.1")
JELLYFIN_USER_ID = os.getenv("JELLYFIN_USER_ID", "")
