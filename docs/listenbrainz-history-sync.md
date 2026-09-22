# ListenBrainz and Maloja history sync

In Settings > Services > ListenBrainz, configure the API Base URL and User Token.
Leave the URL blank for the official ListenBrainz service. A username can be
entered explicitly or resolved from the token.

For Maloja, use `http://your-server:42010/apis/listenbrainz` (an optional `/1`
suffix is accepted) and the existing Maloja API key. Include any reverse-proxy
prefix, for example `https://music.example/maloja/apis/listenbrainz`.
SoulSync recognizes that compatibility URL and reads history from the native
`/apis/mlj_1/scrobbles` endpoint on the same server. The `/apis/lbrnz` alias is
also supported. A proxy must expose the native endpoint as well as the
ListenBrainz compatibility endpoint.

Open Stats and press Run in the ListenBrainz control. This starts the history
backfill and enables hourly sync. Imported history feeds Stats and the existing
Daily Mixes and discovery history queries. Outbound scrobbling remains a
separate setting; imported plays are marked as already scrobbled to ListenBrainz.

Subsequent runs scan newest-first through a 24-hour overlap with the last
successful cursor. They advance that cursor only after the whole window succeeds.
A failed or cancelled run can safely be retried; existing events are deduplicated.
Backfills retain a timestamp checkpoint for resumption. HTTP errors and malformed
responses are reported as failures, not empty history.

The Maloja adapter follows its native page-based history API because its
ListenBrainz adapter does not implement history reads. This is validated with
mocked HTTP responses matching upstream shapes, not a live Maloja deployment.

References: [Maloja native API](https://github.com/krateng/maloja/blob/master/maloja/apis/native_v1.py),
[Maloja ListenBrainz adapter](https://github.com/krateng/maloja/blob/master/maloja/apis/listenbrainz.py).

## Duplicate detection

A source event is identified by normalized title, artist, and exact play timestamp.
Repeated plays with different timestamps are retained, even seconds apart.
Across sources, only matching titles/artists within 10 seconds are considered;
conflicting nonempty album names prevent a match. Each stored play can represent
at most one event from each source. Persisted source timestamps keep those matches
stable across pages, retries, and restarts. Server/player history uses the same
matcher, so arrival order does not create a second copy.

This deliberately favors retaining uncertain events over deleting genuine plays.
Different metadata or clock offsets above 10 seconds can still leave duplicates.
Previously stored duplicates are not removed, and previously skipped plays require
a full backfill to recover if they fall outside the normal overlap window.
