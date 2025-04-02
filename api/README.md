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

- `FLASK_ENV`: Set to 'development' or 'production'
- `RABBITMQ_HOST`: RabbitMQ server hostname
- `RABBITMQ_PORT`: RabbitMQ server port
- `RABBITMQ_USERNAME`: RabbitMQ username
- `RABBITMQ_PASSWORD`: RabbitMQ password
- `RABBITMQ_VHOST`: RabbitMQ virtual host

### Testing

```
uv run pytest
```
