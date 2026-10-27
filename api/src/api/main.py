import os

from api.app import create_app

_config_name = os.getenv("FLASK_ENV")
if _config_name is None:
    raise RuntimeError("FLASK_ENV must be set to 'development' or 'production'")

app = create_app(_config_name)

if __name__ == "__main__":
    app.run()
