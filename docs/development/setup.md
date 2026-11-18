# Development

## Prerequisites

- Python 3.13+ and `uv`
- Docker and Docker Compose (RabbitMQ is part of the Compose stack)
- Media tools for the worker: ffmpeg, mkvextract, mkvmerge, dovi_tool

## Commands

Each package (`api`, `workers`) has its own uv environment, so run package commands **from inside
the package directory**. `uv run pytest <package>/tests` from the repo root fails to find pytest.

For a change that touches one package:

```bash
cd <package> && uv run pytest
cd <package> && uv run ruff check --fix --config=../ruff.toml .
cd <package> && uv run ruff format --config=../ruff.toml .
cd <package> && uv run mypy --config-file=../mypy.ini .   # must report zero errors; CI enforces it
```

To run against both packages at once, use the Makefile (`make test`, `make test_cov`,
`make test_component`, `make test_integration`, `make format`, `make lint`, `make typecheck`,
`make all`). One test:
`cd api|workers && uv run --active pytest tests/path/to/test_file.py::test_function_name`.

Testing layers and what each proves: [testing.md](testing.md).

## Docker and Compose

- `docker-compose.yaml` is a **development** stack: Flask debug server, source bind mounts, and
  macOS media paths under `/Volumes/data`. It needs `workers/.env` and `workers/jellyhook.local.json`
  (neither is committed). `docker-compose.override.yaml` is gitignored and may hold secrets, so keep
  it out of commits.
- For running the published images without Compose, see [docker.md](../guides/docker.md).
- RabbitMQ data lives in the `rabbitmq_data` volume. `docker compose down -v` deletes it and every
  queued message.
- Both Dockerfiles install from `uv.lock` with `--frozen`: after changing dependencies in a
  `pyproject.toml`, regenerate that package's `uv.lock` or the image build will not pick them up.
- The worker image picks the `dovi_tool` release by `TARGETARCH` (amd64 or arm64).
- The compose stack waits for RabbitMQ's healthcheck, and every service has
  `restart: unless-stopped`.

## Known issues

Things that will trip you up, so you do not mistake them for something you broke:

- pika, jellyfin_apiclient_python and debugpy ship no type stubs, so `mypy.ini` ignores their
  missing imports (per module, not globally); anything they return is `Any`. loguru's `Logger` and
  `Record` exist only in its stubs, so import them under `TYPE_CHECKING` and quote the annotation.
