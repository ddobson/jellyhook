# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Jellyhook: Jellyfin Webhook Processor

A microservice architecture for processing Jellyfin media server events via webhooks. The system receives events from a Jellyfin server, queues them through RabbitMQ, and processes them asynchronously, with special handling for media processing tasks such as Dolby Vision conversion.

## Features

- **Webhook Reception**: Secure endpoint for receiving Jellyfin webhook events
- **Asynchronous Processing**: Decoupled architecture using RabbitMQ for reliable message queuing
- **Failure Handling**: Retryable failures are retried after a delay; everything else is dead-lettered with the reason attached (nothing is silently dropped)
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

These services are made to be completely independent. This is true for the code as well as the developer experience.

## Project Structure

```
jellyhook/
├── api/                  # API service
│   ├── src/api/          # API source code
│   │   ├── hooks/        # Webhook handlers
│   │   └── ...          
│   └── tests/            # API tests (integration/ needs Docker)
├── workers/              # Worker service
│   ├── src/workers/      # Worker source code
│   │   ├── clients/      # Jellyfin client and RabbitMQ consumption (see below)
│   │   ├── config/       # Configuration handling and env var constants
│   │   ├── services/     # Service implementations and the orchestrator
│   │   ├── errors.py     # Exception types and transient-vs-permanent classification
│   │   ├── results.py    # ProcessingResult returned by the orchestrator
│   │   ├── supervisor.py # Runs consumers; exits non-zero if one stops
│   │   └── ...
│   └── tests/            # Worker tests
│       ├── component/    # Run the real media tools on real files
│       ├── integration/  # Run against a real RabbitMQ in Docker (not in the default run)
│       └── unit/         # Unit tests
├── rabbitmq/             # Broker config mounted by docker-compose.yaml
├── docker-compose.yaml   # Development stack (not a production deployment)
└── docs/
    ├── message-consumption.md    # How the worker consumes messages, with worked examples
    └── examples/                 # Example configuration files
        ├── jellyhook.example.json    # Example webhook configuration
        └── jellyhook.example.yaml    # Example webhook configuration
```

`workers/src/workers/clients/` holds the RabbitMQ pieces, each with one job:

- `rabbitmq.py`: `QueueConsumer`, one per queue, owning its own connection
- `topology.py`: the work/retry/dead queue names and their declarations
- `retry_policy.py`: `decide()` turns a result into ack / retry / dead-letter

### Prerequisites

- Python 3.13+
- uv (Python package manager)
- Docker and Docker Compose (for containerized development)
- RabbitMQ (included in Docker Compose setup)
- Media processing tools: ffmpeg, mkvextract, mkvmerge, dovi_tool

### User Configuration

Users can configure webhooks and services using a YAML or JSON file. The worker reads it from the path in the `JELLYHOOK_CONFIG_PATH` environment variable (default `/config/jellyhook.json`). This enables:

- Configuring multiple webhooks with independent settings
- Enabling/disabling specific services for each webhook
- Setting service execution priorities
- Providing custom configuration for each service
- Defining metadata updates based on file paths or content patterns

## Worker Message Handling

Full explanation with examples: `docs/message-consumption.md`. The rules below are the ones that
are easy to break by accident.

- **Delivery is at-least-once.** A message is acked only after its job finishes (or after its retry
  or dead-letter copy is publisher-confirmed). The same event can therefore be processed twice, and
  a retry re-runs *every* service for it. Services must be safe to repeat (for example,
  `playlist_assignment` checks playlist membership before adding).
- **Classify failures by exception type.** Raise `TransientError` (or a subclass such as
  `MediaNotFoundError`) for something that may clear up on its own. `is_transient()` in
  `errors.py` also treats network errors, HTTP 5xx/408/429 and Jellyfin client errors for an
  unreachable server as transient. It follows `raise ... from exc` chains (and implicit
  `__context__`, which is how jellyfin_apiclient_python chains its errors), so always keep the
  `from exc` when a service wraps an error.
  Anything else (including other 4xx) is permanent and goes straight to `<queue>.dead`. An unknown or unimportable service raises `ServiceConfigurationError`
  and fails the message; do not turn it into a silent skip.
- **`metadata_update` is best-effort, except for transient failures.** A permanent failure there
  never fails a message; a transient one (Jellyfin down) does, so the update is retried rather than
  lost. Other services' failures always do.
- **Movie-file services skip other item types.** `metadata_update`, `dovi_conversion` and
  `media_track_clean` set `movies_only = True`, and the orchestrator skips them when the
  message's `ItemType` is anything but `Movie`, since only movies have a file under `MEDIA_PATHS`.
  A missing file for a movie is still `MediaNotFoundError` (retried).
- **pika's `BlockingConnection` is not thread-safe.** Only the thread that runs
  `QueueConsumer.run()` may touch the connection or channel. Job threads hand results back through
  `add_callback_threadsafe`. Do not call pika from services.
- **Never lose a message when settling it.** Publish the retry/dead copy and wait for the broker's
  confirm *before* acking the original.
- **The work queue must stay declared as `durable=True` with no arguments.** The API declares it
  too, and any difference makes the redeclare fail with `PRECONDITION_FAILED`. This is why retry
  and dead-lettering are done by the worker, not a broker dead-letter exchange.
- **The retry delay is part of the `<queue>.retry` declaration.** Changing `RETRY_DELAY_MS` means
  deleting that queue first.
- **Fail fast, don't reconnect in-process.** A dropped connection or a stopped consumer makes the
  supervisor exit non-zero, and Docker's `restart: unless-stopped` brings the worker back. Do not
  add reconnect loops inside the process.
- **SIGTERM drains.** In-flight jobs get `WORKER_SHUTDOWN_GRACE_SECONDS`; set the container's
  `stop_grace_period` to at least that plus 15 seconds.
- **A restricted RabbitMQ user needs write access to `amq.default`**, not just the queue names,
  because retries and dead-lettering publish through the default exchange.
- **Run external tools with an argument list, never a shell string.** `utils.run_command` takes a
  list, has a timeout (six hours by default), drains stdout and stderr together, and kills the whole
  process group on timeout. Pass `ok_returncodes=(0, 1)` for tools that use 1 for "warnings"
  (mkvmerge).
- **Never delete the original before the replacement is in place.** Use `fileops.replace_file`. It
  swaps atomically and handles scratch space on another filesystem.
- **Every job gets its own scratch directory** from `fileops.make_job_dir`, never one named after
  the title. The orchestrator registers a service's `tmp_dir` before `exec()` and removes it in a
  `finally`, so failures clean up too.
- **Services that rewrite a file expose it as `locked_path`.** The orchestrator holds a per-file
  lock for the duration of `exec()`.
- **Get the Jellyfin client with `get_client()` at the point of use**, never at import time. It
  connects lazily and raises a retryable `JellyfinUnavailableError` when Jellyfin is down.
- **Running jobs concurrently is a setting, not the default.** All queues share
  `WORKER_MAX_CONCURRENT_JOBS` job slots (1); earlier versions ran one job per webhook.

Worker settings (defaults in `workers/src/workers/config/constants.py`):

| Variable | Default | Purpose |
|---|---|---|
| `WORKER_MAX_ATTEMPTS` | `5` | Tries (first included) before a retryable failure is dead-lettered |
| `WORKER_MAX_CONCURRENT_JOBS` | `1` | Jobs at once across all queues (no effect with a single queue, since prefetch is 1) |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | `45` | Time in-flight jobs get after SIGTERM |
| `RABBITMQ_HEARTBEAT` | `60` | AMQP heartbeat interval, seconds |
| `RABBITMQ_VHOST` | `/` | Virtual host; must match the API's `RABBITMQ_VHOST` |

The API reads `RABBITMQ_USER` / `RABBITMQ_PASS` (not `*_USERNAME` / `*_PASSWORD`; unrecognised names
silently fall back to guest/guest).

API rules that are easy to break:

- **202 means queued.** The API publishes with publisher confirms and persistent delivery mode on a
  connection per request, and returns 503 with `Retry-After` if RabbitMQ does not confirm. Do not
  swallow `PublishError` or return 202 before `publish()` returns.
- **Errors keep their status.** `api/errors.py` maps `HTTPException` to its own code (400, 401, 404,
  405, 413) and only unexpected exceptions to 500. Use `abort(400, ...)` for client mistakes.
- **Declare the queue exactly as the worker does** (durable, no arguments), or the second declare
  fails with `PRECONDITION_FAILED`.

## Docker & Compose

- `docker-compose.yaml` is a **development** stack: Flask debug server, source bind mounts, and
  macOS media paths under `/Volumes/data`. It needs `workers/.env` and `workers/jellyhook.local.json`
  (neither is committed). `docker-compose.override.yaml` is gitignored and may hold secrets; never
  commit it.
- RabbitMQ data lives in the `rabbitmq_data` volume. `docker compose down -v` deletes it and every
  queued message.
- Both Dockerfiles install from `uv.lock` with `--frozen`: after changing dependencies in a
  `pyproject.toml`, regenerate that package's `uv.lock` or the image build will not pick them up.
- The worker image picks the `dovi_tool` release by `TARGETARCH` (amd64 or arm64).
- The compose stack waits for RabbitMQ's healthcheck, and every service has
  `restart: unless-stopped`.

## Build & Test Commands

Each package (`api`, `workers`) has its own uv environment, so run package commands **from inside
the package directory**. `uv run pytest <package>/tests` from the repo root fails to find pytest.

Preferred when changes only apply to a single package

- Test Package: `cd <package_name> && uv run pytest`
- Lint code: `cd <package_name> && uv run ruff check --fix --config=../ruff.toml .`
- Format code: `cd <package_name> && uv run ruff format --config=../ruff.toml .`
- Type check: `cd <package_name> && uv run mypy --config-file=../mypy.ini .` (must report zero errors; CI enforces it)

These commands are useful for running tasks against both main components at once

- Run all tests: `make test`
- Run tests with coverage: `make test_cov`
- Run single test: `cd api|workers && uv run --active pytest tests/path/to/test_file.py::test_function_name`
- Run component tests: `make test_component`
- Run integration tests (real RabbitMQ, needs Docker): `make test_integration`, or
  `cd api|workers && uv run --active pytest -m integration tests/integration`
- Format code: `make format`
- Lint code: `make lint`
- Type checking: `make typecheck`
- Run all checks: `make all`

## Testing layers

Full description, with what each layer proves and does not: `docs/testing.md`.

- **Unit** (`tests/unit`, API `tests`): mocks at the edges. Default run and CI.
- **Component** (`workers/tests/component`): real ffprobe, mkvmerge and dovi_tool on real files.
- **Integration** (`*/tests/integration`): a real RabbitMQ container driven through the `docker`
  CLI, including real SIGTERM/SIGKILL of worker processes and broker stop/kill/restart. Excluded
  from the default run by `-m "not integration"`; `JELLYHOOK_IT_REQUIRED=1` turns a missing Docker
  from a skip into a failure. The two packages each carry their own copy of
  `tests/integration/support.py` on purpose, because they must not import from each other.

When you change delivery behavior (acks, retries, topology, publishing, signals), add or update an
integration test as well as a unit test, and check the new test fails when the behavior is broken.

## Code Style Guidelines
- Python version: 3.13+
- Strict typing required (mypy, zero errors, enforced in CI). Tests are checked too, but
  `mypy.ini` does not require annotations on test functions
- Use modern Python type hints (eg. `list[str]` not `typing.List[str]`)
- Use union syntax (eg. `str | None` not `typing.Optional[str]`)
- Line length: 100 characters max
- Quotes: Double quotes for strings
- Imports: Sorted with isort (combine as-imports, first-party first)
- Docstrings: Google style docstrings with types in docstring
- Error handling: Use appropriate exception types
- Naming: Follow PEP8 naming conventions
- Testing: Write unit tests for all new functionality
- Tests are plain pytest functions (no test classes); use fixtures for setup
- Component tests: Write integration tests for end-to-end workflows

## Developer Tools
`uv` for python application package management.
`pytest` for testing. Coverage extention is installed.
`ruff` for formatting and linting. Config in ruff.toml.
`debugpy` for interactive debugging
`code` is the preferred IDE
`mypy` for typechecking. Typed code is strongly preferred but not required.

## Known Issues

Things that will trip you up, so you do not mistake them for something you broke:

- pika, jellyfin_apiclient_python and debugpy ship no type stubs, so `mypy.ini` ignores their
  missing imports (per module, not globally); anything they return is `Any`. loguru's `Logger` and
  `Record` exist only in its stubs, so import them under `TYPE_CHECKING` and quote the annotation.
- `/hooks/*` has no authentication unless `WEBHOOK_TOKEN` is set. It is optional so existing
  Jellyfin webhook setups keep working; the API logs a warning at startup when it is unset.
