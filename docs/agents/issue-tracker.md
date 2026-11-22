# Issue tracker: Linear

Issues and specs live in the
[Jellyhook team in Linear](https://linear.app/jellyhook/team/JLYH/all).

Use the available Linear integration to read, create, update, comment on,
label, assign, and close issues. If no Linear integration is available, ask
the user for access rather than using a different tracker.

## Wayfinding operations

A map is an issue labelled `wayfinder:map`. Its decision tickets are child
issues labelled `wayfinder:<type>`.

Use Linear's native blocking relationships. The frontier consists of open,
unassigned child issues whose blockers are closed. Claim a ticket by assigning
it to the developer driving the map before working on it.

On resolution, comment with the answer, close the ticket, and add a linked
one-line gist to the map's Decisions so far.
