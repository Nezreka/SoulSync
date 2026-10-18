# Automatic standard-edition album completion

Album Completeness now has an opt-in **Standard Only** mode. It discovers a
standard release, compares its full tracklist with the library database, and
queues only missing songs through the existing wishlist. It does not move,
copy, retag, rename, delete, or replace existing library files, or pin/change
canonical metadata. Normal downloads still use the configured importer.

## First preview

Use a separate test installation and a small copied library/database first.
Do not point a development instance at a production library or database.

1. Open Tools → library maintenance → Album Completeness settings.
2. Set **Standard Only** on and leave **Dry Run** on (the default).
3. Set **Min Owned Tracks** to **1** to include albums introduced by one playlist
   song. Leave **Min Completion %** at **0**. **Min Tracks For Check** defaults to
   3; lower it only if very short albums should qualify.
4. Save, then run the job manually. Expand its **Standard Album Plans** findings.
   Plans show artist/album, selected release, metadata source and release ID,
   expected count, existing matching songs, missing songs, proposed downloads,
   and skip reasons. The progress log also contains each complete JSON plan.
5. Check the reports before changing any download settings. Dry run does not add
   wishlist rows, cache expected counts in the music database, or touch music
   files. The automation framework records run history and preview findings;
   provider clients may maintain their normal metadata caches.

The settings are stored under `repair.jobs.album_completeness.settings`:

```json
{
  "standard_only": true,
  "dry_run": true,
  "min_owned_tracks": 1,
  "min_tracks_for_check": 3,
  "min_completion_pct": 0
}
```

## Automatic operation after preview

Enable Album Completeness and choose its interval or automation schedule. When
ready, turn Dry Run off while keeping Standard Only on. Enable the existing
wishlist automatic processing and normal download/import automation. Keep your
existing download priority configured as Deezer followed by Soulseek; this
feature does not change source priority or require a particular metadata source.

A playlist import becomes eligible once normal library sync has put its song in
SoulSync's database. The next scheduled Album Completeness run discovers the
album, including rows without an external album ID. No playlist event hook is
needed: this reuses the existing scheduled scan. Set a suitable interval after
normal sync/import rather than expecting immediate completion on playlist update.
The usual server scan makes successful imports visible in Navidrome.

Requests retain album provenance and the album owner's profile. They carry no
playlist ID and never change playlist membership. Wishlist's persistent
track/album/profile keys deduplicate repeated scans. Completion also checks
already-requested songs by artist, title and edition group across provider IDs,
and never overwrites an existing playlist request's provenance or quality choice. A dedicated marker keeps
these requests out of whole-album bundle searches, so a missing-song request
cannot widen into a deluxe download. The wishlist worker retains requests until
its normal verified-publish cleanup and uses its existing retry/queue handling.

To stop new completion requests, turn the maintenance job off or turn Dry Run
back on. Already queued wishlist requests remain governed by the wishlist's
existing controls.

## Selection and safety

The canonical resolver answers a different question: which release best fits
existing files? Its count/duration/title scoring can correctly pin a deluxe
release when the local library is deluxe. That pin is not proof of an original
standard tracklist. Standard Only does not trust it, even when manually locked;
it neither edits the pin nor downloads from it.

Standard Only uses the active metadata provider's existing artist-catalogue and
exact-release APIs, and the shared edition-grouping logic. It requires:

- An edition-group title match and independently verified exact artist/release
  metadata, a nonempty complete tracklist matching the provider's expected count,
  source track IDs, and contiguous unique disc/track positions.
- An original/unlabelled standard candidate. Deluxe, expanded, anniversary,
  bonus, special, extended and other recognised special editions are excluded.
- Agreement between the ordered song titles of all eligible standard/remaster
  candidates. Remasters are not automatically extended; they may corroborate an
  original standard list, but a remaster-only catalogue is skipped.
- No conflicting regional tracklist or potentially truncated provider catalogue.

This is deliberately stricter than Watchlist's existing "shortest edition"
choice: shortest alone is insufficient evidence. The shared reducer supplies
`watchlist.prefer_explicit_edition` as a tie-break among equivalent standard
releases. Watchlist's `all` / `one_complete` choices do not permit bonus tracks in
Standard Only. Original candidates take precedence over named remasters.

Ownership uses normalized exact song titles and duration agreement within five
seconds, one-to-one, across same-artist edition rows in the same owner profile.
Track number alone never proves ownership. Standard songs in a local deluxe row
are reused logically; bonus songs do not inflate completion. Unknown duration
or conflicting versions of a matching song cause a skip instead of a duplicate.
No existing deluxe files are reorganized into a standard folder.

Metadata failure, absent full counts, uncertain identity, ambiguous regional
variants and inconsistent editions all produce explicit skip reasons, with no
wishlist additions. Neither local observed counts nor `api_track_count` can
stand in for a verified standard tracklist. Skipped albums are reconsidered on a
later run when metadata may have improved.

## Existing behaviour and the reported anniversary album

For backward compatibility, Standard Only defaults off; Album Completeness
itself still defaults disabled. **Enable Standard Only before using this feature.**
The legacy completeness scan and its **Fill Album** action remain available and
can use canonical editions and copy/move/retag local files. Old incomplete-album
findings are not converted. Do not use their Fill Album / bulk fix action as a
way of applying a standard preview. Standard previews are report-only findings;
a live scheduled run re-resolves fresh metadata before adding wishlist tracks.

The initial inspected fork baseline was `dc53eba3e956a9ce65d2c6f90da58e9455c6383f`;
the feature was subsequently rebased onto upstream `dev` at
`a2aa92e76e2344dec2710a388244b79369074216`.
Its legacy scan already skips an unavailable canonical tracklist (expected total
becomes zero); unpinned albums can still use cached expected counts. The supplied
older log about using a local stored count does not establish that anniversary
tracks were downloaded. A canonical score of 1.0 describes a fit to local files,
not standard-edition confidence. This implementation never interprets that
score or the anniversary album's 20 local/cached tracks as standard evidence.
No production database or historical download records were inspected.

## Limitations and follow-up

- Discovery is deliberately bounded to 50 artist albums on the active metadata
  source. A result at that boundary is skipped. Providers that silently return a
  shorter partial catalogue cannot be detected through the current shared API.
  Full pagination/completeness status in that API is a useful follow-up.
- Provider metadata can be wrong or omit edition labels; agreement within one
  catalogue is not independent cross-provider verification. Unknown regional
  variants or bonus tracks concealed by incorrect metadata remain a provider
  data limitation. Conflicting visible evidence is always skipped.
- Exact title matching is conservative: renamed tracks, missing durations,
  remaster-only catalogues and spelling changes may skip a valid album. There
  is no fuzzy number-only fallback.
- Completion is scheduled, library-wide and eventual, not limited to the latest
  playlist or triggered instantly. The minimum-owned threshold applies equally
  to watched artists in this mode. Metadata-locked albums are excluded.
- Full standard songs may remain distributed across existing edition folders;
  Navidrome may display those as separate edition albums. Producing one physical
  standard folder would require copying/retagging existing tracks and is outside
  this non-destructive feature. No duplicate standard songs are downloaded to
  force a merged Navidrome album view.
- The normal importer/download matchers and source availability determine whether
  a queued song can be acquired. This feature does not certify live provider or
  Navidrome behaviour; automated tests use synthetic data and temporary databases.

## Fixture example

```text
Artist: Fixture Artist
Local album: Fixture Album (Deluxe Edition)
Selected standard release: Fixture Album
Source: deezer  Release ID: 0
Expected: 3
Existing matching: First
Missing: Second, Third
Proposed downloads: Second, Third
Mode: Dry run — no wishlist additions or music-file changes
```

See `tests/metadata/test_standard_completion.py` and
`tests/test_standard_album_automation.py` for synthetic releases and the real
inventory-scan tests. The latter includes a one-song import with no external
album ID, repeated live scans, owner routing, metadata failure and a byte-for-byte
dry-run database comparison with no created music directory.
