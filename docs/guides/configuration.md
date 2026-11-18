# User Configuration

Webhooks, services and Random Playlists are configured in a YAML or JSON file. The worker and the
scheduler read it from the path in the `JELLYHOOK_CONFIG_PATH` environment variable (default
`/config/jellyhook.json`).

The file lets you:

- Configure multiple webhooks with independent settings
- Enable or disable specific services for each webhook
- Set service execution priorities
- Provide custom configuration for each service
- Define metadata updates based on file paths or content patterns
- Define [Random Playlists](#random-playlists) that are refreshed on a Schedule

Starting points: [examples/jellyhook.example.yaml](../examples/jellyhook.example.yaml) and
[examples/jellyhook.example.json](../examples/jellyhook.example.json).

## Random Playlists

A Random Playlist is a Jellyfin playlist of random episodes from one or more series. On each
Schedule, Jellyhook Refreshes it: it replaces the playlist's whole contents with a new random draw.
The `scheduler` container keeps the Schedules and the worker does the Refreshes, so both must be
running (see [docker.md](docker.md#4-scheduler)).

Random Playlists go in a top-level `random_playlists:` list, next to `worker:` rather than under
it:

```yaml
random_playlists:
  - name: Bob's Burgers Random
    series: ["Bob's Burgers"]
    size: 25
    schedule: "0 1 * * 0"   # every Sunday at 1am
    include_specials: false
    unplayed_only: false

  - name: Cartoon Night
    series:
      - Bob's Burgers
      - { id: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6" }   # The Simpsons, by its Jellyfin ID
    size: 40
    schedule: "0 18 * * 5"  # every Friday at 6pm
    unplayed_only: true
```

| Key | Required | Default | Meaning |
|---|---|---|---|
| `name` | yes | | The playlist's name in Jellyfin. Each Random Playlist needs its own. |
| `series` | yes | | A non-empty list. Each element is a series title, matched exactly (case included), or `{ id: "<series id>" }`. Quote an ID that YAML would otherwise read as a number. |
| `size` | yes | | How many episodes a Refresh draws, at least 1. |
| `schedule` | yes | | A standard 5-field cron expression (minute, hour, day of month, month, day of week), read in the container's timezone (`TZ`). |
| `include_specials` | no | `false` | Include Specials (season 0) in the Episode Pool. |
| `unplayed_only` | no | `false` | Draw only episodes that `JELLYFIN_USER_ID` has not played. |

**The name is the playlist's identity.** Jellyhook finds the playlist by its exact name among the
playlists of the user it signs in as (`JELLYFIN_USER_ID`). If there is none, the first Refresh
creates it; if there is exactly one, it is adopted, so you can hand an existing playlist over to
Jellyhook. Renaming a Random Playlist in the config therefore creates a new playlist, and the old
one is left as it is; delete it in Jellyfin if you no longer want it. If that user has several
playlists with the name, the Refresh fails rather than guess which one to replace.

A Refresh replaces the contents in place, so the playlist keeps its Jellyfin ID and pins and
favourites in your clients keep working. Anything you add to the playlist by hand is replaced at
the next Refresh.

### The Episode Pool

A Refresh draws from the Episode Pool: the episodes of all the listed series, each at most once,
that are present on disk (Jellyfin's missing and virtual episodes never count). Specials are left
out unless `include_specials` is set, and played episodes are left out when `unplayed_only` is set.
The draw is random, in random order, with no repeats, and independent of earlier Refreshes.

- **A pool smaller than `size`**: the playlist gets every episode in it, shuffled, and the worker
  logs a warning.
- **An empty pool** (for example, every episode watched with `unplayed_only`, or a library in the
  middle of a rescan): the playlist is left as it is, and the worker logs a warning.
- **A series title that matches no series, or several**: the Refresh fails and its message goes to
  `jellyhook:random_playlist_refresh.dead` (the Refresh queue's dead-letter queue) with the
  reason. For two series that share a title, such as the UK and US
  versions of *The Office*, use `{ id: ... }`. The ID is in the series page's URL in Jellyfin's web
  client (`...details?id=<series id>`).

If Jellyfin is unreachable when a Refresh runs, it is retried like any other job.

### When Refreshes happen

- When a Schedule fires, the scheduler publishes a Refresh and the worker carries it out. A
  Refresh may wait behind a long job, such as a Dolby Vision conversion.
- When the scheduler starts, it publishes one Refresh for each Random Playlist that has never been
  refreshed, or whose Schedule fired while the scheduler was down. However many runs were missed,
  that is one Refresh. A playlist that is up to date is not refreshed by a restart.
- To Refresh a playlist now, run `python3 -m workers.scheduler run "<playlist name>"` in the
  scheduler container, for example
  `docker compose exec scheduler python3 -m workers.scheduler run "Bob's Burgers Random"`.
  It does not change when the next scheduled Refresh happens. An unknown name exits non-zero.

### Changing the config

The worker and the scheduler read the config only when they start. After editing
`random_playlists:`, restart the worker first, then the scheduler:

```sh
docker compose restart worker
docker compose restart scheduler
```

`docker compose restart scheduler` picks up new and removed playlists and changed Schedules. The
worker looks up each playlist's definition in its own copy of the config when it does the Refresh,
so restart it too, first: a Refresh for a playlist the worker does not know is dead-lettered.

An invalid `random_playlists:` section (an unparseable `schedule`, a duplicate `name`, a `size`
below 1, an empty `series` list, or an unknown key) stops both from starting, and the log says
which playlist is wrong. With no Random Playlists configured, the scheduler logs a warning and
waits idle.
