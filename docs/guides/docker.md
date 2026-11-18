# Running Jellyhook with Plain Docker

`docker-compose.yaml` is a development stack (Flask debug server, source bind mounts, macOS media
paths), so it is not a deployment template. This page describes how to run the published images
with nothing but `docker`, which means doing by hand what Compose would otherwise do: the network,
the broker's volume and config, start order, restart policies and environment.

A complete, ready-to-edit script is in
[`examples/docker-run.example.sh`](../examples/docker-run.example.sh). Fill in the values at the top
and run it. Running it again recreates the containers with the current settings, which is also how
you upgrade.

## Images

The API and worker images are published to GitHub Container Registry for amd64 and arm64 on each
release:

- `ghcr.io/ddobson/jellyhook-api:<tag>`
- `ghcr.io/ddobson/jellyhook-worker:<tag>`

To build them yourself instead, run `docker build -t jellyhook-api ./api` and
`docker build -t jellyhook-worker ./workers`. The default build argument `ENV=production` leaves
out dev dependencies.

The scheduler, needed only for [Random Playlists](configuration.md#random-playlists), runs from
the worker image with a different command; there is no separate image.

## 1. Network and broker

```sh
docker network create jellyhook
docker volume create rabbitmq_data

docker run -d --name rabbitmq --hostname rabbitmq \
  --network jellyhook --restart unless-stopped \
  -v rabbitmq_data:/var/lib/rabbitmq \
  -v /srv/jellyhook/rabbitmq.conf:/etc/rabbitmq/conf.d/10-jellyhook.conf:ro \
  -e RABBITMQ_DEFAULT_USER=jellyhook -e RABBITMQ_DEFAULT_PASS=<password> \
  rabbitmq:4.0.5-management-alpine
```

- **Name the container `rabbitmq`.** Both services default `RABBITMQ_HOST` to `rabbitmq`, and
  containers on a user-defined network resolve each other by name.
- **Pin `--hostname`.** RabbitMQ keys its data directory on the node name, which comes from the
  hostname. Without a fixed hostname, a recreated container cannot find the queued messages in the
  volume.
- **Mount `rabbitmq.conf`** from this repository. It raises `consumer_timeout` to 24
  hours. Without it the limit is 30 minutes, and any job that runs longer (a Dolby Vision
  conversion or track clean on a large remux) has its channel closed by the broker and the message
  redelivered, over and over.
- `RABBITMQ_DEFAULT_USER` and `RABBITMQ_DEFAULT_PASS` only take effect when the volume is empty.
  To change credentials later, use `rabbitmqctl` or the management UI.
- If you use a restricted user, it needs write access to `amq.default`, not just the queue names,
  because the worker publishes retries and dead letters through the default exchange.
- Port 5672 does not need publishing; only the containers talk to it. Publish 15672
  (`-p 15672:15672`) if you want the management UI.

## 2. API

```sh
docker run -d --name jellyhook-api --network jellyhook --restart unless-stopped \
  -p 8000:8000 \
  -e FLASK_ENV=production \
  -e SECRET_KEY=<random> \
  -e WEBHOOK_TOKEN=<random> \
  -e RABBITMQ_USER=jellyhook -e RABBITMQ_PASS=<password> \
  ghcr.io/ddobson/jellyhook-api:<tag>
```

The image runs gunicorn on **port 8000** and has its own healthcheck against `/health`. (Port 5001
is only the Compose dev server.) Point Jellyfin's webhook plugin at
`http://<docker-host>:8000/hooks/item_added`.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `FLASK_ENV` | **yes** | none | `production`. The app refuses to start without it. |
| `SECRET_KEY` | yes | none | Read by the production config, with no fallback. |
| `WEBHOOK_TOKEN` | recommended | unset | When set, `/hooks/*` requires `Authorization: Bearer <token>`. When unset, anyone who can reach the port can queue events. |
| `RABBITMQ_HOST` | no | `rabbitmq` | |
| `RABBITMQ_PORT` | no | `5672` | |
| `RABBITMQ_USER` | no | `guest` | Exactly `RABBITMQ_USER`; other spellings are ignored and fall back to guest. |
| `RABBITMQ_PASS` | no | `guest` | Exactly `RABBITMQ_PASS`, as above. |
| `RABBITMQ_VHOST` | no | `/` | Must match the worker's. |

## 3. Worker

```sh
docker run -d --name jellyhook-worker --network jellyhook --restart unless-stopped \
  --init --stop-timeout 60 \
  -v /srv/jellyhook/config:/config \
  -v /mnt/media:/data/media \
  -v /mnt/media/.jellyhook-tmp:/data/tmp \
  -e WORKER_ENV=production \
  -e JELLYFIN_HOST=http://jellyfin.lan -e JELLYFIN_PORT=8096 \
  -e JELLYFIN_API_KEY=<key> -e JELLYFIN_USER_ID=<user-id> \
  -e RABBITMQ_USER=jellyhook -e RABBITMQ_PASS=<password> \
  ghcr.io/ddobson/jellyhook-worker:<tag>
```

### Flags

- **`--init`** runs a tiny init as PID 1, so SIGTERM reaches Python and orphaned ffmpeg and
  mkvmerge processes are reaped.
- **`--stop-timeout 60`.** On SIGTERM the worker gives in-flight jobs
  `WORKER_SHUTDOWN_GRACE_SECONDS` (45 by default) to finish before disconnecting. Keep the stop
  timeout at least that value plus 15 seconds, or Docker kills the worker mid-drain.
- **`--restart unless-stopped` is required.** The worker deliberately exits non-zero when it loses
  its RabbitMQ connection or a consumer stops, and relies on Docker to start it again (see
  [Why the worker restarts](#why-the-worker-restarts)).

### Mounts

- **Configuration.** The worker reads `/config/jellyhook.json` (set `JELLYHOOK_CONFIG_PATH` to use
  a `.yaml` file instead). Start from [`examples/jellyhook.example.yaml`](../examples/jellyhook.example.yaml).
  If the file is missing, invalid or has neither an enabled webhook nor a Random Playlist, the
  worker logs a warning and exits 1, and Docker restarts it in a loop. Mount the whole `/config` directory rather than the single
  file: with `WORKER_ENV=production` the worker also writes `/config/logs/worker.log`.
- **Media, read-write.** The services rewrite files in place. The worker finds a file by looking
  for `<MOVIE_PATH>/<Name> (<Year>)/<Name>*.mkv` (also `.mp4` and `.avi`), and the same under
  `STANDUP_PATH`, so that folder layout must exist at the container paths.
- **Scratch space** at `TEMP_DIR` (`/data/tmp` by default), with room for a full remux. Putting it
  on the same filesystem as the media makes the final swap a cheap rename. Check any per-service
  `temp_dir` in your config too: the example's `dovi_conversion.temp_dir: /tmp/dovi_conversion`
  writes into the container's own filesystem, so point it at the mounted scratch path.
- **Path rules** in `metadata_update` (such as `path: /data/media/stand-up`) are matched against
  the worker's container paths, not Jellyfin's.

### Environment

| Variable | Default | Notes |
|---|---|---|
| `JELLYFIN_HOST` | none | **Required.** Include the scheme (`http://...`); the port is appended separately. |
| `JELLYFIN_PORT` | `8096` | |
| `JELLYFIN_API_KEY` | empty | Needed by `metadata_update`, `playlist_assignment` and Refreshes of Random Playlists. |
| `JELLYFIN_USER_ID` | empty | As above. Random Playlists are owned by this user, and `unplayed_only` means unplayed by them. |
| `WORKER_ENV` | **`development`** | Set to `production`. Otherwise logging is at DEBUG and no log file is written. |
| `WORKER_LOG_PATH` | `/config/logs/worker.log` | The log file with `WORKER_ENV=production`. Leave it for the worker; the scheduler sets its own. |
| `MOVIE_PATH` | `/data/media/movies` | Must match your mounts. |
| `STANDUP_PATH` | `/data/media/stand-up` | Must match your mounts. |
| `TEMP_DIR` | `/data/tmp` | |
| `JELLYHOOK_CONFIG_PATH` | `/config/jellyhook.json` | |
| `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASS` | as the API | |
| `RABBITMQ_VHOST` | `/` | Must match the API's, or the worker consumes an empty queue elsewhere. |
| `WORKER_MAX_ATTEMPTS` | `5` | Tries before a retryable failure is dead-lettered. |
| `WORKER_MAX_CONCURRENT_JOBS` | `1` | Jobs at once across all queues. |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | `45` | Keep `--stop-timeout` at least this plus 15. |
| `RABBITMQ_HEARTBEAT` | `60` | Seconds. |
| `REFRESH_QUEUE` | `jellyhook:random_playlist_refresh` | The queue Refreshes arrive on, consumed only when Random Playlists are configured. Must match the scheduler's. |
| `DEBUG` | `0` | Leave off. It starts debugpy, which the production image does not include, so the worker crashes. |

Use `--env-file` to keep secrets off the command line.

## 4. Scheduler

Only needed for [Random Playlists](configuration.md#random-playlists). The scheduler keeps their
Schedules and publishes a Refresh to RabbitMQ when one fires; the worker does the Refresh. The
scheduler never calls Jellyfin.

```sh
docker volume create scheduler_state

docker run -d --name jellyhook-scheduler --network jellyhook --restart unless-stopped \
  --init \
  -v /srv/jellyhook/config:/config \
  -v scheduler_state:/var/lib/jellyhook \
  -e WORKER_ENV=production \
  -e WORKER_LOG_PATH=/config/logs/scheduler.log \
  -e TZ=Europe/London \
  -e RABBITMQ_USER=jellyhook -e RABBITMQ_PASS=<password> \
  ghcr.io/ddobson/jellyhook-worker:<tag> \
  python3 -m workers.scheduler
```

- **Same config as the worker.** Mount the same `/config` directory (and set the same
  `JELLYHOOK_CONFIG_PATH`, if you changed it). An invalid `random_playlists:` section makes the
  scheduler exit 1; with none configured it logs a warning and waits idle, so it is safe to run
  either way.
- **Its own log file.** With `WORKER_ENV=production` the scheduler, like the worker, writes a log
  file under `/config/logs`. Set `WORKER_LOG_PATH=/config/logs/scheduler.log`, or both containers
  write to and rotate `worker.log`.
- **A volume for the state file.** The scheduler records when it last published a Refresh for
  each Random Playlist in `/var/lib/jellyhook/scheduler-state.json`. Without a volume there, every
  recreate (an upgrade, for example) counts every playlist as never refreshed and refreshes them
  all.
- **`TZ`** sets the timezone Schedules are read in, as an IANA name such as `Europe/London`. The
  default is UTC, so `0 1 * * 0` would mean 1am UTC.
- **`--restart unless-stopped` is required**, for the same reason as the worker's: a publish that
  fails because RabbitMQ is unreachable makes the scheduler exit non-zero, and Docker brings it
  back. On restart it catches up any Refresh it missed. SIGTERM stops it at once, so the default
  stop timeout is enough.

| Variable | Default | Notes |
|---|---|---|
| `JELLYHOOK_CONFIG_PATH` | `/config/jellyhook.json` | Only `random_playlists:` is read. |
| `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASS`, `RABBITMQ_VHOST` | as the worker | Must reach the same broker and vhost as the worker. |
| `REFRESH_QUEUE` | `jellyhook:random_playlist_refresh` | Must match the worker's. |
| `SCHEDULER_STATE_PATH` | `/var/lib/jellyhook/scheduler-state.json` | Keep it on the volume. |
| `WORKER_ENV` | **`development`** | Set to `production`, as for the worker. |
| `WORKER_LOG_PATH` | `/config/logs/worker.log` | Set to `/config/logs/scheduler.log`. |
| `TZ` | UTC | The timezone Schedules are read in. |

The Jellyfin variables are not needed here.

To Refresh a Random Playlist now, without waiting for its Schedule (and without changing when the
next one happens):

```sh
docker exec jellyhook-scheduler python3 -m workers.scheduler run "Bob's Burgers Random"
```

Both containers read the config only at startup. After editing `random_playlists:`, restart the
worker first and then the scheduler (`docker restart jellyhook-worker`, then
`docker restart jellyhook-scheduler`), so the worker knows any new playlist before the scheduler
publishes a Refresh for it.

## What Compose did that plain Docker does not

- **No start ordering.** Compose waited for RabbitMQ's healthcheck. Without it, a worker (or a
  scheduler with a Refresh to publish) that starts first fails to connect, exits and restarts
  until the broker answers; that is expected. The
  API connects per request and returns 503 with `Retry-After` until RabbitMQ is up. Starting
  RabbitMQ first avoids most of this; the example script waits on `rabbitmq-diagnostics ping`
  before starting the others.
- **No `.env` file loaded automatically.** Pass `--env-file` yourself.

## Why the worker restarts

The worker does not reconnect in-process. When it loses its connection, or a consumer stops, it
exits non-zero and Docker brings it back. Some causes act on the channel rather than the
connection, but the worker treats them the same way.

**The broker goes away or restarts**

- The RabbitMQ container is restarted: `docker restart`, recreating it on a new image, a Docker
  daemon upgrade, or a host reboot where the worker comes back first.
- RabbitMQ crashes or is OOM-killed, for example on a small host where ffmpeg is using most of the
  memory.
- The broker container is recreated with a new IP. The worker's open TCP connection points at the
  old address, so it breaks even though `rabbitmq` resolves correctly afterwards.

**The broker closes the connection on purpose**

- **Resource alarms.** When RabbitMQ crosses its memory high watermark or free-disk limit, it
  blocks connections that publish. The worker publishes retry and dead-letter copies and sets
  `blocked_connection_timeout=300`, so after five minutes blocked it drops the connection. Watch
  for this if the `rabbitmq_data` volume shares a disk with your media or scratch space: a Dolby
  Vision conversion writing a full-size remux can trip the disk alarm.
- **The consumer timeout runs out**, if a job outlasts `consumer_timeout` without an ack. This is
  why the broker config above matters.
- **Admin actions**: closing the connection from the management UI, deleting or redeclaring the
  queue, or changing the user's password, permissions or vhost.

**The network or host gets in the way**

- **Missed heartbeats.** With `RABBITMQ_HEARTBEAT=60`, if nothing gets through for about two
  intervals, both sides consider the connection dead. Causes include the host sleeping or
  suspending (common on NAS boxes and laptops), `docker pause` on either container, or a host so
  overloaded or deep in swap that the worker's connection thread cannot run. ffmpeg runs as a
  subprocess, so heavy CPU use alone usually is not enough.
- **Network changes**: `docker network disconnect`, or recreating the `jellyhook` network.
- **A broker on another machine**: Wi-Fi drops, router or switch reboots, VPN reconnects, or a
  firewall or NAT dropping the connection.

**When a restart will not help**

Restarts only fix problems that clear up on their own. Wrong credentials, a missing vhost, a
`RABBITMQ_HOST` that points at the wrong place, or a queue declared with different arguments
(`PRECONDITION_FAILED`) leave the worker restarting in a loop until you fix them.
`docker logs jellyhook-worker` shows the reason on each attempt.

**What happens to a running job**

Its message has not been acked, so the broker redelivers it once the worker reconnects, and the
whole job runs again from the start, including every service for that event. This is the
at-least-once delivery the services are designed for (see
[How the Worker Consumes Messages](message-consumption.md)). The practical cost is that a long
conversion interrupted near the end starts over.

On a home setup, the ones you are most likely to meet are a RabbitMQ restart or upgrade, the host
sleeping, and the disk alarm. The last two are avoidable: disable sleep on the host, and keep the
broker's volume off the disk that holds the media and scratch space.

## Upgrading

Pull the new images and recreate the API, worker and scheduler containers with the same settings
(or rerun the example script). Stop the worker with `docker stop`, not `docker rm -f`, so it gets
its grace period. Queued messages are in the `rabbitmq_data` volume and survive; `docker volume rm
rabbitmq_data` deletes them. The scheduler's state is in the `scheduler_state` volume, so a
recreated scheduler refreshes only the Random Playlists that are actually due.
