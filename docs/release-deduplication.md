# Cross-indexer release deduplication

The implementation uses a release identity for Torrent/Usenet rows, separate
from the duration-based recording identity used by other sources. The original
Prowlarr release title is retained; parsed artist/album labels are never used
as release identity.

## Evidence inspected on 2026-10-08

Five enabled Usenet indexers were each queried once through the existing
Prowlarr installation (v2.6.5.5623). The searches returned 127 results.
No `infoHash`, `magnetUrl` or `releaseHash` was present. `guid`, `downloadUrl`,
`infoUrl`, `title`, `size`, `publishDate` and categories were present.
There were three cross-indexer groups with the same complete title and exact
size. Many identical complete titles had different sizes, including rounded
sizes on one indexer. Publication timestamps could differ for identical
name/size pairs. Those timestamps and indexer-local GUIDs are not identities.

The fixture in `tests/fixtures/prowlarr/release_identity.json` preserves five
observed entries: one scene release with two equal sizes and a different
rounded size, and a second release with an equal-size pair. Artists, albums,
groups, indexer names, GUIDs and all URLs were replaced. Categories, title
structure, sizes and publication times retain the observed evidence.
Credentials and signed download URLs were never persisted by the probe.

There were no configured Torrent indexers. Torrent hash cases are synthetic
and follow Prowlarr's public `ReleaseResource` mapping, which exposes
`torrentInfo.InfoHash` as `infoHash` and `torrentInfo.MagnetUrl` as `magnetUrl`:
https://github.com/Prowlarr/Prowlarr/blob/develop/src/Prowlarr.Api.V1/Search/ReleaseResource.cs
No live Torrent search or download was performed. The subsequent isolated
Usenet live run, including three real downloads/imports and an alternate-source
retrieval test, is described in [the live test report](release-deduplication-live-test.md).

## Identity and uncertainty

* A valid Torrent v1 BTIH is strong content evidence: 40-character hex or
  32-character base32, from `infoHash` or a magnet `xt=urn:btih:`. Magnets in
  either `magnetUrl` or `downloadUrl` are inspected. Contradictory or malformed
  supplied hashes disable deduplication; GUIDs and `releaseHash` are not BTIHs.
* Without BTIH, identity is a heuristic: the full original title (case and
  whitespace normalization only), exact positive byte size, and matching
  advertised quality evidence. A year plus a release format/media or scene
  marker is required. Bare artist/album names and missing sizes remain
  separate. There is no fuzzy matching or size tolerance.
* Different complete structured names remain separate even with a reported
  equal hash: conflicting edition/year/quality claims are not resolved by
  guessing. Generic display labels can share an otherwise consistent BTIH.
  Richer consistent original-title quality evidence is retained even when a
  preferred endpoint has sparse labels. Every endpoint's original name and
  categories remain intact; separate representative evidence supplies ranking
  and grab-quality checks. All source pairs in both groups are checked, so
  enrichment cannot erase an edition difference or bridge incompatible groups.
* Hashless rows cannot bridge distinct hashed rows. Protocols never merge.
  Unsupported hashes, title punctuation changes, repost overhead and rounded
  sizes can therefore leave genuine duplicates. This is intentional: the
  observed Usenet API cannot prove byte identity.

## Ranking, endpoints and retries

Grouping happens in the Prowlarr variant search and plugin projection, and
again in the ranked Best Quality pool for independently projected sightings.
The user's Quality Profile still determines release order; there is no group
quota. Candidate inspector/decision alternatives collapse releases before
applying their limits, including when a nested alternative endpoint won.

Each grouped release retains its endpoints. Raw source selection prefers a
usable URL and, for torrents, a seeded endpoint; indexer priority breaks ties.
The album picker applies its availability threshold to each endpoint; an
unknown seeder count is still eligible under the existing availability rules.
The server-side candidate store keeps download URLs behind opaque tokens.
Alternative candidates inherit the visible root's validated match decision.
An endpoint's successful start or failed attempt is tracked by both token and
release/indexer identity so a renewed signed URL does not reset the walk.

Retrieval/transfer errors leave other indexers eligible. The existing monitor
retry flow can walk a cached alternative or find it in a fresh search.
Quarantine/quality/integrity failures reject the stable release identity for
the task, so the same identified content is not downloaded once per indexer.
Torrent file-list quality rejection carries an explicit `content` failure kind
through `DownloadStatus` to the monitor. Unidentified releases retain existing
source-token retry behavior.

Album bundle grabs also try alternative endpoints when the initial fetch/add
fails, and stop on a Torrent file-list quality rejection. After a job is
accepted, the existing bundle poll/cleanup/fallback lifecycle is preserved.
The single Wishlist request's album song selection and optional extra-track
import pipeline are unchanged: extra tracks retain request profile/path/quality,
skip download, and cannot create a search/retry/Wishlist entry on failure.
The option still defaults to requested tracks only.

## Verification

The regression tests cover observed equal and unequal sizes, complete names,
separate same-group releases, REPACK/PROPER/editions/year/quality, hash conflicts
and missing evidence, protocol separation, preferred/unavailable/seeded
endpoints, fresh tokens, cached retries, root match evidence on alternatives,
content failures, album pre-grab fallback, quality ranking and summary limits.
Existing release import, album bundle, quality profile and UI tests were rerun.

A broader deduplication/album regression selection passed 569 tests before
the live-test follow-ups. The full UI suite subsequently passed 10,007 tests
in 530 files, including the five existing release-import-mode tests.
Ruff checks on every changed production module and `git diff --check` passed.
An in-memory comparison against both configured Prowlarr API keys found
neither in the changed files, fixtures or probe/test logs; fixture endpoints
use unsigned `.invalid` URLs.

The full offline run (`pytest -n4 -q --tb=short --maxfail=20`) passed
23,137 tests, skipped two and failed one. It emitted 1,400 warnings, including
existing deprecation and temporary-database/background-thread shutdown warnings.
The failure is independently confirmed on the unmodified baseline:
`tests/downloads/test_release_file_matching.py::test_duration_rejects_different_recording`.
It also fails in an isolated archive of unmodified `cf4d015ae` (a 12-second
fixture against a 2-second target is accepted by that commit's duration rules).
It was not changed as part of release deduplication. The unrelated offline
`tools/repro_library_tools_consistency.py` was not modified.

After that full run, final review added two cancellation cases covering a
cleared UI row while NZB submission is pending. The final 318-test selection
of Usenet/indexer clients, matching, release deduplication and file selection
passed after that last fix; the independently confirmed baseline duration
test was deselected. Late-job cleanup preserves the requested keep/delete
files choice even after clearing the cancelled entry. The other follow-ups
reject the observed cover-artist false positive and keep credentials and
signed NZB URLs out of Prowlarr/SABnzbd HTTP-error diagnostics. These changes
were tested offline and were not part of the earlier real downloads.
AcoustID production code and tests are unchanged.

Tests use isolated temporary databases/configuration. The native sandbox
blocked a minimal Python cross-thread async submission; the same isolated
repro succeeded outside the sandbox, so broad backend runs used that execution
mode. No production container was started, restarted or reconfigured; all
containers were already stopped when inspected.
