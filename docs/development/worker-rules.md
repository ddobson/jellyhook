# Worker Rules

Invariants in the worker service that are easy to break by accident. For the full explanation with
worked examples, see [message-consumption.md](../guides/message-consumption.md).

## Delivery and failures

- **Delivery is at-least-once.** A message is acked only after its job finishes (or after its retry
  or dead-letter copy is publisher-confirmed). The same event can therefore be processed twice, and
  a retry re-runs *every* service for it. Services must be safe to repeat (for example,
  `playlist_assignment` checks playlist membership before adding).
- **A Refresh replaces the whole playlist, so it is safe to repeat.** It finds (or creates) the
  playlist by exact name and swaps its contents in place, keeping the ID; an empty Episode Pool
  leaves the playlist alone. It reaches Jellyfin only through `JellyfinLibrary`
  (`clients/jellyfin_library.py`), so its tests run against the in-memory fake in
  `tests/unit/services/conftest.py`; add any new Jellyfin operation to both.
- **Classify failures by exception type.** Raise `TransientError` (or a subclass such as
  `MediaNotFoundError`) for something that may clear up on its own. `is_transient()` in
  `errors.py` also treats network errors, HTTP 5xx/408/429 and Jellyfin client errors for an
  unreachable server as transient. It follows `raise ... from exc` chains (and implicit
  `__context__`, which is how jellyfin_apiclient_python chains its errors), so always keep the
  `from exc` when a service wraps an error.
  Anything else (including other 4xx) is permanent and goes straight to `<queue>.dead`. An unknown
  or unimportable service raises `ServiceConfigurationError` and fails the message; do not turn it
  into a silent skip.
- **`metadata_update` is best-effort, except for transient failures.** A permanent failure there
  never fails a message; a transient one (Jellyfin down) does, so the update is retried rather than
  lost. Other services' failures always do.
- **Movie-file services skip other item types.** `metadata_update`, `dovi_conversion` and
  `media_track_clean` set `movies_only = True`, and the orchestrator skips them when the
  message's `ItemType` is anything but `Movie`, since only movies have a file under `MEDIA_PATHS`.
  A missing file for a movie is still `MediaNotFoundError` (retried).

## RabbitMQ

The pieces live in `workers/src/workers/clients/`: `rabbitmq.py` (`QueueConsumer`, one per queue,
owning its own connection), `topology.py` (the work/retry/dead queue names and declarations) and
`retry_policy.py` (`decide()` turns a result into ack / retry / dead-letter).

- **pika's `BlockingConnection` is not thread-safe.** Only the thread that runs
  `QueueConsumer.run()` may touch the connection or channel. Job threads hand results back through
  `add_callback_threadsafe`. Do not call pika from services.
- **Settle in this order:** publish the retry/dead copy, wait for the broker's confirm, then ack
  the original. That order is what keeps a message from being lost.
- **The work queue must stay declared as `durable=True` with no arguments.** The API declares it
  too, and any difference makes the redeclare fail with `PRECONDITION_FAILED`. This is why retry
  and dead-lettering are done by the worker, not a broker dead-letter exchange.
- **The retry delay is part of the `<queue>.retry` declaration.** Changing `RETRY_DELAY_MS` means
  deleting that queue first.
- **A restricted RabbitMQ user needs write access to `amq.default`**, not just the queue names,
  because retries and dead-lettering publish through the default exchange.

## Process lifecycle

- **Fail fast, reconnect via Docker.** A dropped connection or a stopped consumer makes the
  supervisor exit non-zero, and Docker's `restart: unless-stopped` brings the worker back. Leave
  reconnect loops out of the process.
- **SIGTERM drains.** In-flight jobs get `WORKER_SHUTDOWN_GRACE_SECONDS`; set the container's
  `stop_grace_period` to at least that plus 15 seconds.
- **The scheduler connects per publish.** `python -m workers.scheduler` (in the worker image)
  opens a connection, declares the Refresh queue's topology, publishes with a confirm and closes,
  each time. A connection held across a week-long wait would miss heartbeats. A publish that
  fails (broker down, connection dropped, no confirm) makes it exit non-zero, like the worker;
  SIGTERM ends its wait at once. `run "<playlist name>"` publishes one Refresh and exits.
- **Concurrency is a setting, default off.** All queues share `WORKER_MAX_CONCURRENT_JOBS` job
  slots (1); earlier versions ran one job per webhook.

## Services, files and tools

- **Run external tools with an argument list.** `utils.run_command` takes a list, has a timeout
  (six hours by default), drains stdout and stderr together, and kills the whole process group on
  timeout. Pass `ok_returncodes=(0, 1)` for tools that use 1 for "warnings" (mkvmerge).
- **Replace the original only once the replacement is in place.** Use `fileops.replace_file`. It
  swaps atomically and handles scratch space on another filesystem.
- **Every job gets its own scratch directory** from `fileops.make_job_dir`, never one named after
  the title. The orchestrator registers a service's `tmp_dir` before `exec()` and removes it in a
  `finally`, so failures clean up too.
- **Services that rewrite a file expose it as `locked_path`.** The orchestrator holds a per-file
  lock for the duration of `exec()`.
- **Get the Jellyfin client with `get_client()` at the point of use**, not at import time. It
  connects lazily and raises a retryable `JellyfinUnavailableError` when Jellyfin is down.

## Settings

Defaults live in `workers/src/workers/config/constants.py`.

| Variable | Default | Purpose |
|---|---|---|
| `WORKER_MAX_ATTEMPTS` | `5` | Tries (first included) before a retryable failure is dead-lettered |
| `WORKER_MAX_CONCURRENT_JOBS` | `1` | Jobs at once across all queues (no effect with a single queue, since prefetch is 1) |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | `45` | Time in-flight jobs get after SIGTERM |
| `RABBITMQ_HEARTBEAT` | `60` | AMQP heartbeat interval, seconds |
| `RABBITMQ_VHOST` | `/` | Virtual host; must match the API's `RABBITMQ_VHOST` |
| `REFRESH_QUEUE` | `jellyhook:random_playlist_refresh` | Queue carrying Refresh messages; consumed only when Random Playlists are configured. Gets `.retry` / `.dead` like a webhook queue |
| `SCHEDULER_STATE_PATH` | `/var/lib/jellyhook/scheduler-state.json` | Scheduler only: when it last published a Refresh for each Random Playlist. Keep it on a volume that survives container recreation |
| `WORKER_LOG_PATH` | `/config/logs/worker.log` | The log file written with `WORKER_ENV=production` (read by `logger.py`). The scheduler sets `/config/logs/scheduler.log`, since it shares the worker's `/config` mount and two processes must not rotate one file |
