# Best-Quality Search Mode — Design

**Date:** 2026-06-14
**Branch:** (new, off current `fix/import-folder-artist-override-optin`)
**Status:** Approved approach (Option A), pending spec review

## Goal

Add a user-toggleable download strategy. Today hybrid search is **priority-first**:
`engine.search_with_fallback` walks the source chain in priority order and accepts
the **first** source that meets a quality target — so a passable Soulseek 16-bit FLAC
"wins" even when HiFi/Qobuz/Tidal could deliver a 24-bit version of the same track.

The new **best-quality** mode instead searches **all** configured sources, pools their
candidates, and works them **best→worst by actual audio quality**. Source priority
becomes only a tiebreaker between equally-good candidates.

## Hard constraints (from the user)

1. **Two independent toggles.** The new `search_mode` and the existing
   `post_processing.retry_exhaustive` are orthogonal. The feature must behave
   correctly in **all four** combinations (priority/best × default/exhaustive budget).
2. **Budget semantics preserved 1:1.** No change to how retries are counted:
   - Default mode: single global cap `MAX_QUARANTINE_RETRIES = 5` across all sources.
   - Exhaustive mode: per-source budget `query_count × retries_per_query`.
   When a source **completely spends** its budget, **all of that source's candidates
   are removed from the entire pooled list** (not just skipped once).
3. **Do not touch the query generator.** `matching_engine.generate_download_queries`
   and the legacy query-building block in `download_track_worker` stay exactly as-is.
4. **`force_import` never fires on quality** (unchanged existing invariant — only
   AcoustID mismatches can force-import).

## Key realization: the budget-removal mechanism already exists

When a source spends its budget, `monitor.requeue_quarantined_task_for_retry`
already adds it to the task's **`exhausted_download_sources`** set, and the worker
already passes that set as `exclude_sources` to its searches. Constraint #2's
"remove the source from the whole list" is therefore **not new logic** — best-quality
mode simply consults the *same* set when assembling/filtering its pooled candidate
list. No new budget bookkeeping is introduced.

## Architecture

### The one genuinely new piece: quality-dominant candidate ordering

`attempt_download_with_candidates` (core/downloads/candidates.py:71) sorts candidates
**confidence-first, then `quality_score`**. That is correct for priority mode (never
download a high-quality *wrong* file). For best-quality mode we keep the
"correct-first" guarantee — `get_valid_candidates` already drops candidates below the
match threshold, so **every candidate in the list is correctly matched** — but among
those valid candidates we want **profile quality rank to dominate**, with confidence as
the tiebreaker.

Add an opt-in parameter:

```python
attempt_download_with_candidates(task_id, candidates, track, batch_id=None,
                                 deps=None, *, quality_first=False)
```

- `quality_first=False` (default): today's sort, byte-for-byte unchanged.
- `quality_first=True`: sort key becomes
  `(profile_quality_rank, confidence, upload_speed, -queue_length, free_slots, size)`,
  i.e. profile quality dominates, all existing signals become tiebreakers.

`profile_quality_rank` is derived from the candidate's stamped `AudioQuality` against
the user's `ranked_targets` (reusing `core.quality.model` / `filter_and_rank` ordering).
Candidates with no usable quality info sort last (rank = worst), never dropped.

### New engine method: `search_all_sources`

Mirror of `search_with_fallback`, but it does **not** stop at the first satisfying
source. It iterates every configured, non-excluded source in the chain, runs each
source's `search`, and returns the **combined** raw track list (each track already
quality-stamped by its client's `set_quality`, as today). Per-source exceptions are
swallowed exactly like the existing method. Excluded (`exhausted`) sources are skipped.

```python
async def search_all_sources(self, query, source_chain, timeout=None,
                             progress_callback=None, exclude_sources=None)
        -> Tuple[list, list]   # (combined_tracks, combined_albums)
```

Quality ranking is **not** done here — the orchestrator/worker owns final ranking, same
division of labour as today (engine returns raw, orchestrator filters/ranks).

### Orchestrator wiring

`download_orchestrator.search(...)` gains awareness of `search_mode`:
- `priority` (default) → unchanged: `search_with_fallback`.
- `best_quality` **and** `mode == 'hybrid'` → `search_all_sources`.
- Single-source mode → unchanged regardless of `search_mode` (only one source exists;
  its candidates are still quality-ranked downstream, so the toggle is a no-op there).

A thin helper `load_search_mode() -> str` reads the quality profile (see Config).

### Worker integration (the per-query loop stays intact)

In `download_track_worker`'s existing query loop (core/downloads/task_worker.py:398),
when best-quality mode is active:

1. The loop and the query generator are untouched.
2. `orchestrator.search(query, exclude_sources=_exclude_sources)` now returns
   **pooled** results from all non-exhausted sources (via `search_all_sources`).
3. `candidates = get_valid_candidates(pooled, track, query)` — unchanged.
4. `ranked, _ = rank_for_profile(candidates)` — orders best→worst by profile quality.
5. `attempt_download_with_candidates(task_id, ranked, track, batch_id, quality_first=True)`.

Because the pool already came from every source, the **hybrid-fallback block**
(task_worker.py:529–593, which re-tries `hybrid_order[1:]` individually) is
**redundant in best-quality mode** and is skipped — it would just re-search sources the
pool already covered. In priority mode it runs exactly as today.

The `exhausted_download_sources` set is already folded into `_exclude_sources`
(task_worker.py:446), so a budget-spent source is excluded from `search_all_sources`'s
pool automatically — satisfying constraint #2 with no new code. A belt-and-suspenders
filter drops any straggler exhausted-source candidates before step 5.

## Config / persistence

`search_mode` belongs to the quality profile (it *is* a quality-driven policy). Stored
in the v3 quality-profile JSON (`database.music_database.get_quality_profile`):

```json
{ "version": 3, "search_mode": "priority", ... }
```

- Default: `"priority"` (preserves today's behavior for every existing install).
- Accessor: extend `core/quality/selection.py` with `load_search_mode() -> str`
  returning `'priority'` unless the profile says `'best_quality'`.
- No migration needed: a profile lacking the key reads as `'priority'`.

## UI

A single toggle in the existing **Quality Profile** tile (webui/index.html +
webui/static/settings.js), e.g. a labelled switch:

> **Search strategy:** ( ) Source priority — fastest, stops at first good source
> (•) Best quality — searches all sources, picks the highest quality

With a short note that best-quality searches **all** sources every track (slower / more
API calls). `collectQualityProfileFromUI` emits `search_mode`; `populateQualityProfileUI`
reads it. No change to the ranked-targets editor or the fallback toggle.

## Behaviour matrix (all four combinations must hold)

| search_mode | retry_exhaustive | Behaviour |
|---|---|---|
| priority | off | **Unchanged today.** First satisfying source wins; 5 global retries. |
| priority | on | **Unchanged today.** First source wins; per-source budgets. |
| best_quality | off | Pool all sources, best→worst; 5 **global** retries total. A source can't be "removed from the pool" because there's no per-source budget — the global cap fires first. (Documented: combine best-quality with exhaustive for per-source removal.) |
| best_quality | on | Pool all sources, best→worst; each source has `query_count × retries_per_query`; on spend → source removed from the whole pool via `exhausted_download_sources`. **This is the user's target configuration.** |

## Error handling

- `search_all_sources`: fail-open per source (swallow exceptions, keep going), identical
  to `search_with_fallback`. If the whole pool is empty, the worker falls through to its
  existing not-found handling.
- Quality-rank computation: any candidate that can't be ranked sorts last, never crashes
  the walk (mirrors the engine's existing fail-open ranking).

## Testing (TDD)

New tests under `tests/quality/` and `tests/downloads/`:

1. **`load_search_mode`** — defaults to `'priority'`; returns `'best_quality'` when set;
   unknown value falls back to `'priority'`.
2. **`search_all_sources`** — returns combined candidates from multiple fake sources;
   skips excluded/exhausted sources; swallows a raising source; empty when all empty.
3. **quality-first ordering** — `attempt_download_with_candidates(quality_first=True)`
   tries a 24-bit candidate before a higher-confidence 16-bit one; `quality_first=False`
   reproduces today's confidence-first order exactly (regression lock).
4. **budget-removal in pool** — given an exhausted source, its candidates never appear in
   the walked list, while other sources' candidates remain (exhaustive mode).
5. **toggle independence** — orchestrator chooses `search_all_sources` only when
   `search_mode == best_quality and mode == hybrid`; single-source and priority paths
   call `search_with_fallback` (or single-client search) unchanged.

Each test written first, watched fail, then minimal code to green. No production code
before a failing test.

## Files touched

- `core/quality/selection.py` — add `load_search_mode()`; small quality-rank helper for the sort key (or reuse model).
- `core/download_engine/engine.py` — add `search_all_sources`.
- `core/download_orchestrator.py` — branch `search()` on `search_mode`.
- `core/downloads/candidates.py` — add `quality_first` param + alternate sort key.
- `core/downloads/task_worker.py` — pass pooled results + `quality_first=True`; skip the redundant hybrid-fallback block in best-quality mode. (Query generator block untouched.)
- `database/music_database.py` — include `search_mode` default in the v3 profile.
- `webui/index.html`, `webui/static/settings.js` — the toggle.
- `tests/quality/`, `tests/downloads/` — the tests above.

## Explicitly out of scope

- No change to the query generator.
- No change to the budget counters / `MAX_*` constants / `requeue_quarantined_task_for_retry`.
- No change to AcoustID / force_import behavior.
- No cross-query pooling: best-quality pools across **sources** within each query of the
  existing loop. The primary query already covers all sources, so this captures the best
  candidate without restructuring the loop. Later queries remain per-source fallbacks for
  the empty-result case.
