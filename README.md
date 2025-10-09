# Jellyhook: Jellyfin Webhook Processor

A microservice architecture for processing Jellyfin media server events via webhooks. The system receives events from a Jellyfin server, queues them through RabbitMQ, and processes them asynchronously, with special handling for media processing tasks such as Dolby Vision conversion.

## Features

- **Webhook Reception**: Secure endpoint for receiving Jellyfin webhook events
- **Asynchronous Processing**: Decoupled architecture using RabbitMQ for reliable message queuing
- **Media Processing**: Specialized handlers for different Jellyfin events
- **Dolby Vision Conversion**: Automatic conversion from Dolby Vision Profile 7.x to 8.x format
- **User Configuration**: Support for custom webhook and service configuration via YAML/JSON
- **Prioritized Services**: Execute services in configurable priority order 
- **Metadata Management**: Configurable metadata updates based on file paths or content patterns
- **Multi-platform Support**: Docker images for both ARM64 and AMD64 architectures

## Overview

Jellyhook consists of two main components:

- **API Service**: A Flask application that receives webhooks from Jellyfin and routes them to RabbitMQ
- **Worker Service**: Processes queued messages asynchronously for various media-related tasks

## Project Structure

```
jellyhook/
├── api/                  # API service
│   ├── src/api/          # API source code
│   │   ├── hooks/        # Webhook handlers
│   │   └── ...          
│   └── tests/            # API tests
├── workers/              # Worker service
│   ├── src/workers/      # Worker source code
│   │   ├── clients/      # Client implementations (Jellyfin, RabbitMQ)
│   │   ├── config/       # Configuration handling
│   │   ├── services/     # Service implementations
│   │   └── ...
│   └── tests/            # Worker tests
│       ├── component/    # Component/integration tests
│       └── unit/         # Unit tests
└── docs/examples/        # Example configuration files
    ├── jellyhook.example.json    # Example webhook configuration
    └── jellyhook.example.yaml    # Example webhook configuration
```

## Development

### Prerequisites

- Python 3.12+
- uv (Python package manager)
- Docker and Docker Compose (for containerized development)
- RabbitMQ (included in Docker Compose setup)
- Media processing tools: ffmpeg, mkvextract, mkvmerge, dovi_tool

### Setup

1. Clone the repository
2. Navigate to the project directory
3. Install dependencies:
   ```
   cd api && uv pip install -e .
   cd workers && uv pip install -e .
   ```

### Development Commands

The project uses a Makefile for common development tasks:

```bash
# Linting
make lint

# Code formatting
make format

# Type checking
make typecheck  # Project is not yet type compliant

# Run tests
make test

# Run all checks
make all
```

### Running Locally

Using Docker Compose (recommended):
```bash
docker-compose up -d
```

Or run services individually:


Run workers:
```bash
cd workers
uv run python -m workers.main
```

## Deployment

### Building and Pushing Docker Images

The project includes Makefile commands for building and pushing multi-architecture Docker images:

```bash
# Build API image
make build_api TAG=v1.0.0

# Build Worker image
make build_worker TAG=v1.0.0

# Tag images for GitHub Container Registry
make tag_api ACCOUNT=your-github-username TAG=v1.0.0
make tag_worker ACCOUNT=your-github-username TAG=v1.0.0

# Push images
make push_api ACCOUNT=your-github-username TAG=v1.0.0
make push_worker ACCOUNT=your-github-username TAG=v1.0.0

# Or do everything at once
make push_all ACCOUNT=your-github-username TAG=v1.0.0
```

### Configuration

Configure the application using environment variables and configuration files:

#### API Service
- `FLASK_ENV`: Set to `development` or `production`
- `LOG_LEVEL`: Logging level (default: "INFO" in production, "DEBUG" in development)
- `RABBITMQ_HOST`: RabbitMQ hostname (default: "rabbitmq")
- `RABBITMQ_PASS`: RabbitMQ password (default: "guest")
- `RABBITMQ_USER`: RabbitMQ username (default: "guest")
- `RABBITMQ_VHOST`: RabbitMQ virtual host (default: "/")
- `SECRET_KEY`: Secret key for Flask (required in production)

#### Worker Service
- `DEBUG`: Enable debug logging (1 for enabled, 0 for disabled, default: 0)
- `MOVIE_PATH`: Path to movie files on your system (default: "/data/media/movies")
- `RABBITMQ_HOST`: RabbitMQ hostname (default: "rabbitmq")
- `RABBITMQ_PASS`: RabbitMQ password (default: "guest")
- `RABBITMQ_USER`: RabbitMQ username (default: "guest")
- `STANDUP_PATH`: Path to stand-up comedy files (default: "/data/media/stand-up")
- `TEMP_DIR`: Directory for temporary files (default: "/data/tmp")
- `WORKER_ENV`: Worker environment (default: "development")

#### User Configuration File

The worker service can be configured using a YAML or JSON file:

```yaml
worker:
  webhooks:
    item_added:  # Webhook type
      enabled: true  # Enable/disable this webhook
      queue: jellyfin:item_added  # RabbitMQ queue name
      services:  # List of services to execute for this webhook
        - name: metadata_update  # Service name
          enabled: true  # Enable/disable this service
          priority: 10  # Lower numbers run first
          config:  # Service-specific configuration
            # Configuration options for metadata updates
        
        - name: dovi_conversion
          enabled: true
          priority: 20
          config:
            # Configuration options for Dolby Vision conversion

        - name: playlist_assignment
          enabled: true
          priority: 30
          config:
            rules:
              - playlist_id: 1234567890abcdef1234567890abcdef
                playlist_name: Under 2 Hours
                conditions:
                  item_types:
                    - Movie
                  max_runtime_minutes: 120
```

Place this file at `~/.config/jellyhook/jellyhook.yaml` or specify a custom path with the `JELLYHOOK_CONFIG_PATH` environment variable.

## Code Standards

- **Python Version**: 3.13+
- **Linting**: Ruff for linting and formatting
- **Type Checking**: MyPy for static type checking
- **Testing**: pytest for testing

### Coding Guidelines

- Use type annotations for all function parameters and return values
- Follow PEP 8 naming conventions
- Write meaningful docstrings for public functions and classes
- Handle errors appropriately with specific exception catching

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the project
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
