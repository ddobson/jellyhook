# Jellyhook

Jellyhook automates media housekeeping for a Jellyfin server. Some of its work responds to events
that Jellyfin reports, and some runs on a timetable.

## Triggers

**Webhook**:
An event that Jellyfin reports to Jellyhook, such as an item being added. This term covers only
Jellyfin-originated events, never work that runs on a timetable.
_Avoid_: hook, event (for scheduled work)

**Schedule**:
The recurring timetable on which a Random Playlist is refreshed.
_Avoid_: cron, timer, job

## Random Playlists

**Random Playlist**:
A Jellyfin playlist, identified by its name, whose contents Jellyhook owns and regenerates as a
random draw of episodes from one or more series.
_Avoid_: shuffle playlist, randomized playlist, generated playlist

**Refresh**:
Replacing a Random Playlist's entire contents with a new, independent random draw from its Episode
Pool. The playlist itself, and its identity, stay the same.
_Avoid_: regenerate, rebuild, update

**Episode Pool**:
The episodes a Refresh draws from: the episodes of the Random Playlist's series that are present in
the library, excluding Specials unless the playlist asks for them.
_Avoid_: candidates, library, source
