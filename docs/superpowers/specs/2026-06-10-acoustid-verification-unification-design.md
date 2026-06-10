# AcoustID Verification: Unify the Pipeline + Persist Verification Status

Date: 2026-06-10
Branch: `fix/import-folder-artist-override-optin`

## Problem

Audio verification logic is **duplicated** across two code paths that have
drifted apart, producing inconsistent results ("komische Fehler"):

- **Import time** — `core/acoustid_verification.py` (`verify_audio_file`,
  `_normalize` @ line 59, alias-aware artist sim, cross-script SKIP, version
  gate, duration guard). Keeps CJK characters; strips `()`/`[]`/`<>` (after the
  recent fix).
- **Library scan** — `core/repair_jobs/acoustid_scanner.py` (its OWN `_normalize`
  @ line 485, which strips **all non-ASCII** — kanji/IPA vanish — and has NO
  cross-script alias bridge / SKIP). A full library scan therefore false-flags
  correct cross-script tracks (e.g. anime OSTs credited `澤野弘之 <Vocal: …>`).

Two `_normalize`s + two decision paths = the same bug must be fixed twice and
they diverge. There is also no record of *how* a track passed verification, so a
force-imported or skipped-but-imported track looks identical to a cleanly
verified one.

## Goals

1. **One verification core** used by BOTH import and scan. Normalization,
   alias-aware comparison, cross-script handling, version gate, duration guard,
   and thresholds live in exactly one place.
2. **Persist a verification status** per track in the DB **and** as a file tag,
   so it survives DB resets / file moves and can drive the UI.
3. **Surface the status on the Downloads page** as an info badge.

## Consumer map (discovered 2026-06-10)

In scope (call the new core):
- `core/imports/pipeline.py:340-373` — the only caller of
  `AcoustIDVerification.verify_audio_file` (import path).
- `core/repair_jobs/acoustid_scanner.py` — the library-scan job.

Shared helpers (stay shared, unchanged; the core builds on them):
- `core/matching/artist_aliases.py::artist_names_match` / `best_alias_match`
- `core/matching/script_compat.py::is_cross_script_mismatch`
- `core/matching/acoustid_candidates.py::find_matching_recording`,
  `duration_mismatches_strongly`
- `core/matching/version_mismatch.py::is_acceptable_version_mismatch`

Status hook:
- `core/imports/version_mismatch_fallback.py` sets
  `context["_version_mismatch_fallback"]` when it force-accepts the best
  quarantined candidate after retries are exhausted → maps to `force_imported`.
  Dispatched from `core/downloads/task_worker.py` via `try_version_mismatch_fallback`.

Deliberately OUT of scope (consume a shared helper but serve other purposes;
NOT merged):
- `core/repair_worker.py::_album_fill_artist_names_match` — Album-Completeness
  auto-fill gate.
- `core/matching/album_context_title.py::_normalize` — trivial album-grouping
  normalize.
- `core/downloads/task_worker.py`, `core/matching_engine.py` — pre-download
  (Soulseek candidate) matching, not post-download verification.

## Design

### 1. Shared core — `core/matching/audio_verification.py` (new)

The single home for the verification *decision*. Pure where possible (no file
I/O, no DB) so it is unit-testable in isolation. Callers keep their own I/O
(fingerprinting, quarantine, finding creation, tag writing).

```
def normalize(text: str) -> str
    # lowercase; strip ()/[]/<> annotations; strip version (- Live, etc) +
    # featuring tags; KEEP CJK (\w under unicode); collapse whitespace.
    # Single source of truth — replaces acoustid_verification._normalize AND
    # acoustid_scanner._normalize.

def evaluate(
    expected_title, expected_artist, recordings, *,
    fingerprint_score, file_duration_s=None, aliases_provider=None,
) -> Outcome
    # Outcome(decision, title_sim, artist_sim, matched_title, matched_artist, reason)
    # decision in {PASS, SKIP, FAIL}.
    # Encapsulates: alias-aware artist sim, version-mismatch gate, duration
    # collision guard, cross-script SKIP, thresholds. Built on the shared helpers.
```

Thresholds (`TITLE_MATCH_THRESHOLD`, `ARTIST_MATCH_THRESHOLD`, `MIN_ACOUSTID_SCORE`)
move into the core as the single definition.

Caller mapping:
- **Import** (`acoustid_verification.verify_audio_file`): PASS → import +
  status `verified`; SKIP → import + status `unverified`; FAIL → quarantine.
- **Scan** (`acoustid_scanner._scan_file`): PASS/SKIP → no finding (optionally
  refresh the stored status); FAIL → create `acoustid_mismatch` finding.

`acoustid_verification.py` keeps `verify_audio_file` as the import-facing wrapper
(fingerprint lookup, MB enrichment, alias provider, returns `VerificationResult`)
but delegates the decision to `evaluate`. `acoustid_scanner.py` drops its private
`_normalize` and decision branches and calls `normalize` + `evaluate`.

### 2. Verification status

Values (the three the user selected; quarantined files are not imported, so they
carry no status):
- `verified` — clean AcoustID PASS.
- `unverified` — SKIP: cross-script / ambiguous / no AcoustID match. Imported,
  not hard-confirmed.
- `force_imported` — accepted via `version_mismatch_fallback` after retries
  exhausted.

### 3. Storage — DB + tag

- **DB:** new nullable column `tracks.verification_status TEXT` (idempotent
  migration in `database/music_database.py`). Set at import; refreshed by the
  scan. Mirrored onto the download record/context so the Downloads page can show
  it before a library re-scan.
- **Tag:** `SOULSYNC_VERIFICATION=<status>` written via `core/tag_writer.py`
  (Vorbis comment / ID3 `TXXX`). Travels with the file; the scanner reads it and
  may skip re-verifying an already-`verified` file (perf bonus) and to display.

### 4. Downloads page UI

A small, info-only badge per row sourced from the status:
- ✓ `verified` · ⚠ `unverified` · ⚑ `force_imported`.
Wired in `webui/static/downloads.js` (+ the download-status API payload).

### 5. Testing (TDD)

- Unit-test the core `normalize` (incl. `<>`/`()`/`[]`, CJK retention) and
  `evaluate` (Sawano/IPA cross-script → SKIP not FAIL; vocal-credit artist →
  alias 100%; version mismatch → FAIL; duration collision → no FAIL).
- Wire import + scanner to the core; their existing tests
  (`test_acoustid_skip_logic`, `test_acoustid_scanner`,
  `test_acoustid_version_mismatch`, `test_acoustid_error_reporting`,
  `test_acoustid_normalize_angle_annotations`) act as regression.
- New tests for the status: force-import → `force_imported`; PASS → `verified`;
  SKIP → `unverified`; tag written + read back; DB column populated.

## Rollout / risk

- Pure-core extraction is behaviour-preserving for the import path (existing
  tests pin it); the scan path *changes* (gains alias bridge + cross-script SKIP)
  — that is the intended fix.
- DB migration is additive (nullable column), safe on existing DBs.
- Tag writing reuses the existing `tag_writer` path; failures are non-fatal.
- Staged on the current branch with TDD; ships in the next image build.

## Out of scope (now)

Unifying the pre-download Soulseek matcher or the album-completeness gate. They
consume shared helpers but are different decisions; folding them in would widen
blast radius without serving this goal.
