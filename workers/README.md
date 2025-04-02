# JellyHook Worker

## Overview

JellyHook Worker is a service that processes media-related tasks triggered by Jellyfin webhook events. It provides an extensible framework for automating various media processing operations when content is added or updated in Jellyfin.

## Key Components

- **Worker Service**: Listens for messages on a RabbitMQ queue, processing Jellyfin webhook events
- **Hook System**: Extensible handlers for different Jellyfin event types
- **Service Modules**: Specialized services for media processing tasks
- **Movie Class**: Models media files and their metadata for processing

## Current Capabilities

The worker currently implements:

- **Dolby Vision Conversion**: Automatically converts Dolby Vision videos from profile 7.x to profile 8.x format, improving compatibility with various media players and devices

Additional media processing capabilities are planned for future releases.

## Workflow

1. Jellyfin sends a webhook when media-related events occur
2. The worker service receives the message via RabbitMQ
3. Appropriate hook handlers process the event based on type
4. Service modules perform specialized media processing tasks as needed

## Technology Stack

- Python 3.13
- RabbitMQ
- FFmpeg
- MKVToolNix
- Dovi Tool
- Docker
- Pytest

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
