# The MetaSync metadata depot

SoulSync's MetaSync node can ask the metadata network about artists,
albums and tracks: every online node that holds an item answers with
what it knows, and your node merges the answers into one rich record —
the complete tracklist, every provider ID anyone has matched, ISRCs,
durations, year, genres.

**That record is never written into your library on arrival.** It
lands in the **depot**: a separate store inside SoulSync that holds
network-collected metadata as labelled candidates, under its own
management. The library stays yours; the depot is where network data
waits until something earns it a place.

## How depot data earns its place

SoulSync's own workflows consult the depot — chiefly **tagging during
post-processing**, which checks the depot before it runs any live
provider search. A live search still happens for everything the
depot can't supply. When the tagging pipeline fills a field from the
depot, that is a **promotion**, and promotion has a floor that peer
agreement alone never satisfies:

- **(a) Strong-verified.** Your own node cross-checked the value
  against the providers itself, and the check is less than 90 days
  old.
- **(b) Confirmed locally.** SoulSync spends one provider fetch on
  the candidate, by its own ID, and the answer agrees with your own
  data on a real key: an ISRC, a MusicBrainz ID, or album + track
  position. Agreeing on the *name* or the *duration* never counts —
  a remaster, a re-recording and a clean/explicit twin share both.
  The fetch confirms only the candidate whose ID it was spent on —
  never a different candidate, and never a genre (one fetch is not
  about each value in a list).
- **(c) Single survivor, verified anchor.** One candidate remains
  after disambiguation, and a verified anchor picked it: a fetched
  provider link or a shared strong-verified MusicBrainz ID. The
  tracklist slot is *not* an anchor for provider IDs: a track
  sitting at the file's own disc and position in a verified
  tracklist proves *which track* this is, not that the ID a node
  attached to it is right — and on a niche item one answering node
  is automatically the single survivor. Provider IDs promote via
  (a), (b) or the settlement above. The slot anchor returns when
  the per-slot provider ID recorded at verification time can be
  checked against the candidate.

A value other nodes merely *agree* on ("3+ nodes" in the depot) is
shown and stored as a candidate, but **never promoted on agreement
alone**. A value that itself came from the network (zero standing
support) can only ever promote under (a) — your node's own check.

Promotion fills **empty fields only**. The network never overwrites
your data.

## The network mark

Every promoted value is marked in SoulSync's data as
network-derived, and the MetaSync export carries that mark (the
`network_fields` list per row). Your node therefore re-emits a
promoted value with source `network`: it counts zero support on
every other node and is never re-served as own-library data. Without
the mark, one network answer promoted on three installs would come
back as three "independent" answers and launder itself into trust.

Marks are kept **per value**, not one per field: a field can hold
two network-derived values from one tagging run (the depot promotes
one ISRC, and the fetch steered by a promoted ID returns another),
and neither mark may evict the other. The export checks each mark
against the value in the very row being served — never another row
that happens to share the same Soul ID (a single and its album
version share one by design).

Fields fetched *by way of* a promoted ID are marked too: if tagging
takes a depot-promoted Deezer ID and fetches the track's details
with it, the ISRC that fetch returns inherits the mark. The same
goes for MusicBrainz: when MusicBrainz's own artist lookup comes
back empty and a depot-promoted artist ID is what gates the
recording and release lookups, everything they land — recording
ID, release-group ID, year (on the album *and* the track, since
tracks export a year too), ISRC, genres — inherits the mark.

## Bounds, retractions, reconcile

- Depot entries are bounded like the node's cache: a candidate dies
  90 days after the depot last refreshed it from the node, and a
  strong-verified tier lapses 90 days after it was earned unless the
  node pushes a fresh verdict.
- Anything the depot doesn't recognize — a verification tier or
  support bucket from a newer (or broken) node — is stored at the
  weakest reading, unverified with zero support. Unknown never
  fails open.
- When the node learns an answer died — a revocation, an expiry, a
  superseding answer — it pushes that death to the depot
  (`POST /api/v1/metasync/depot/invalidate` and `/records`). The
  depot flags the dead fields and stops promoting from them
  immediately. A value already promoted into your library or a
  file's tags is **not** rewritten by the network; the flag stops
  future promotions, and fixing written tags is your library's own
  business, on its own schedule.
- Whenever SoulSync reconnects to the node (the daily MetaSync
  refresh proves reachability), a **reconcile** runs: every depot
  subject is re-read from the node, dead ones are dropped, live
  ones refreshed. You can run it any time with
  `POST /api/metasync/depot/reconcile`; `GET /api/metasync/depot`
  shows depot counts.

## Turning it off

Set `metasync.depot_enabled` to `false` in SoulSync's config.
Tagging then behaves exactly as it did before the depot existed,
and the depot's contents simply sit unused. SoulSync works fully
with the network absent — the depot is an optimisation, never a
dependency.

## Known limits of this step

- **Duration** is held in the depot but not promoted into library
  records yet: the network's unit is milliseconds and SoulSync's
  stored durations have mixed-unit history, so a wrong-unit
  promotion would be worse than none.
- The album export now includes `musicbrainz_release_group_id`
  (the design always assumed it was served). Older nodes ignore
  the extra field.
- The node's claim builder picks up `network_fields` in its own
  follow-up; until then the bit is emitted and recorded, ready.
- The detail-page jump-start and the combined search tab build on
  this depot next; they are not part of this step.
