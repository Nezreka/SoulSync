# Spec: Canonical Album Version (fixes #765 + #767-Bug2)

**Status:** design only — no code yet.
**Goal:** Pin ONE canonical `(source, album_id)` per album, chosen by best-fit to
the user's actual files, so the Library Reorganizer, Track Number Repair, and
tagging/enrichment all agree on the same release. Today each re-resolves
independently and they contradict each other (Spotify Believer=4 vs MusicBrainz
Believer=3; standard album mislinked to a deluxe release).

**Canonical-selection rule (decided):** *match the user's actual files.* The
canonical release is the candidate whose track count + per-track durations +
titles best fit what's on disk. Self-correcting: picks standard when you own the
standard, deluxe when you own the deluxe.

---

## Hard requirement: don't disrupt the running app

Every stage below is **additive and dormant until explicitly consumed**, and
every consumer **falls back to today's behavior when no canonical is set**. So:
- albums with no resolved canonical behave EXACTLY as they do now;
- each stage is independently shippable and reversible;
- nothing big-bangs.

---

## Stage 1 — Schema + pure scorer (ships dormant, zero behavior change)

### Schema (additive, nullable → migration-safe)
Add to `albums` (guarded `ALTER TABLE ... ADD COLUMN`, idempotent — mirror the
existing column-exists checks; see [[db-schema-review]] migration-safety notes):
- `canonical_source TEXT` — e.g. 'spotify' / 'itunes' / 'musicbrainz'
- `canonical_album_id TEXT`
- `canonical_score REAL` — best-fit score (for transparency / re-resolve gating)
- `canonical_resolved_at TIMESTAMP`

All nullable. Existing rows = NULL → "unresolved" → consumers fall back. No
backfill in this stage. No reads in this stage.

### Pure core helper (the testable heart) — `core/metadata/canonical_version.py`
```
score_release_against_files(file_tracks, release_tracks) -> float
pick_canonical_release(file_tracks, candidates) -> (best, score) | (None, 0)
```
- `file_tracks`: list of {duration_ms, title, track_number?} read from disk.
- `release_tracks`: a candidate release's tracklist (same shape).
- Scoring (tunable weights):
  - **track-count fit** — exact match strongly preferred; |Δcount| penalized.
  - **duration alignment** — greedily match each file to its closest release
    track by duration (within a tolerance, e.g. ±3s); reward coverage.
  - **title overlap** — token/fuzzy overlap as a tiebreaker.
  - **graceful degradation** — if a source gives no per-track durations, fall
    back to count + title only (never crash, never force-pick).
- Returns the best candidate + score, or (None, 0) when nothing clears a floor
  (so we never pin a bad guess — leave it unresolved, consumers fall back).

### Tests (extreme, like the rest of this codebase)
- standard (11) vs deluxe (17) with 11 files on disk → picks standard.
- same album, 17 files → picks deluxe.
- duration disambiguation when track counts tie (e.g. radio edit vs album).
- missing-duration source → count+title fallback still picks sanely.
- no candidate clears the floor → (None, 0).
- "Believer" standard(=track 3 listing) vs Spotify(=4) with the user's files →
  whichever the files actually match.

**End of Stage 1: scorer exists + tested, columns exist, NOTHING reads/writes
them yet. Provably zero behavior change.**

---

## Stage 2 — Resolver populates canonical (writes, still no consumers)

A function `resolve_canonical_for_album(album_id, db, ...)`:
1. Gather on-disk file metadata for the album (durations/titles) via the
   library's known file paths.
2. Gather candidate releases: every source the album has an ID for
   (spotify/itunes/deezer/discogs/soul/musicbrainz) AND — for the deluxe/standard
   case — sibling editions discoverable from those. Fetch each tracklist
   (cached, rate-limited).
3. `pick_canonical_release(files, candidates)` → store `(source, album_id, score)`
   on the album row if it clears the floor.

Wiring: a small **backfill repair job** (dry-run-capable) + a hook in enrichment
when an album is (re)enriched. Still **no tool READS canonical**, so behavior is
unchanged — this stage only populates the new columns. Reversible: clearing the
columns reverts to unresolved.

Tests: resolver picks the right release for the standard/deluxe fixtures; stores
nothing when below floor; idempotent re-resolve.

Cost note: fetching multiple candidate releases = more API calls. Mitigate via
cache + only-on-(re)enrich + the existing rate trackers. Surface in the job's
progress so it's not silent.

---

## Stage 3 — Reorganizer reads canonical (first real behavior change, gated)

In `library_reorganize._resolve_source`: if the album has
`canonical_source`/`canonical_album_id`, use THAT first; else fall back to the
current `get_source_priority` walk. One-line precedence change, fully gated on
non-NULL.

Tests: with canonical set → resolves to it; with canonical NULL → byte-identical
to today. Re-run the existing reorganize battery (148 tests) — must stay green.

**This alone fixes #767-Bug2** (a standard album whose files match the standard
release pins the standard, so reorganize stops targeting the deluxe folder).

---

## Stage 4 — Track Number Repair reads canonical (closes #765)

In `track_number_repair._resolve_album_tracklist`: add **Fallback -1** (before
everything) — if the album has a canonical `(source, album_id)`, use it. The
existing 6-level cascade stays as the fallback for albums with no canonical
(preserves its all-01-album rescue ability — the regression risk we refused to
take in the reactive fix).

Now both tools resolve the SAME release → same track numbers → no contradiction.

Tests: canonical present → both tools agree (shared-release test); canonical
NULL → existing cascade unchanged.

---

## Risks & mitigations
- **Extra API calls** (Stage 2 fetches multiple releases) → cache, rate-limit,
  only-on-(re)enrich, progress-logged.
- **Sources without per-track durations** → scorer degrades to count+title.
- **Schema migration** → additive nullable columns only; idempotent guards.
- **Wrong pick** → floor gate (never pin a low-confidence guess); `canonical_score`
  stored for inspection/re-resolve; manual override possible later.
- **Backward-compat** → every consumer falls back to today's path when NULL, so
  un-resolved albums (incl. all existing albums until backfilled) are unaffected.

## Out of scope (for now)
- Per-album manual version override UI (can layer on later — the columns support it).
- Merging the two tools into one (the reporter's alt suggestion) — unnecessary
  once they share the canonical.

## Suggested order to build
1, then 2, then 3, then 4 — each shippable and verifiable on its own. We can stop
after any stage and the app is consistent (just with fewer consumers wired).
