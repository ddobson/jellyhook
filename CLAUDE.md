# Jellyhook

Receives Jellyfin webhooks over a Flask API, queues them in RabbitMQ, and processes them in an
async worker (Dolby Vision conversion, metadata updates, and similar media tasks).

The API (`api/`) and worker (`workers/`) are independent services, in code and in developer
experience. They must not import from each other.

Each package has its own uv environment: run `uv run` commands from inside `api/` or `workers/`.
Before finishing, run `make all` (lint, format, typecheck, tests).

Delivery is at-least-once, so repeated processing of one message has to be safe.

For worker message handling, retries and the worker settings table, see `docs/development/worker-rules.md`.
For a full walkthrough of how the worker consumes messages, see `docs/guides/message-consumption.md`.
For the API, see `docs/development/api-rules.md`.
For commands, the Compose dev stack and known issues, see `docs/development/setup.md`.
For style conventions, see `docs/development/code-style.md`.
For test layers and when to add which, see `docs/development/testing.md`.
For the user config file, see `docs/guides/configuration.md`.
For deploying with plain Docker, see `docs/guides/docker.md`.
