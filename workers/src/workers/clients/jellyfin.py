from jellyfin_apiclient_python import JellyfinClient

from workers import config

client = JellyfinClient()
client.config.app(
    config.APP_NAME, config.APP_VERSION, config.APP_DEVICE_NAME, config.APP_DEVICE_ID
)
client.config.data["auth.ssl"] = True
client.authenticate(
    {
        "Servers": [
            {
                "AccessToken": config.JELLYFIN_API_KEY,
                "address": f"{config.JELLYFIN_HOST}:{config.JELLYFIN_PORT}",
                "DateLastAccessed": 0,
                "UserId": config.JELLYFIN_USER_ID,
            }
        ]
    },
    discover=True,
)
