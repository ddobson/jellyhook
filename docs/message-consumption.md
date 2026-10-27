# How the Worker Consumes Messages

This page explains what happens to a webhook event between the moment Jellyfin sends it and the
moment the worker is finished with it: what runs, in what order, what happens when something goes
wrong, and what you will see in RabbitMQ while it does.

For the one-page summary and the list of settings, see
[Message Handling](../workers/README.md#message-handling) in the worker README. The code lives in
`workers/src/workers/clients/` (`rabbitmq.py`, `topology.py`, `retry_policy.py`) and
`workers/src/workers/supervisor.py`.

## The short version

- Events are handled **one at a time, in arrival order** (per queue).
- A message is **acknowledged only after it has been dealt with**. If the worker dies first, the
  broker hands the message to the next consumer.
- Failures are **sorted into "worth retrying" and "not worth retrying"**. Nothing is thrown away:
  messages end up either done, waiting to be retried, or parked on a dead-letter queue with the
  reason attached.
- If the worker loses its connection to RabbitMQ it **exits** and relies on Docker to start it
  again, instead of carrying on while consuming nothing.

## The moving parts

```
Jellyfin ──webhook──▶ API ──publish──▶  jellyfin:item_added        (work queue)
                                              │
                                              ▼
                                  worker (one connection per queue)
                                              │
                          runs the configured services in priority order
                                              │
                 ┌────────────────────────────┼─────────────────────────────┐
                 ▼                            ▼                             ▼
              success                  retryable failure             permanent failure,
              ack, done                       │                      or retries used up
                                              ▼                             │
                                jellyfin:item_added.retry                   ▼
                                (waits 60s, then returns to          jellyfin:item_added.dead
                                 the work queue)                     (kept for you to inspect)
```

Every webhook queue gets the same three queues, all on RabbitMQ's default exchange. The work queue
is declared exactly as the API declares it, so existing installs keep working.

| Queue | Purpose |
|---|---|
| `<queue>` | New events and events returning from a retry delay |
| `<queue>.retry` | Holds a failed event for 60 seconds, then sends it back to `<queue>` |
| `<queue>.dead` | Events the worker gave up on. Nothing consumes this queue. |

## One message, start to finish

1. RabbitMQ delivers one message. The worker asks for one at a time (prefetch of 1), so the rest
   wait in the queue.
2. A job thread runs every enabled service for that message, in priority order (lowest number
   first). A service failing does not stop the later ones from running.
3. The job reports a result: **success**, **retry** or **failed** (see below).
4. The worker settles the message:
   - success: acknowledge it
   - retry: publish a copy to `.retry`, wait for the broker to confirm, *then* acknowledge the
     original
   - failed, or retries used up: same, but to `.dead`
5. The next message is delivered.

The order in step 4 matters. If the worker crashes between publishing the copy and acknowledging
the original, you get the message twice. The opposite order could lose it. Duplicates are
recoverable; lost events are not.

### What counts as retryable

| Failure | Result |
|---|---|
| The video file is not on disk yet | Retry |
| Jellyfin is unreachable or times out | Retry |
| Jellyfin answers with a server error (5xx), `408` or `429` | Retry |
| Jellyfin rejects the request with any other 4xx (a bad API key, an unknown item) | Failed |
| An unknown or unloadable service in your config | Failed |
| A message body that is not a JSON object | Failed |
| Any other error inside a service | Failed |

Services often catch a low-level error and re-raise their own (`raise SomeServiceError(...) from
exc`). The worker follows that chain to find the underlying cause, so a wrapped connection failure
is still retried and a wrapped `401` is not. Keep the `from exc` when you write a service, or the
cause is hidden and the failure is treated as permanent.

A message with several failures is retried if *any* of them is retryable. The permanent one will
simply fail again and the message ends up on `.dead`.

`metadata_update` is the exception to "a failure fails the message": its errors are logged but
never fail the event.

### How many tries

`WORKER_MAX_ATTEMPTS` (default 5) counts the first attempt. With the fixed 60-second delay, a
retryable failure is attempted at roughly 0, 1, 2, 3 and 4 minutes before it is dead-lettered. If
files regularly take longer than that to land, raise the setting: each extra attempt adds a minute.

The attempt count travels with the message in the `x-jellyhook-attempts` header, and the reason for
the last failure in `x-jellyhook-failure-reason`. A header that is not a usable count (say, edited
by hand when replaying a dead letter) is logged and treated as no earlier attempts.

## Worked example: a burst of five new items

You add five items to a library at 20:00 and Jellyfin sends five events within seconds. Service
priorities are metadata (10), Dolby Vision conversion (20), track clean (30), playlist (40).

| Item | What it needs |
|---|---|
| **Item A** | Dolby Vision Profile 7 remux. All four services. Conversion takes about 35 minutes, track clean about 8. |
| **Item B** | Standard dynamic range. Only metadata and playlist have anything to do; they take seconds. |
| **Item C** | The file is still being copied into place when the event arrives. |
| **Item D** | Metadata and playlist, but Jellyfin is restarting and refuses connections. |
| **Item E** | The conversion service raises an error on a stream layout it does not support. |

All five messages land on `jellyfin:item_added`. The worker takes Item A first and the other four
sit in the queue as "ready".

| Time | What happens |
|---|---|
| 20:00 | Item A is delivered and its four services start running one after another. The worker's connection thread keeps exchanging heartbeats the whole time, so the broker sees a healthy consumer. |
| 20:45 | Item A finishes and is acknowledged. The queue now shows one fewer message. |
| 20:45 | Item B is delivered, takes a few seconds and is acknowledged. |
| 20:45 | **Item C**: the services that need the file raise "no video found". That is retryable, so a copy goes to `.retry` (attempt 1) and the original is acknowledged. The worker moves on without waiting. |
| 20:45 | **Item D**: the playlist service cannot reach Jellyfin, which is retryable. The metadata service fails the same way, but its errors never fail an event. The message goes to `.retry`. |
| 20:45 | **Item E**: the conversion error is permanent. The message goes straight to `.dead` with the reason in its headers. |
| 20:46 | The 60-second delay on `.retry` expires. Items C and D return to the work queue and are processed again. If the copy has finished and Jellyfin is back, both succeed on their second attempt. |

Things this shows:

- **A failing item does not block the others.** Item C waits out its delay on the side while
  other events keep flowing. The price is that it is processed *later*, not in its original order.
- **Short items wait behind long ones.** Item B's few seconds of work still queue behind Item A's
  45 minutes. Handling one message at a time is deliberate: media jobs are heavy on disk and CPU.
- **Item E is not lost.** It sits on `.dead` until you look at it.

## Worked example: what an operator sees

During the burst, the management UI (port 15672, Queues tab) shows roughly:

| Moment | `item_added` ready / unacked | `.retry` | `.dead` |
|---|---|---|---|
| 20:00:05, Item A running | 4 / 1 | 0 | 0 |
| 20:45:10, A and B done, C and D waiting to retry | 0 / 0 | 2 | 1 |
| 20:46:15, C and D succeeded on retry | 0 / 0 | 0 | 1 |

The worker log carries the same story, for example:

```
WARNING Attempt 1/5 failed for jellyfin:item_added; retrying in 60s: media_track_clean: MediaNotFoundError: No video found for 'Item C'
ERROR   Giving up on a message from jellyfin:item_added after 1 attempt(s); moved to jellyfin:item_added.dead: dovi_conversion: ValueError: unsupported stream layout
```

### Looking at and replaying dead messages

Open `<queue>.dead` in the management UI and use "Get messages" to read one. The failure reason and
attempt count are in the properties' headers.

To send messages back for another go once you have fixed the cause, enable the shovel plugins:

```bash
docker compose exec rabbitmq rabbitmq-plugins enable rabbitmq_shovel rabbitmq_shovel_management
```

That adds a "Move messages" tab to each queue's page. Move them from `<queue>.dead` back to
`<queue>`. The attempt counter in their headers carries over, so a message that already used all
its attempts goes straight back to `.dead` if it hits another retryable failure. To give an event a
full set of attempts again, publish its body to `<queue>` as a new message instead of moving it.

## What happens when things break

### The worker is redeployed mid-job

The worker receives `SIGTERM` and stops taking new messages. It then gives running jobs
`WORKER_SHUTDOWN_GRACE_SECONDS` (default 45) to finish, and acknowledges any that do.

- A **short job** (a metadata or playlist update) that is running at that moment finishes and is
  acknowledged, so it is not repeated.
- A **long job** (a 35-minute conversion) will not finish in time. The worker disconnects, the
  broker sees an unacknowledged message and puts it back, and the new worker starts that item over.

Set the container's `stop_grace_period` to at least `WORKER_SHUTDOWN_GRACE_SECONDS` plus 15 seconds
(the worker allows a few extra seconds to disconnect). The compose file uses 60 against the default
45. Docker's own default of 10 seconds is too short: the worker would be killed in the middle of
draining.

### RabbitMQ restarts or the network drops

The worker's connection fails, the worker exits with a non-zero code, and Docker's restart policy
starts it again. It keeps trying until the broker is healthy. A job that was running when the
connection dropped is stopped along with the process and run again from the start.

This is intentional. The alternative, reconnecting inside a long-lived process, is where the earlier
design failed: the process stayed up with nothing consuming. A fresh process has fresh state.

### A job takes longer than expected

The broker expects a consumer to acknowledge within `consumer_timeout`. RabbitMQ's default is 30
minutes, which a large remux conversion can exceed; when that happens the broker closes the channel
and the message is delivered again, so the job runs twice. The bundled `rabbitmq/rabbitmq.conf` raises it to 24 hours. If you run your own broker, set the same.

### Several webhooks

Each enabled webhook gets its own queue and its own connection. They share one pool of job slots
(`WORKER_MAX_CONCURRENT_JOBS`, default 1), so heavy work on two different queues runs one job at a
time unless you raise it. With only the `item_added` webhook, that setting has no effect, because
one queue never has two jobs in flight.

**This is a change from earlier versions**, which ran one job per webhook in parallel. If you have
several enabled webhooks, their jobs now queue behind each other. One job at a time is the safer
default for ffmpeg and dovi_tool, which compete hard for disk and CPU. Set
`WORKER_MAX_CONCURRENT_JOBS` to the number of webhooks to get the old behaviour back, if your host
can take that many conversions at once.

## Deploying and upgrading

Things to put in place when you run the worker. The Docker Compose file in this repo already does
the first three; other platforms (Portainer, Unraid, Kubernetes, plain `docker run`) need them by
hand.

1. **A restart policy.** A dropped connection makes the worker exit non-zero on purpose, so
   something must start it again (`restart: unless-stopped` or the platform's equivalent). Without
   one, the worker stays down after the first broker restart.
2. **A long enough stop timeout.** `stop_grace_period` of at least
   `WORKER_SHUTDOWN_GRACE_SECONDS` + 15 seconds (see above).
3. **A long enough ack timeout on the broker.** Set `consumer_timeout = 86400000` (24 hours) in
   `rabbitmq.conf`, as `rabbitmq/rabbitmq.conf` does for the bundled broker. A broker you run
   yourself keeps RabbitMQ's 30-minute default otherwise, which is shorter than a large Dolby Vision
   conversion. An unacknowledged message is held for the whole job.
4. **Permissions, if you use a restricted RabbitMQ user.** The worker declares `<queue>.retry` and
   `<queue>.dead` and publishes to them through the default exchange. Grant the user configure and
   read on all three queue names, and write on those names **plus `amq.default`**:

   ```bash
   rabbitmqctl add_user jellyhook-worker '<password>'
   rabbitmqctl set_permissions -p / jellyhook-worker \
     '^jellyfin:item_added(\.retry|\.dead)?$' \
     '^(jellyfin:item_added(\.retry|\.dead)?|amq\.default)$' \
     '^jellyfin:item_added(\.retry|\.dead)?$'
   ```

   (The arguments are configure, write, read. Repeat the queue names for each enabled webhook.)
   Without the `amq.default` write permission, publishing a retry fails with `ACCESS_REFUSED`
   even though the queue permissions look right. The API publishes the same way, so a restricted
   API user needs that write permission too. The default `guest` user needs none of this.
5. **A check on `.dead`.** Nothing consumes it and nothing alerts on it. The worker logs an `ERROR`
   line ("Giving up on a message…") each time it dead-letters one, and a `WARNING` at startup if any
   are waiting. You can also look at the queue in the management UI or run
   `rabbitmqctl list_queues name messages` and watch the `.dead` rows. If you want an alert, hook it
   to that log line or that count.

**Existing queues need no migration.** The work queue is declared exactly as before. Do not add
dead-letter arguments to it. If you change `RETRY_DELAY_MS` later, delete `<queue>.retry` first.

**Rolling back is safe.** The broker's own TTL on `<queue>.retry` pushes parked messages back onto
the work queue even if an older worker is running. The leftover `.retry` and `.dead` queues are
harmless, though the older worker goes back to dropping failed messages.

## Known limitations

These are properties of the current design, worth keeping in mind when reading the examples above.

- **A retry re-runs every service for that event**, including ones that already succeeded, and a
  redelivered event does the same. Services have to be safe to repeat. Where each stands:
  - `dovi_conversion`: safe. A finished conversion leaves a Profile 8 file, and a repeat sees that
    and does nothing.
  - `media_track_clean`: safe. After cleaning, every remaining stream is one it wants to keep, so a
    repeat does nothing.
  - `metadata_update`: safe. It only sends changes when the computed genres or tags differ.
  - `playlist_assignment`: **not verified.** It POSTs the item to each matching playlist every
    time. Whether Jellyfin ignores an item that is already in the playlist depends on the Jellyfin
    version, and that has not been checked.
- **At-least-once, not exactly-once.** After a crash or disconnect the same event can be processed
  twice.
- **`metadata_update` and outages.** Its permanent failures are logged and ignored, but if
  Jellyfin is unreachable the message is retried like any other.
- **Abandoned conversions and cleans.** A job cut off mid-way (shutdown past the grace period, a
  broker restart) is redelivered and starts again from the beginning. That is now harmless to the
  library:
  - Every run works in its own scratch directory (`<temp_dir>/<name>-<random>`), so a rerun never
    reuses the half-written files of the run before it. The abandoned run's directory is cleaned
    up when the worker finishes with it, or stays behind if the process was killed outright; it is
    safe to delete by hand.
  - The original file is never deleted or moved aside first. The finished file is written next to
    it (copied there, as a hidden `.<name>.<random>.jellyhook-tmp` file, if the scratch directory
    is on another filesystem) and then swapped in with one atomic rename. A crash at any point
    leaves either the old file or the new one, never neither. A killed worker can leave a
    `.jellyhook-tmp` file beside the movie; it is safe to delete.
  - Scratch space needs room for a full copy of the movie (and for the extracted video stream
    during a Dolby Vision conversion). If the media library also needs room for the staged copy
    during the swap, that is another full copy on that disk.
- **A 202 from the API means RabbitMQ confirmed the message**, and it is stored as persistent, so
  events waiting in the queue survive a broker restart or a hard kill (the queue data lives in
  the `rabbitmq_data` volume). One narrow exception: a queue created within moments of an unclean
  crash can be lost with its contents, because the broker writes queue declarations lazily. See
  `docs/testing.md`. If the broker cannot be reached the API answers 503 with `Retry-After`
  instead. Whether Jellyfin's webhook plugin retries after a 503 has not been checked, so a webhook
  sent while RabbitMQ is down may still be lost on the Jellyfin side.
- **The retry delay is fixed.** It is part of the `.retry` queue's declaration. To change it, delete
  that queue first or RabbitMQ rejects the new settings.
- **File locks are per worker process.** Two jobs that rewrite the same file wait for each other
  (an in-process lock keyed on the resolved path), even across queues. The lock does not reach
  across several worker containers pointed at the same library; run one worker.
- **External programs have a time limit.** Each ffmpeg, mkvmerge, mkvextract and dovi_tool run is
  killed after six hours. A killed run is a permanent failure and lands on `.dead`; it is not
  retried.
- **Jellyfin is contacted on first use.** A Jellyfin that is down when the worker starts no longer
  crashes it; the jobs that need Jellyfin fail as retryable and are tried again.
