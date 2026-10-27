# JellyHook API

A Flask-based API service that receives webhooks from Jellyfin media server and forwards events to RabbitMQ for asynchronous processing.

## Overview

JellyHook API receives and processes webhook events from Jellyfin (a media server). When media is added to the Jellyfin library, the API captures these events and routes them to a RabbitMQ message queue for asynchronous processing by worker services.

## Architecture

- **Flask Application**: Provides webhook endpoints and application structure
- **RabbitMQ Integration**: Handles message queuing for asynchronous processing
- **Webhook Processing**: Currently supports item_added events from Jellyfin
- **Configuration System**: Environment-specific configuration with environment variables
- **Logging System**: Enhanced logging with loguru

## Components

- **Flask Routes**:
  - `/health`: Health check endpoint
  - `/hooks/item_added`: Processes new items added to Jellyfin

- **RabbitMQ Queues**:
  - `jellyfin:item_added`: Queue for new media items

## Development

### Prerequisites

- Python 3.13+
- RabbitMQ server
- Jellyfin instance (for webhook source)

### Setup

1. Install dependencies:
   ```
   uv sync
   ```

### Environment Variables

These are the names `api/config.py` actually reads. Using any other name (for example
`RABBITMQ_USERNAME`) is silently ignored and the API falls back to the `guest` defaults.

- `FLASK_ENV`: Set to 'development' or 'production'
- `SECRET_KEY`: Flask secret key (production)
- `RABBITMQ_HOST`: RabbitMQ server hostname (default `rabbitmq`)
- `RABBITMQ_USER`: RabbitMQ username (default `guest`)
- `RABBITMQ_PASS`: RabbitMQ password (default `guest`)
- `RABBITMQ_PORT`: RabbitMQ port (default `5672`)
- `RABBITMQ_VHOST`: RabbitMQ virtual host
- `WEBHOOK_TOKEN`: optional shared secret. When set, `/hooks/*` requires
  `Authorization: Bearer <token>`; `/health` stays open. Unset means no authentication, and the
  API logs a warning at startup.

### Responses

| Status | When |
|---|---|
| 202 | The message was confirmed by RabbitMQ and stored persistently |
| 400 | The body is not a JSON object (any Content-Type is accepted if the body parses) |
| 401 | `WEBHOOK_TOKEN` is set and the `Authorization` header is missing or wrong |
| 413 | The body is larger than 1 MiB |
| 503 | RabbitMQ could not be reached, stalled during the handshake, or did not confirm. `Retry-After: 5` is set |
| 500 | Anything unexpected. The message is generic; details are in the log |

Each publish opens its own connection (3 attempts, 1 second apart, 3 second socket and handshake
timeouts). A broker that refuses connections is reported in about 2 seconds; one that silently drops
packets or never answers takes up to about 11 seconds, still well inside the web server's 60 second
timeout. Sustained bursts are fine: 400 events from 8 concurrent senders took 0.7 seconds.

### Securing the webhook

Set `WEBHOOK_TOKEN` on the API, then add a request header in the Jellyfin webhook plugin's generic
destination: name `Authorization`, value `Bearer <the same token>`. Use HTTPS, or keep the API on a
private network, since the token travels in a header.

### Testing

```
uv run pytest
```
