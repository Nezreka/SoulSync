# AcoustID Verification Unification + Status Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make import-time verification and the library AcoustID scan share ONE decision core, and persist a per-track verification status (DB + file tag) surfaced on the Downloads page.

**Architecture:** Extract a pure `core/matching/audio_verification.py` (`normalize` + `evaluate`) built on the existing shared helpers (`artist_aliases`, `script_compat`, `acoustid_candidates`, `version_mismatch`). `acoustid_verification.verify_audio_file` (import) and `acoustid_scanner._scan_file` (scan) delegate the decision to it. A new `tracks.verification_status` column + `SOULSYNC_VERIFICATION` file tag record `verified` / `unverified` / `force_imported`; the Downloads page shows a badge.

**Tech Stack:** Python 3.11, pytest (`.venv/bin/python -m pytest`), mutagen, SQLite, vanilla JS webui.

**Spec:** `docs/superpowers/specs/2026-06-10-acoustid-verification-unification-design.md`

---

## File Structure

- **Create** `core/matching/audio_verification.py` — the shared `normalize()` + `evaluate()` decision core (pure, no I/O). Single source of truth for normalization, thresholds, alias-aware comparison, cross-script SKIP, version gate, duration guard.
- **Create** `core/matching/verification_status.py` — the status vocabulary (`VERIFIED`, `UNVERIFIED`, `FORCE_IMPORTED`) + `status_from_outcome(decision)` + `status_from_context(context)` mapping helpers. Tiny, pure, testable.
- **Modify** `core/acoustid_verification.py` — `_normalize`/`_similarity`/`_alias_aware_artist_sim`/`_find_best_title_artist_match` and the decision branches in `verify_audio_file` delegate to the core. Keep the import-facing wrapper (fingerprint lookup, MB alias provider, `VerificationResult`).
- **Modify** `core/repair_jobs/acoustid_scanner.py` — drop the private `_normalize` (line 485) and the inline decision; call `normalize` + `evaluate`.
- **Modify** `database/music_database.py` — additive migration: `tracks.verification_status TEXT`.
- **Modify** `core/tag_writer.py` — write `SOULSYNC_VERIFICATION` in `_write_vorbis`/`_write_id3`/`_write_mp4`; read it in `read_file_tags`.
- **Modify** `core/imports/pipeline.py` — compute + persist the status (DB + tag) after post-processing; stash on the download context.
- **Modify** `web_server.py` + `webui/static/downloads.js` + `webui/index.html` — surface the status badge.

---

## Task 1: Core `normalize()`

**Files:**
- Create: `core/matching/audio_verification.py`
- Test: `tests/matching/test_audio_verification_core.py`

- [ ] **Step 1: Write the failing test**

```python
from core.matching.audio_verification import normalize

def test_normalize_strips_paren_bracket_angle_and_keeps_cjk():
    assert normalize("澤野弘之 <Vocal: MIKA KOBAYASHI>") == "澤野弘之"
    assert normalize("Clarity (Live at X) [Remastered]") == "clarity"
    assert normalize("Attack on Titan <TV Size>") == "attack on titan"

def test_normalize_strips_version_and_featuring():
    assert normalize("In My Feelings - Instrumental") == "in my feelings"
    assert normalize("Song feat. Someone") == "song"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/matching/test_audio_verification_core.py -q`
Expected: FAIL — `ModuleNotFoundError: core.matching.audio_verification`.

- [ ] **Step 3: Write minimal implementation**

Port the current (already-correct) body of `core/acoustid_verification.py::_normalize`
verbatim into the new module (it already strips `()`/`[]`/`<>`/version/featuring and
keeps CJK via `\w`). Add the thresholds as module constants for later tasks.

```python
"""Shared audio-verification decision core (pure; no file/DB I/O).

Single source of truth for normalization + the PASS/SKIP/FAIL decision used by
BOTH import-time verification (core/acoustid_verification.py) and the library
scan (core/repair_jobs/acoustid_scanner.py).
"""
import re
from difflib import SequenceMatcher

MIN_ACOUSTID_SCORE = 0.80
TITLE_MATCH_THRESHOLD = 0.70
ARTIST_MATCH_THRESHOLD = 0.60

def normalize(text: str) -> str:
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r'\s*\([^)]*\)', '', s)
    s = re.sub(r'\s*\[[^\]]*\]', '', s)
    s = re.sub(r'\s*<[^>]*>', '', s)
    s = re.sub(r'\s+(?:feat\.?|ft\.?|featuring)\s+.*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*-\s*(?:vocal|instrumental|acoustic|live|remix|cover|clean|explicit|radio\s*edit|original\s*mix|extended\s*mix|club\s*mix)\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*-\s*from\s+.+$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/matching/test_audio_verification_core.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/matching/audio_verification.py tests/matching/test_audio_verification_core.py
git commit -m "feat(verification): shared normalize() core for import + scan"
```

---

## Task 2: Core `evaluate()` decision

**Files:**
- Modify: `core/matching/audio_verification.py`
- Test: `tests/matching/test_audio_verification_core.py`

`evaluate` reproduces the import-path decision (it is the richer of the two), so it
is behaviour-preserving for import and an upgrade for scan. Port the logic from
`acoustid_verification.verify_audio_file` Steps 4b–end (version gate via
`is_acceptable_version_mismatch`, alias-aware artist sim, secondary/scan match via
`find_matching_recording`, cross-script SKIP via `is_cross_script_mismatch`,
duration guard via `duration_mismatches_strongly`).

- [ ] **Step 1: Write the failing tests** (the three real-world cases)

```python
from core.matching.audio_verification import evaluate, Decision

REC = lambda t, a, d=None: {"title": t, "artist": a, "duration": d}

def test_cross_script_artist_with_vocal_credit_skips_not_fails():
    # Sawano / 澤野弘之 <Vocal: ...> + IPA title -> SKIP, never FAIL
    out = evaluate(
        "Call Your Name", "Sawano Hiroyuki",
        [REC("call your name", "澤野弘之 <Vocal: mpi & CASG>")],
        fingerprint_score=0.95,
        aliases_provider=lambda: ["澤野弘之"],
    )
    assert out.decision == Decision.SKIP

def test_clean_match_passes():
    out = evaluate("Xl-Tt", "Sawano Hiroyuki",
                   [REC("xl-tt", "澤野弘之")], fingerprint_score=0.95,
                   aliases_provider=lambda: ["澤野弘之"])
    assert out.decision == Decision.PASS

def test_genuine_wrong_song_fails():
    out = evaluate("Yellow", "Coldplay",
                   [REC("Rich Interlude", "Kendrick Lamar")], fingerprint_score=0.85)
    assert out.decision == Decision.FAIL
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/matching/test_audio_verification_core.py -k evaluate -q`
Expected: FAIL — `cannot import name 'evaluate'`.

- [ ] **Step 3: Implement `evaluate` + `Decision` + `Outcome`**

Add an `enum Decision {PASS, SKIP, FAIL}`, a dataclass `Outcome(decision, title_sim,
artist_sim, matched_title, matched_artist, reason)`, and `_alias_aware_artist_sim`
(ported from acoustid_verification). Port the decision sequence from
`verify_audio_file` lines 470–703 into `evaluate`, taking `recordings` +
`fingerprint_score` + `file_duration_s` + `aliases_provider` as params and
returning an `Outcome` instead of `(VerificationResult, msg)`. Reuse the existing
imports: `from core.matching.artist_aliases import artist_names_match, best_alias_match`,
`from core.matching.script_compat import is_cross_script_mismatch`,
`from core.matching.acoustid_candidates import find_matching_recording, duration_mismatches_strongly`,
`from core.matching.version_mismatch import is_acceptable_version_mismatch`, and
`MusicMatchingEngine().detect_version_type` for `_detect_title_version`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/matching/test_audio_verification_core.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add core/matching/audio_verification.py tests/matching/test_audio_verification_core.py
git commit -m "feat(verification): shared evaluate() PASS/SKIP/FAIL decision core"
```

---

## Task 3: Import path delegates to the core

**Files:**
- Modify: `core/acoustid_verification.py`
- Test (regression): `tests/test_acoustid_skip_logic.py`, `tests/test_acoustid_version_mismatch.py`, `tests/test_acoustid_normalize_angle_annotations.py`, `tests/test_acoustid_error_reporting.py`

- [ ] **Step 1: Run the existing suite to capture green baseline**

Run: `.venv/bin/python -m pytest tests/test_acoustid_skip_logic.py tests/test_acoustid_version_mismatch.py tests/test_acoustid_normalize_angle_annotations.py tests/test_acoustid_error_reporting.py -q`
Expected: PASS (baseline before refactor).

- [ ] **Step 2: Refactor**

In `core/acoustid_verification.py`: replace the bodies of `_normalize`/`_similarity`
with re-exports from the core (`from core.matching.audio_verification import normalize as _normalize, similarity as _similarity`). Replace the decision block in
`verify_audio_file` (Steps 4b–end) with a single call to `evaluate(...)`, then map
`Outcome.decision` → `VerificationResult` (PASS→PASS, SKIP→SKIP, FAIL→FAIL) and pass
`Outcome.reason` through. Keep the existing fingerprint lookup, MB enrichment, and
`_resolve_expected_artist_aliases` thunk (pass it as `aliases_provider`).

- [ ] **Step 3: Run regression suite**

Run: same command as Step 1.
Expected: PASS (unchanged behaviour for import).

- [ ] **Step 4: Commit**

```bash
git add core/acoustid_verification.py
git commit -m "refactor(verification): import path delegates to shared core"
```

---

## Task 4: Scanner uses the core (gains alias bridge + cross-script SKIP)

**Files:**
- Modify: `core/repair_jobs/acoustid_scanner.py`
- Test: `tests/test_acoustid_scanner.py` (regression) + new case

- [ ] **Step 1: Write the failing test** (cross-script track must NOT create a finding)

```python
# tests/test_acoustid_scanner_cross_script.py
def test_scanner_does_not_flag_cross_script_anime_ost(monkeypatch):
    # Build a scan over one track expected "Call Your Name" / "Sawano Hiroyuki"
    # whose fingerprint returns "澤野弘之 <Vocal: ...>"; assert findings_created == 0.
    ...
```

(Concrete construction mirrors `tests/test_acoustid_scanner.py` fixtures; assert the
mismatch branch is NOT reached.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_acoustid_scanner_cross_script.py -q`
Expected: FAIL — a finding IS created (current scanner strips non-ASCII, no alias).

- [ ] **Step 3: Refactor scanner**

Delete `_normalize` (line 485) and import `normalize` from the core. Replace the
`_scan_file` similarity + decision block (the `title_sim`/`artist_sim`/finding logic)
with a call to `evaluate(...)` passing `fp_result['recordings']`, `best_score`,
`file_duration_s`, and an `aliases_provider` (reuse the MB alias lookup, or pass the
DB-resolved aliases). Create a finding only when `Outcome.decision == Decision.FAIL`.

- [ ] **Step 4: Run new + existing scanner tests**

Run: `.venv/bin/python -m pytest tests/test_acoustid_scanner.py tests/test_acoustid_scanner_cross_script.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/repair_jobs/acoustid_scanner.py tests/test_acoustid_scanner_cross_script.py
git commit -m "refactor(scanner): use shared verification core; stop false-flagging cross-script"
```

---

## Task 5: Status vocabulary + mapping

**Files:**
- Create: `core/matching/verification_status.py`
- Test: `tests/matching/test_verification_status.py`

- [ ] **Step 1: Failing test**

```python
from core.matching.verification_status import (
    VERIFIED, UNVERIFIED, FORCE_IMPORTED, status_from_decision, status_from_context)
from core.matching.audio_verification import Decision

def test_decision_maps_to_status():
    assert status_from_decision(Decision.PASS) == VERIFIED
    assert status_from_decision(Decision.SKIP) == UNVERIFIED

def test_force_import_context_wins():
    assert status_from_context({"_version_mismatch_fallback": "instrumental"}) == FORCE_IMPORTED
    assert status_from_context({}) is None
```

- [ ] **Step 2: Run → fail.** `.venv/bin/python -m pytest tests/matching/test_verification_status.py -q`

- [ ] **Step 3: Implement** the three string constants + the two pure mappers.

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit** `feat(verification): status vocabulary + mappers`.

---

## Task 6: DB migration `tracks.verification_status`

**Files:**
- Modify: `database/music_database.py`
- Test: `tests/test_verification_status_migration.py`

- [ ] **Step 1: Failing test** — open a fresh DB, assert `verification_status` is in `PRAGMA table_info(tracks)`.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** following the existing additive pattern:

```python
cursor.execute("PRAGMA table_info(tracks)")
cols = [r[1] for r in cursor.fetchall()]
if 'verification_status' not in cols:
    cursor.execute("ALTER TABLE tracks ADD COLUMN verification_status TEXT")
```
(Place beside the other `tracks` ADD COLUMN migrations, e.g. near line 2372.)

- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(db): add tracks.verification_status`.

---

## Task 7: Write + read the `SOULSYNC_VERIFICATION` tag

**Files:**
- Modify: `core/tag_writer.py`
- Test: `tests/test_verification_tag_roundtrip.py`

- [ ] **Step 1: Failing test** — write a FLAC with `db_data={'verification_status': 'unverified', ...}`, read it back via `read_file_tags`, assert `tags['verification_status'] == 'unverified'`. (Build the FLAC with ffmpeg as in `/tmp` test harness; or reuse an existing tag-writer test fixture.)
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — in `_write_vorbis` set `audio['SOULSYNC_VERIFICATION']=[status]`; in `_write_id3` add `TXXX(desc='SOULSYNC_VERIFICATION', text=[status])`; in `_write_mp4` set `----:com.soulsync:VERIFICATION`. Read all three back in `read_file_tags` into key `verification_status`. Only write when `db_data.get('verification_status')` is set (non-fatal on error).
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(tags): persist SOULSYNC_VERIFICATION tag`.

---

## Task 8: Persist status at import (DB + tag + context)

**Files:**
- Modify: `core/imports/pipeline.py`
- Test: `tests/imports/test_import_verification_status.py`

- [ ] **Step 1: Failing test** — drive `post_process_matched_download` (or the helper that records the library row) with a PASS verification result and assert the track row's `verification_status == 'verified'`; with `context['_version_mismatch_fallback']` set assert `'force_imported'`.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — after verification, compute `status = status_from_context(context) or status_from_decision(verification_decision)`; thread it into `db_data` (so the tag write in Task 7 picks it up) and into the library-row write (`tracks.verification_status`); also stash `context['_verification_status'] = status` for the Downloads payload.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(import): record verification status (db+tag+context)`.

---

## Task 9: Downloads page badge

**Files:**
- Modify: `web_server.py` (download-status payload), `webui/static/downloads.js`, `webui/index.html` (badge styles if needed)
- Test: manual + a small JS-free assertion if a payload test exists

- [ ] **Step 1:** Add `verification_status` to the per-task download payload in `web_server.py` (read from `context['_verification_status']` / the task record).
- [ ] **Step 2:** In `downloads.js`, render a badge per completed row: ✓ verified / ⚠ unverified / ⚑ force-imported, keyed on `task.verification_status`.
- [ ] **Step 3:** Add minimal badge CSS in `index.html`/`style.css` mirroring existing status pills.
- [ ] **Step 4:** Manual verify: import one clean + one cross-script track; confirm ✓ and ⚠ badges.
- [ ] **Step 5: Commit** `feat(ui): show verification status badge on Downloads`.

---

## Final verification

- [ ] Run the full AcoustID + import suites:
  `.venv/bin/python -m pytest tests/test_acoustid_*.py tests/matching/ tests/imports/ -q`
  Expected: all PASS.
- [ ] Build + push image (per build-deploy loop) for live testing.

## Self-review notes

- Spec coverage: unify (Tasks 1–4), status values (Task 5), DB (6), tag (7),
  import wiring (8), UI (9) — all spec sections mapped.
- Behaviour-preserving for import (Task 3 regression-gated); intentional change for
  scan (Task 4 — the fix).
- Tasks 4, 8, 9 reference existing fixtures/APIs the executor must read live
  (`test_acoustid_scanner.py` fixtures, the download payload shape, `downloads.js`
  row render) — flagged as such rather than guessed.
