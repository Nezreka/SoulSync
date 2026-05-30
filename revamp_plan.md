# Stream / Player / Radio Revamp — Plan

Goal: bring the audio stream + media-player + radio system to Spotify/Apple-level polish and feature set. Target stack: **plain JS** (`webui/static/media-player.js`), not the React migration. Intended architecture direction: **multi-listener** (final call deferred to Phase 3; Phases 0–2 stay compatible either way).

Rule for every phase: kettui standard — importable/testable logic, seam-level + differential tests, break nothing, ship one reviewable phase at a time.

---

## Phase 0 — Make it provable (foundation, no user-visible change)

- [x] **0a. Extract radio selection logic into testable `core/radio/`.** DONE (commit cbc001e2). `core/radio/selection.py` owns parse_tags/merge_tags/same_artist_cap/build_like_conditions/RadioCollector; DB method delegates. 29 tests, refactor-equivalence proven (behavioral tests pass against old AND new).
- [ ] **0b. Centralize frontend player state.** ~10 scattered `np*` globals in `media-player.js` → one `PlayerState` object. Seam for every later frontend phase. No behavior change.

## Phase 1 — Polish / feel (frontend)

- [ ] Persistent queue across refresh (localStorage first; server-side in P3)
- [ ] Drag-to-reorder queue; duration + art per queue item
- [ ] Seek tooltip (hover timestamp); smoother progress
- [ ] Crossfade via dual-`<audio>` swap (honest approximation of gapless — true gapless impossible w/ single element)
- [ ] Full Media Session API (lockscreen / hardware transport keys)
- [ ] Keyboard shortcut overlay + fuller bindings

## Phase 2 — Smart radio (backend algorithm)

- [x] **Weighted ranking** DONE. Each tier now fetches a random POOL (4x, floored) and `core/radio/selection.rank_candidates` orders it by `score_candidate`: play_count + lastfm_playcount (log-damped), recently-played penalty, stable per-id jitter for run variety. Defensive column-probe → still works on a DB predating the play_count/lastfm migration. 43 radio tests; ranking math is deterministic-unit-proven; DB wiring shown via decoy-pool test (probabilistic by nature — documented).
- [ ] **Future (optional deepening):** wire `_recently_played` from `listening_history` (column + scorer support already exist; not yet populated in the query), genre-adjacency graph (currently exact-genre LIKE only).

## Phase 3 — Architecture (deepest, riskiest — multi-listener)

- [x] **3a. Stream-state store extracted + wired (foundation).** DONE. `core/streaming/state.py`: `StreamSession` (dict-compatible, own RLock) + `StreamStateStore` (named-session registry, lazy create, race-safe). `web_server.py` now binds `stream_state` to the store's DEFAULT session — behavior identical to the old single global (proven by call-site-compat + real-session worker tests). 33 streaming tests. This is the provable foundation multi-listener needs.
- [ ] **3b. Per-listener session id (the unprovable-here part).** Derive a session id per browser/device (cookie/header) and key `stream_state_store.get(session_id)` off it in the stream routes; per-session `Stream/` staging subdir; drop session on disconnect; bump `stream_executor` past max_workers=1. Needs live multi-client testing — do in a session where Boulder can drive 2+ clients. The store API (`get(id)`, `drop`, `active_ids`, per-session staging) is already built for it.
- [ ] Server-side persistent queue (resume across devices/refresh).

---

## Order of execution

0a (radio extraction) → 2 (smart radio) first: highest *visible* upgrade, backend-only, cleanest to prove, zero playback risk. Then 0b → 1 (polish). Then 3 (architecture) last.
