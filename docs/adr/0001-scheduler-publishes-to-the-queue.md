# The scheduler publishes Refresh messages; the worker does the work

Random Playlist Refreshes run on a Schedule, not in response to a Webhook. A separate scheduler
process (the worker image with its own entrypoint) does no Jellyfin work itself. When a Schedule
fires, it publishes a message to a dedicated RabbitMQ queue, and the existing worker performs the
Refresh. That way, Refreshes get the worker's retry and dead-letter handling: for example, when
Jellyfin is down at 1am, the Refresh is retried rather than lost. It also keeps one Jellyfin client
and one process lifecycle model.

## Considered Options

- **The scheduler calls Jellyfin directly.** Simpler, but it would need its own retry handling.
- **A scheduler thread inside the worker.** It would mix a timer into the worker's fail-fast,
  restart-via-Docker lifecycle.
- **Host cron or a one-shot container.** It would need setup outside Jellyhook.

## Consequences

Refreshes share the worker's job slots, so a Refresh can wait behind a long Dolby Vision
conversion. That's acceptable for a weekly Schedule.
