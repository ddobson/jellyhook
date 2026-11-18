# API Rules

The API is a Flask app that receives Jellyfin webhooks and publishes them to RabbitMQ. These are
the rules that are easy to break. The worker side is in [worker-rules.md](worker-rules.md).

- **202 means queued.** The API publishes with publisher confirms and persistent delivery mode on a
  connection per request, and returns 503 with `Retry-After` if RabbitMQ does not confirm. Let
  `PublishError` propagate, and return 202 only after `publish()` returns.
- **Errors keep their status.** `api/errors.py` maps `HTTPException` to its own code (400, 401, 404,
  405, 413) and only unexpected exceptions to 500. Use `abort(400, ...)` for client mistakes.
- **Declare the queue exactly as the worker does** (durable, no arguments), or the second declare
  fails with `PRECONDITION_FAILED`.
- **RabbitMQ credentials come from `RABBITMQ_USER` / `RABBITMQ_PASS`** (not `*_USERNAME` /
  `*_PASSWORD`); unrecognised names silently fall back to guest/guest.
- **`/hooks/*` has no authentication unless `WEBHOOK_TOKEN` is set.** It is optional so existing
  Jellyfin webhook setups keep working; the API logs a warning at startup when it is unset.
