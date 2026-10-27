# Testing

Jellyhook has three layers of tests. Each exists because the layer below it cannot prove
something that matters.

| Layer | Where | Needs | Runs in the default `pytest` | Proves |
|---|---|---|---|---|
| Unit | `workers/tests/unit`, `api/tests` | nothing | yes | Logic and branches, with the outside world mocked |
| Component | `workers/tests/component` | ffmpeg, mkvtoolnix, dovi_tool | yes (CI runs it in an image that has them) | The media tools do what the commands assume, on real files |
| Integration | `workers/tests/integration`, `api/tests/integration` | Docker | **no** | Delivery guarantees against a real RabbitMQ, with real signals and real crashes |

Unit tests with a mocked broker can show that the code calls `basic_ack` after `basic_publish`.
They cannot show that a message survives a broker restart, that a `SIGKILL`ed worker's job is
picked up again, or that the broker actually refuses a queue declared with different arguments.
The integration layer exists for those.

## Running them

```bash
make test                # unit (and component, where the tools are installed), both packages
make test_integration    # integration, both packages; needs Docker running

# one package, or one test
cd workers && uv run --active pytest -m integration tests/integration
cd workers && uv run --active pytest -m integration tests/integration -k broker_restart
```

The integration run pulls `rabbitmq:4.0.5-alpine` (the version `docker-compose.yaml` uses), starts
one container per package on a free local port, and removes it at the end. It does not touch the
compose stack or its `rabbitmq_data` volume. Set `JELLYHOOK_IT_RABBITMQ_IMAGE` to test another
image. Expect about a minute for the worker suite and half a minute for the API suite.

Without Docker the integration tests skip. `JELLYHOOK_IT_REQUIRED=1` turns that into a failure;
`make test_integration` and CI both set it, so a runner without Docker cannot pass silently.

## What the integration tests cover

### Worker (`workers/tests/integration`)

Consumer behavior, in-process against the real broker (`test_consumer.py`):

- A successful message is acked and leaves nothing on `.retry` or `.dead`.
- A retryable failure returns after the retry delay and then succeeds. The delay is shortened to
  half a second for the test; production uses 60 seconds.
- Retries stop at the attempt cap and the message lands on `.dead` with the attempt count and the
  failure reason, still persistent, with the broker's `x-death` headers stripped.
- A permanent failure goes straight to `.dead`. A handler that raises is classified, not lost.
- A burst of 50 messages is processed one at a time and in order, with exactly one message
  unacknowledged at any moment (`prefetch=1`) and nothing dead-lettered.
- A job still running when the grace period ends is redelivered to the next worker; a job that
  fits inside the grace is finished and acked.
- The work queue declared the way the API declares it (durable, no arguments) is accepted. A queue
  with dead-letter arguments makes the worker fail with `PRECONDITION_FAILED`, which is the reason
  retries are done by the worker and not by the broker.
- A RabbitMQ user with only the documented minimal permissions (configure and read on the three
  queue names, write on those names and `amq.default`) can run every path. Without write access to
  `amq.default` the worker stops instead of losing the message.

Whole processes, with real signals and exit codes (`test_worker_process.py`). These run
`fake_worker.py`, which wires the real consumer, supervisor and signal handlers around a job whose
duration the test controls, plus the real `python -m workers.main`:

- `SIGTERM` lets a running job finish, acks it, and exits 0.
- `SIGTERM` past the grace period exits 0 and the message is back on the queue.
- `kill -9` mid-job: the next worker runs the job again (at-least-once, as documented).
- The broker restarting makes the worker exit 1, which is what Docker's restart policy reacts to.
  A restart in the middle of a job costs a rerun of that job, not the message.
- Messages queued before a broker restart, or before the broker was killed outright, are still
  there afterwards.
- The real worker processes a real message through the orchestrator and the `metadata_update`
  service, and parks a message for a file that has not landed yet on `.retry` with a readable
  reason.

### API (`api/tests/integration`)

- A 202 means the message is on the queue, persistent, with `application/json` content type.
- An accepted webhook survives a broker restart and the broker being killed.
- A stopped broker is a 503 with `Retry-After`, quickly, and the API recovers by itself when the
  broker returns. A broker that accepts connections but never answers is a bounded 503.
- A queue declared with different arguments, or wrong credentials, is a 503, never a lost message.
- 200 webhooks from 8 concurrent senders (the gunicorn layout) are each queued exactly once.
- A rejected body never reaches the queue.

## What they do not cover

- **The Docker images and compose file.** The tests run the Python code directly. The image builds,
  the healthchecks and `restart: unless-stopped` were checked by hand in Phase 1; nothing
  automated starts the real containers.
- **Long jobs against the broker's `consumer_timeout`.** The default is 30 minutes and the compose
  file raises it to 24 hours. Testing it would take longer than anyone would wait.
- **A real Jellyfin.** The worker tests use `metadata_update` with no rules, which never calls
  Jellyfin. The Jellyfin client is covered by unit tests only.
- **Several workers on one library.** The file lock is per process, and there is no test that two
  worker containers behave.
- **A queue created in the same instant the broker is `SIGKILL`ed.** While building this suite a
  confirmed, persistent message was lost this way. The broker's own state showed why: the *queue*
  was gone, not the message. RabbitMQ writes queue declarations to its metadata store lazily, so a
  queue created milliseconds before an unclean crash can vanish with what was in it. The tests that
  kill the broker therefore establish the queue first (`support.establish_queue`), which is the
  real situation: the worker declares its queues at startup, long before traffic. A message
  published to an established queue survived 24 of 24 kills in a separate probe. What this means
  for you: only the very first webhook after a brand-new install, followed by a hard crash within
  moments, is exposed.
- **A message the API never got a confirm for** may or may not be on disk; the API reports that as
  a 503.

## Writing a new integration test

- Use the `queue` fixture for a unique queue name; it removes the queue and its companions.
- Use `start_consumer` (worker) or `live_app` (API) rather than building your own plumbing, and
  `support.wait_until` instead of `sleep`. Every wait needs a timeout.
- Tests are plain functions, like the rest of the project.
- Never `docker exec` into the broker as root while it is still starting. Erlang then creates a
  root-owned `.erlang.cookie` that the broker's own user cannot read, and the broker crashes with
  `eacces`. Wait for the AMQP port instead (`support.wait_ready`), and use `support.rabbitmqctl`,
  which runs as the `rabbitmq` user.
- Check your test can fail. Break the behavior it claims to guard (change `prefetch_count`, ack
  before republishing, drop persistence) and confirm the test goes red.
