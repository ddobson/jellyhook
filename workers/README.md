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
