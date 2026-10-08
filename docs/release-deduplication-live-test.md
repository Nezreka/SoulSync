# Isolated Usenet live test, 2026-10-08

Three real single-track requests were tested against the existing Prowlarr
installation and a separate SABnzbd 5.1.3 container, using the modified
`fix/usenet-matching` backend. These were actual indexer searches, NZB grabs,
NNTP downloads, archive extraction, song selection and imports, rather than
mock download responses.

## Isolation and copied settings

The backend source was copied from the feature worktree and mounted read-only.
The test used a fresh database, separate Docker network and separate Music
Library under `/tmp/soulsync-live-usenet-20261008`. Only configuration, the user
profile and Quality Profile were copied. Production library records, Wishlist,
queues and history were not copied. The worker received public Deezer track
and album metadata as three independent single-track tasks, each in its own
batch; no production Wishlist row or UI scheduler was exercised.

All existing Quality Profile columns were compared with the production
database and matched exactly: Best Quality, no quality fallback, AcoustID
required and deep audio verification enabled. The added release import mode
retained its default `requested_tracks`. Every imported database row retained
user profile 1 and Quality Profile 1, and points into the test Library.

The existing Prowlarr configuration file supplied the current search API key;
the value stored in the production SoulSync configuration was stale. The new
SABnzbd used its own generated key and copied only NNTP server settings, with
two connections per server and an 8 MB/s download cap. It had no production
mounts, queue, scripts, notifications, RSS or schedules.

## Search matrix

For each song, five enabled Usenet indexers were queried with four forms:
artist + album + track, artist + album, artist + track, and track alone.
The recorded preflight matrix comprises 60 indexer requests and 1,471 raw hits.
Successful download runs made additional fresh searches.

| Request | Artist + track: hits / accepted releases | Track alone: hits / accepted releases | Album fallback |
| --- | --- | --- | --- |
| Daft Punk — Get Lucky | 35 / 2 | 254 / 2 | Found |
| Daft Punk — Instant Crush | 2 / 0 | 48 / 0 | Found |
| Pink Floyd — Money | 14 / 0 | 477 / 0 | Found |

For each Daft Punk album query, the final ranked pool contained 45 release
groups with 49 source sightings; four groups retained multiple indexers.
The same complete 2013 OBZEN release name with exact size 1,706,459,787 bytes
merged across two indexers. The indexer reporting 1,733,529,000 bytes retained
its own group. Drumless Edition 2023 releases stayed separate. Pink Floyd's
41 ranked groups remained separate where names differed in punctuation,
sizes differed or structured identity evidence was insufficient.

No Torrent indexers are configured, and these Usenet responses supplied no
Torrent infohash. Thus observed merging remains a conservative full-name,
exact-size and quality heuristic, rather than proof of identical bytes.
The actual worker's ranked candidates were checked against Quality Profile
ranking before dispatch; no release-group quota was applied.

The broad Get Lucky search also accepted a release by the cover artist
"Daft Punk Experience". It did not win the normal Best Quality album search
and was not downloaded or imported. A subsequent indexer-only matching fix
rejects this false positive using the artist credit instead of a substring
inside another band's name. Anonymized track and album examples cover both
plugins, including valid featured-artist credits. This fix was tested offline;
the live observations above describe the code before that follow-up.

## Download and import results

| Requested song | Actual selected file | Imported audio | Duration | Result |
| --- | --- | --- | --- | --- |
| Get Lucky | Album track 8 | FLAC, 24 bit, 88,200 Hz | 369.615 s | Imported; AcoustID PASS |
| Instant Crush | Album track 5 | FLAC, 24 bit, 88,200 Hz | 337.548 s | Imported; AcoustID PASS |
| Money | Album track 6 | FLAC, 24 bit, 176,400 Hz | 381.960 s | Imported; AcoustID PASS |

The two Daft Punk requests downloaded the 2013 OBZEN album independently;
Money downloaded the selected 24/176.4 album. Each successful request took
one download attempt, approximately 214, 208 and 260 seconds respectively.
The normal post-processing and import pipeline selected only the requested
song, applied metadata and wrote it under the copied user's test Library.
All three files were inspected with ffprobe, and independently decoded in
full with ffmpeg without errors. AcoustID logs contain three actual PASS
results. The test database and Library contain exactly these three tracks.
No additional album tracks or Wishlist entries were created.

## Alternate-source retrieval test

A further controlled test used the real, two-indexer OBZEN candidate with
size 1,706,459,787 bytes. An NZB retrieval error was injected at its first
endpoint. The normal monitor error path retried the task and selected the
second endpoint of the same release identity. The second indexer's real NZB
reached the paused test SABnzbd, with a nonzero parsed download size. The
release was not blocked. This test passed and exited 0; its job was removed
before downloading music payloads. The first endpoint's outage was simulated,
not an observed outage of that indexer.

## Failures and limits

The first run failed because the test downloader's 1 GiB memory cap was too
small. `/tmp` is tmpfs: downloaded and extracted file pages count against
the container memory limit. Docker confirmed OOM, and cgroup accounting
showed file/shmem pages dominating RAM. The test cap was corrected to 6 GiB,
with a 64 MiB cache and direct unpack disabled. A 2 GiB free-space reserve
remained enforced. The three subsequent downloads and imports completed.

After reporting all three completed imports, the one-shot backend process
exited 139. The kernel confirmed a Python segmentation fault. Its cause is
not established; this is not evidence of a clean runtime shutdown, nor proof
that the persistent production server has the same failure. The imported
files and database records were independently checked after the process exited.

The initial private SABnzbd test log recorded signed download URLs, and
failed-client diagnostics included encoded links. They were removed. Test
logging was then guarded before further downloads to redact plain and encoded
URLs, API query strings, credentials, and archive-password arguments. The final
logs and reports were checked against copied credentials and signed-link
patterns. Raw responses and private configuration were never added to Git.

The final check confirmed unchanged production database size and modification
time, unchanged production library/Wishlist/profile table counts, and an
unchanged production SABnzbd configuration hash. It also confirmed that the
tested backend source matched the feature worktree at that point. The own test queue
was empty, and the test containers and network were removed. The separate
test Library, private copied configuration and sanitized reports remain.

The follow-up also removes raw HTTP exception text from Prowlarr/SABnzbd
diagnostics, including exception chaining, so API keys and nested signed NZB
links are not exposed by those error paths. Synthetic credential-bearing
failures exercise the real client methods. A separately reproduced Usenet
cancellation race is fixed: a job ID arriving after cancellation is removed,
and late status, submit errors or file collection cannot restore an active
or successful state. These are offline regression tests, not evidence that
the unrelated exit-139 cause has been identified. AcoustID code is unchanged.

The enabled extra-album-track option, persistent UI/Wishlist scheduling,
Torrent downloads and genuine content-rejection retries were not exercised
live. Their feature coverage remains the documented offline regression tests.
The separate older library metadata repro was not run or changed.

Detailed sanitized observations, search matrix, download results, ffprobe
inspection and full-decode results remain in the test directory's `reports/`.
