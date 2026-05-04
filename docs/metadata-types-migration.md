# Typed Metadata Migration Plan

## Why

Right now the metadata pipeline has no real contract about the shape
of data flowing between providers and consumers. Each provider
(Spotify, iTunes, Deezer, Tidal, Qobuz, MusicBrainz, AudioDB,
Discogs, Hydrabase) returns its own response shape, and consumer
code defensively extracts every field via fallback chains:

```python
def _build_album_info(album_data, album_id, album_name='', artist_name=''):
    images = _extract_lookup_value(album_data, 'images', default=[]) or []
    ...
    return {
        'id': _extract_lookup_value(album_data, 'id', 'album_id',
                                    'collectionId', 'release_id',
                                    default=album_id) or album_id,
        ...
    }
```

This pattern works but makes the codebase hard to extend safely:

- Adding a new provider means adding more keys to the fallback chains
  in every consumer file (currently ~150 call sites of
  `_extract_lookup_value` across the codebase).
- Fixing a bug in extraction means fixing it in N places.
- New consumers can't trust the data — they re-run defensive logic.
- Tests are theatre because the contract is "whatever shape happens
  to come in."

## What this PR adds

`core/metadata/types.py` defines the canonical typed dataclasses:

- `Album` — required fields: `id`, `name`, `artists`, `release_date`,
  `total_tracks`, `album_type`. Optional: `image_url`, `artist_id`,
  `genres`, `label`, `barcode`, `external_ids`, `external_urls`.
- `Track` — required fields: `id`, `name`, `artists`, `album`,
  `duration_ms`. Optional: track/disc number, image, ISRC, etc.
- `Artist` — required fields: `id`, `name`. Optional: image, genres.

Plus per-provider classmethod converters on `Album`:

- `Album.from_spotify_dict(raw)`
- `Album.from_itunes_dict(raw)`
- `Album.from_deezer_dict(raw)`
- `Album.from_discogs_dict(raw)`
- `Album.from_musicbrainz_dict(raw)`
- `Album.from_hydrabase_dict(raw)`

Each converter is the SINGLE place that knows that provider's wire
shape. Adding a new provider = adding one classmethod here and
nothing else needs to change.

`Album.to_context_dict()` returns the canonical dict shape SoulSync's
existing import / download pipelines expect — the bridge between
typed data and the current dict-passing internal API.

## What this PR DOES NOT do

This PR does not migrate any consumer. No behavior changes. The new
types and converters are pure additive — every existing code path
keeps using `_extract_lookup_value` exactly as before.

The reason: a single big-bang migration would be a 153-call-site
refactor with subtle behavior risk. Better to land the foundation
in isolation, prove the contract via tests, then migrate consumers
one at a time in follow-up PRs that are individually reviewable
and revertable.

## Migration roadmap

Numbered in suggested order. Each item is its own PR.

1. **Foundation (this PR).** Land `core/metadata/types.py` +
   converters + tests. Document migration plan.
2. **Migrate `_build_album_info`** in
   `core/metadata/album_tracks.py` — accept either a typed `Album`
   OR a raw dict. When it gets a typed Album, return
   `album.to_context_dict()`. When it gets a raw dict, normalize
   via the appropriate `from_<source>_dict()` based on the
   provided `source` argument. Reduces from 41 LOC of fallback
   chains to ~5 LOC of dispatch.
3. **Migrate `_build_single_import_context_payload`** in the same
   file — same pattern.
4. **Migrate Spotify client.** `SpotifyClient.get_album()` returns
   `Album` instead of raw dict. Internal callers update. Public
   API surface unchanged where it has to be (return both for one
   release, deprecate dict version).
5. **Migrate iTunes/Deezer/Tidal/Qobuz/Discogs/Hydrabase clients.**
   Same pattern. Each client's `get_album()` returns `Album`.
6. **Migrate consumers in `core/discovery/quality_scanner.py`,
   `core/imports/context.py`, etc.** Drop their fallback chains
   in favor of typed access.
7. **Add `Track` converters and migrate Track-shaped consumers.**
   Same pattern as Album.
8. **Add `Artist` converters and migrate Artist-shaped consumers.**
9. **Deprecate `_extract_lookup_value`.** Once no caller needs it,
   delete it.

Each PR is independently revertable. Behavior preserved at every
step.

## Acceptance criteria for this PR

- All converters produce a fully-populated `Album` from realistic
  provider response samples.
- Every required field is set even when source data is partial.
- `to_context_dict()` shape is identical across all six providers
  (pinned via cross-provider parametrized tests).
- No existing consumer is changed; existing tests pass unchanged.
- Cross-provider invariants (release_date format, album_type values,
  Discogs `(N)` stripping, iTunes artwork upgrade) are pinned.
