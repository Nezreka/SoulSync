# Stream / Player / Radio Revamp — Plan

Goal: bring the audio stream + media-player + radio system to Spotify/Apple-level polish and feature set. Target stack: **plain JS** (`webui/static/media-player.js`), not the React migration. Intended architecture direction: **multi-listener** (final call deferred to Phase 3; Phases 0–2 stay compatible either way).

Rule for every phase: kettui standard — importable/testable logic, seam-level + differential tests, break nothing, ship one reviewable phase at a time.

---

## Phase 0 — Make it provable (foundation, no user-visible change)

- [ ] **0a. Extract radio selection logic into testable `core/radio/`.** The algorithm (tier orchestration, cap math, dedup, tag parsing, SQL-condition building) is currently tangled with `cursor.execute` inside `database/music_database.py:get_radio_tracks` (~12756) — untestable without a live DB. Pull the pure decisions into `core/radio/selection.py`; the DB method keeps SQL execution but delegates the decisions. Differential-test: same inputs → same output as today.
- [ ] **0b. Centralize frontend player state.** ~10 scattered `np*` globals in `media-player.js` → one `PlayerState` object. Seam for every later frontend phase. No behavior change.

## Phase 1 — Polish / feel (frontend)

- [ ] Persistent queue across refresh (localStorage first; server-side in P3)
- [ ] Drag-to-reorder queue; duration + art per queue item
- [ ] Seek tooltip (hover timestamp); smoother progress
- [ ] Crossfade via dual-`<audio>` swap (honest approximation of gapless — true gapless impossible w/ single element)
- [ ] Full Media Session API (lockscreen / hardware transport keys)
- [ ] Keyboard shortcut overlay + fuller bindings

## Phase 2 — Smart radio (backend algorithm)

- [ ] Replace `ORDER BY RANDOM()` with real seeding: play-count + recency weighting, genre-adjacency, recently-played memory. Slots into the Phase-0a pure module → fully unit-testable (seed → expected ordering). Both radio buttons benefit (shared function).

## Phase 3 — Architecture (deepest, riskiest — listener decision lands here)

- [ ] Per-session (or multi-tenant) stream state — replaces the single global `stream_state` + 1-worker executor + single `Stream/` staging file (`web_server.py:747`).
- [ ] Server-side persistent queue (resume across devices/refresh).
- [ ] Final multi-listener vs single-listener scope decided here, with real usage in hand.

---

## Order of execution

0a (radio extraction) → 2 (smart radio) first: highest *visible* upgrade, backend-only, cleanest to prove, zero playback risk. Then 0b → 1 (polish). Then 3 (architecture) last.
