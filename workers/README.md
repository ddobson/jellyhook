# JellyHook Worker

## Overview

JellyHook Worker is a service that processes media-related tasks triggered by Jellyfin webhook events. It provides an extensible framework for automating various media processing operations when content is added or updated in Jellyfin.

## Key Components

- **Worker Service**: Listens for messages on a RabbitMQ queue, processing Jellyfin webhook events
- **Client Modules**: Handles external integrations like RabbitMQ and Jellyfin API
- **Service Modules**: Specialized services for media processing tasks
- **Config System**: User-configurable webhooks and services
- **Job**: Built once per message. Owns the item, the media file, scratch space and the file lock
- **Item and Movie Models**: `Item` parses the webhook payload; `Movie` models the media file and
  its parsed filename

## Current Capabilities

The worker currently implements:

- **Dolby Vision Conversion**: Automatically converts Dolby Vision videos from profile 7.x to profile 8.x format, improving compatibility with various media players and devices
- **Track Cleaning** (`media_track_clean`): Removes audio and subtitle tracks that do not match the
  configured languages or original/default flags
- **Metadata Updates**: Configurable metadata changes based on file paths or content patterns
- **Playlist Assignment** (`playlist_assignment`): Adds items to Jellyfin playlists when rule
  conditions (item type, runtime, release year, genres, tags) match
- **User Configuration**: Support for custom webhook and service configuration via YAML/JSON

## Directory Structure

```
workers/
├── src/workers/
│   ├── clients/       # Client implementations (Jellyfin, RabbitMQ)
│   ├── config/        # Configuration handling
│   ├── models/        # Item and Movie models
│   ├── parsers/       # Movie filename parsers
│   ├── services/      # Service implementations and the orchestrator
│   ├── job.py         # Per-message Job
│   └── ...
└── tests/
    ├── unit/          # Unit tests
    ├── component/     # Tests that run the real media tools
    └── integration/   # Tests against a real RabbitMQ (need Docker)
```

## Workflow

1. Jellyfin sends a webhook when media-related events occur
2. The worker service receives the message via RabbitMQ client
3. Service orchestrator builds a `Job` for the message and loads the enabled services for that
   webhook from the user configuration
4. Services execute in priority order to process the media. Services that work on the movie file
   (`metadata_update`, `dovi_conversion`, `media_track_clean`) are skipped for items that are not
   movies

## Message Handling

Each enabled webhook's queue is consumed on its own RabbitMQ connection. A message is only
acknowledged once it has been handled, so a crash or lost connection means the broker redelivers
it (at-least-once delivery).

When processing fails, the worker decides what to do based on the kind of failure:

| Failure | Examples | What happens |
|---|---|---|
| Transient | Video not on disk yet, Jellyfin unreachable or timing out | Parked on `<queue>.retry`, returns to `<queue>` after 60 seconds, up to `WORKER_MAX_ATTEMPTS` tries |
| Permanent | Unknown or unloadable service, invalid message body, any other error | Moved straight to `<queue>.dead` |
| Retries exhausted | A transient failure that keeps happening | Moved to `<queue>.dead` |

Nothing is discarded. Messages on `<queue>.dead` carry `x-jellyhook-failure-reason` and
`x-jellyhook-attempts` headers; inspect them in the RabbitMQ management UI (port 15672). To replay
one, move it back onto `<queue>`: enable the `rabbitmq_shovel` and `rabbitmq_shovel_management`
plugins, which add a "Move messages" tab to each queue's page in the management UI. Because
redelivery can run a job twice, services should be safe to repeat.

If the RabbitMQ connection drops or a consumer fails, the worker exits non-zero instead of
carrying on with nothing consuming. Run it under a restart policy (`restart: unless-stopped` in
`docker-compose.yaml`) so it comes back with a fresh connection.

On `SIGTERM` (`docker stop`) the worker stops taking new messages and gives in-flight jobs
`WORKER_SHUTDOWN_GRACE_SECONDS` to finish. A job still running after that is abandoned and its
message is redelivered. Set the container's `stop_grace_period` to at least that value plus 15
seconds (Docker's default of 10 would kill the worker mid-drain).

| Variable | Default | Purpose |
|---|---|---|
| `WORKER_MAX_ATTEMPTS` | `5` | Tries (first one included) before a transient failure is dead-lettered |
| `WORKER_MAX_CONCURRENT_JOBS` | `1` | Jobs running at once across all queues. Earlier versions ran one per webhook, so with several webhooks raise this to match. Only do so if the host can take several concurrent ffmpeg/mkvmerge/dovi_tool runs |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | `45` | How long in-flight jobs may finish after `SIGTERM` |
| `RABBITMQ_HEARTBEAT` | `60` | AMQP heartbeat interval in seconds |

For a walkthrough with worked examples, and a checklist for deploying outside Docker Compose
(restart policy, stop timeout, broker `consumer_timeout`, restricted-user permissions), see
[How the Worker Consumes Messages](../docs/guides/message-consumption.md).

The retry delay (60 seconds) is part of the `<queue>.retry` declaration. To change it, delete
that queue first or RabbitMQ rejects the redeclare.

## Technology Stack

- Python 3.13+
- RabbitMQ
- FFmpeg
- MKVToolNix
- Dovi Tool
- Docker
- Pytest

## Configuration

Users can configure webhooks and services using a YAML or JSON file:

```yaml
worker:
  webhooks:
    item_added:
      enabled: true
      queue: jellyfin:item_added
      services:
        - name: metadata_update
          enabled: true
          priority: 10
          config:
            # Service-specific configuration
        - name: dovi_conversion
          enabled: true
          priority: 20
          config:
            # Service-specific configuration
```

The worker reads `/config/jellyhook.json` unless `JELLYHOOK_CONFIG_PATH` says otherwise. See
[User Configuration](../docs/guides/configuration.md) and [`docs/examples/`](../docs/examples/).

## Deployment

The application is containerized using Docker. Environment variables control:
- Media paths
- RabbitMQ connection details
- Temporary file storage (`TEMP_DIR`, or `temp_dir` per service). Put it on the same disk as the media if you can: a cross-disk swap has to copy the whole file onto the media disk first.
- Debug mode
- Environment type

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run component tests (need ffmpeg, mkvtoolnix and dovi_tool)
uv run pytest tests/component

# Run integration tests (need Docker)
uv run pytest -m integration tests/integration
```
