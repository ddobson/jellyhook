# JellyHook Worker

## Overview

JellyHook Worker is a service that processes media-related tasks triggered by Jellyfin webhook events. It provides an extensible framework for automating various media processing operations when content is added or updated in Jellyfin.

## Key Components

- **Worker Service**: Listens for messages on a RabbitMQ queue, processing Jellyfin webhook events
- **Client Modules**: Handles external integrations like RabbitMQ and Jellyfin API
- **Service Modules**: Specialized services for media processing tasks
- **Config System**: User-configurable webhooks and services
- **Movie Class**: Models media files and their metadata for processing

## Current Capabilities

The worker currently implements:

- **Dolby Vision Conversion**: Automatically converts Dolby Vision videos from profile 7.x to profile 8.x format, improving compatibility with various media players and devices
- **Metadata Updates**: Configurable metadata changes based on file paths or content patterns
- **User Configuration**: Support for custom webhook and service configuration via YAML/JSON

Additional media processing capabilities are planned for future releases.

## Directory Structure

```
workers/
├── src/workers/
│   ├── clients/       # Client implementations (Jellyfin, RabbitMQ)
│   ├── config/        # Configuration handling
│   ├── services/      # Service implementations
│   └── ...
└── tests/
    ├── component/     # Component/integration tests
    └── unit/          # Unit tests
```

## Workflow

1. Jellyfin sends a webhook when media-related events occur
2. The worker service receives the message via RabbitMQ client
3. Service orchestrator loads appropriate services based on user configuration
4. Services execute in priority order to process the media

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
message is redelivered, so keep the container's `stop_grace_period` above that value.

| Variable | Default | Purpose |
|---|---|---|
| `WORKER_MAX_ATTEMPTS` | `5` | Tries (first one included) before a transient failure is dead-lettered |
| `WORKER_MAX_CONCURRENT_JOBS` | `1` | Jobs running at once across all queues. Raise only if the host can take several concurrent ffmpeg/mkvmerge/dovi_tool runs |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | `45` | How long in-flight jobs may finish after `SIGTERM` |
| `RABBITMQ_HEARTBEAT` | `60` | AMQP heartbeat interval in seconds |

For a walkthrough with worked examples, see
[How the Worker Consumes Messages](../docs/message-consumption.md).

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

## Deployment

The application is containerized using Docker. Environment variables control:
- Media paths
- RabbitMQ connection details
- Temporary file storage
- Debug mode
- Environment type

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run component tests
uv run pytest tests/component
