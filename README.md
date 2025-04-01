# Jellyhook: Jellyfin Webhook Processor

A microservice architecture for processing Jellyfin media server events via webhooks. The system receives events from a Jellyfin server, queues them through RabbitMQ, and processes them asynchronously, with special handling for media processing tasks such as Dolby Vision conversion.

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
└── workers/              # Worker service
    ├── src/workers/      # Worker source code
    └── tests/            # Worker tests
```

## Development

### Prerequisites

- Python 3.12+
- uv (Python package manager)
- Docker and Docker Compose (for containerized development)
- RabbitMQ (can be run via Docker)

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
make typecheck

# Run tests
make test

# Or run tests in specific directories
cd api && uv run pytest
```

### Running Locally

Run the API service:
```bash
cd api
FLASK_ENV=development uv run python -m api.main
```

Run workers:
```bash
cd workers
# Commands will depend on your worker implementation
```

## Deployment

Build and run with Docker:

```bash
# Build images
make build dockerfile=Dockerfile.api tag=latest
make build dockerfile=Dockerfile.workers tag=latest

# Tag images
make tag tag=latest

# Push images
make push tag=latest dockerfile=Dockerfile.api

# Run with Docker Compose
docker-compose up -d

# View logs
make api_logs
```

## Code Standards

- **Python Version**: 3.12+
- **Linting**: Using Ruff for linting and formatting
- **Type Checking**: Using MyPy for static type checking
- **Testing**: Using pytest for testing

### Coding Guidelines

- Use type annotations for all function parameters and return values
- Follow PEP 8 naming conventions
- Write meaningful docstrings for public functions and classes
- Handle errors appropriately with specific exception catching
- Use loguru for logging

## License

[Add your license information here]

## Contributing

[Add contribution guidelines here]