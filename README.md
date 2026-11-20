# Jellyhook: Jellyfin Webhook Processor

A microservice architecture for processing Jellyfin media server events via webhooks. The system receives events from a Jellyfin server, queues them through RabbitMQ, and processes them asynchronously, with special handling for media processing tasks such as Dolby Vision conversion.

## Features

- **Webhook Reception**: Secure endpoint for receiving Jellyfin webhook events
- **Asynchronous Processing**: Decoupled architecture using RabbitMQ for reliable message queuing
- **Media Processing**: Specialized handlers for different Jellyfin events
- **Dolby Vision Conversion**: Automatic conversion from Dolby Vision Profile 7.x to 8.x format
- **Track Cleaning**: Removes unwanted audio and subtitle tracks from media files
- **Playlist Assignment**: Adds items to Jellyfin playlists based on rules (runtime, genre, tags, year)
- **User Configuration**: Support for custom webhook and service configuration via YAML/JSON
- **Prioritized Services**: Execute services in configurable priority order
- **Reliable Delivery**: At-least-once delivery with retries and a dead-letter queue
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
│   └── tests/            # API tests (unit, plus integration/ against a real RabbitMQ)
├── workers/              # Worker service
│   ├── src/workers/      # Worker source code
│   │   ├── clients/      # Client implementations (Jellyfin, RabbitMQ, retry policy)
│   │   ├── config/       # Configuration handling
│   │   ├── models/       # Item and Movie models
│   │   ├── parsers/      # Movie filename parsers
│   │   ├── services/     # Service implementations
│   │   └── ...
│   └── tests/            # Worker tests
│       ├── unit/         # Unit tests
│       ├── component/    # Tests that run the real media tools
│       └── integration/  # Tests against a real RabbitMQ (need Docker)
├── docs/                 # Documentation
│   ├── development/      # Rules, setup, code style and testing for contributors
│   ├── guides/           # Configuration, Docker and message-consumption guides
│   └── examples/         # Example configuration files and scripts
├── docker-compose.yaml   # Development stack
└── rabbitmq.conf         # Broker settings (raises consumer_timeout)
```

## Development

### Prerequisites

- Python 3.13+
- uv (Python package manager)
- Docker and Docker Compose (for containerized development)
- RabbitMQ (included in Docker Compose setup)
- Media processing tools: ffmpeg, mkvextract, mkvmerge, dovi_tool

### Setup

1. Clone the repository
2. Navigate to the project directory
3. Install dependencies (each package has its own uv environment):
   ```
   cd api && uv sync
   cd workers && uv sync
   ```

### Development Commands

The project uses a Makefile for common development tasks:

```bash
# Linting
make lint

# Code formatting
make format

# Type checking
make typecheck  # mypy on api and workers; fails on any error

# Run tests (unit, plus component tests where the media tools are installed)
make test

# Run integration tests against a real RabbitMQ (needs Docker)
make test_integration

# Run all checks
make all
```

### Running Locally

Using Docker Compose (recommended):
```bash
docker-compose up -d
```

Or run services individually. Both need a reachable RabbitMQ.

Run the API (`FLASK_ENV` is required):
```bash
cd api
FLASK_ENV=development uv run flask --app api.main run --port 5001
```

Run the worker:
```bash
cd workers
uv run python -m workers.main
```

The compose file is a development stack, not a deployment template. See
[Development](docs/development/setup.md) for details.

## Deployment

To run the published images with plain `docker` (no Compose), see
[Running Jellyhook with Plain Docker](docs/guides/docker.md) and the ready-to-edit
[`docs/examples/docker-run.example.sh`](docs/examples/docker-run.example.sh).

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
- `FLASK_ENV`: Set to `development` or `production` (required; the app will not start without it)
- `RABBITMQ_HOST`: RabbitMQ hostname (default: "rabbitmq")
- `RABBITMQ_PASS`: RabbitMQ password (default: "guest")
- `RABBITMQ_PORT`: RabbitMQ port (default: 5672)
- `RABBITMQ_USER`: RabbitMQ username (default: "guest")
- `RABBITMQ_VHOST`: RabbitMQ virtual host (default: "/"). Must match the worker's `RABBITMQ_VHOST`
- `SECRET_KEY`: Secret key for Flask (required in production)
- `WEBHOOK_TOKEN`: Optional shared secret. When set, `/hooks/*` requires
  `Authorization: Bearer <token>`. When unset, anyone who can reach the API can queue events

#### Worker Service
- `DEBUG`: Enable debug logging (1 for enabled, 0 for disabled, default: 0)
- `JELLYFIN_HOST`: Jellyfin URL including the scheme, e.g. `http://jellyfin.lan` (required by the
  services that call Jellyfin)
- `JELLYFIN_PORT`: Jellyfin port (default: 8096)
- `JELLYFIN_API_KEY`: Jellyfin API key
- `JELLYFIN_USER_ID`: Jellyfin user ID
- `JELLYHOOK_CONFIG_PATH`: Path to the user configuration file (default: "/config/jellyhook.json")
- `MOVIE_PATH`: Path to movie files on your system (default: "/data/media/movies")
- `RABBITMQ_HOST`: RabbitMQ hostname (default: "rabbitmq")
- `RABBITMQ_PASS`: RabbitMQ password (default: "guest")
- `RABBITMQ_PORT`: RabbitMQ port (default: 5672)
- `RABBITMQ_USER`: RabbitMQ username (default: "guest")
- `RABBITMQ_VHOST`: RabbitMQ virtual host (default: "/"). Must match the API's `RABBITMQ_VHOST`
- `STANDUP_PATH`: Path to stand-up comedy files (default: "/data/media/stand-up")
- `TEMP_DIR`: Directory for temporary files (default: "/data/tmp")
- `WORKER_ENV`: Worker environment (default: "development"). Set it to `production` to log at
  INFO and write `/config/logs/worker.log`

Retry, concurrency and shutdown settings (`WORKER_MAX_ATTEMPTS`, `WORKER_MAX_CONCURRENT_JOBS`,
`WORKER_SHUTDOWN_GRACE_SECONDS`, `RABBITMQ_HEARTBEAT`) are described in the
[worker README](workers/README.md#message-handling).

#### User Configuration File

The worker service is configured with a YAML or JSON file (see
[User Configuration](docs/guides/configuration.md)):

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

The worker reads `/config/jellyhook.json` by default. Point `JELLYHOOK_CONFIG_PATH` at another
path to use a different location or a `.yaml` file. Full examples are in
[`docs/examples/`](docs/examples/).

## Code Standards

- **Python Version**: 3.13+
- **Linting**: Ruff for linting and formatting
- **Type Checking**: MyPy for static type checking
- **Testing**: pytest for testing

See [Code Style](docs/development/code-style.md) for the conventions and
[Testing](docs/development/testing.md) for the test layers.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the project
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
