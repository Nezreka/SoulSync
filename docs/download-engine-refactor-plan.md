# Download Engine Refactor Plan

## Goal

Mirror Cin's "metadata engine" architecture for the download dispatcher. Move shared logic OUT of the per-source clients (currently 1600+ LOC of duplicated thread workers, search retry ladders, rate-limiters, state machines) and INTO a central `DownloadEngine`. Clients become dumb: make raw API requests + manage their own auth state. Everything else is the engine.

This is the SAME architectural smell Cin flagged on the metadata layer, applied to downloads. If we keep adding sources (usenet planned + likely more), the only honest fix is to stop reinventing the wheel per client.

## Architecture target

```
┌─────────────┐                                                   ┌──────────────────┐
│   feature   │ ── search/download ──▶  ┌────────────────────┐ ─▶│ Soulseek (raw)   │
│             │ ◀── normalized ────────  │  DownloadEngine    │ ─▶│ YouTube (raw)    │
└─────────────┘                          │                    │ ─▶│ Tidal (raw)      │
                                         │  ◆ thread workers  │ ─▶│ Qobuz (raw)      │
                                         │  ◆ rate-limit pool │ ─▶│ HiFi (raw)       │
                                         │  ◆ search retry    │ ─▶│ Deezer (raw)     │
                                         │  ◆ quality filter  │ ─▶│ SoundCloud (raw) │
                                         │  ◆ state tracking  │ ─▶│ Lidarr (album)   │
                                         │  ◆ fallback chain  │   └──────────────────┘
                                         │  ◆ cache           │   clients only do:
                                         └────────────────────┘   - raw API request
                                                                   - auth/token state
```

## What clients keep (legitimately per-source)

- Auth flow + token refresh (Tidal OAuth, Qobuz session, Deezer ARL, slskd API key, etc.)
- Source-specific protocol (slskd events vs HTTP REST vs HLS demux vs Blowfish decrypt vs yt-dlp subprocess)
- Source-specific search query shape (free text vs keyword filters vs MusicBrainz ID lookup)
- Source-specific "download a thing" atomic operation (`_download_impl(target_id) → file_path`)

## What moves into the engine

| Today (per-client, duplicated) | Tomorrow (engine, single source of truth) |
|---|---|
| `self.active_downloads = {}` per client | `engine.active_downloads = {}` |
| `self._download_lock = Lock()` per client | `engine.state_lock = Lock()` |
| `self._download_semaphore = Semaphore(...)` per client | `engine.download_pool` (per-source semaphore from registry) |
| `self._last_download_time / _download_delay` per client | `engine.rate_limiter.acquire(source)` |
| `_download_thread_worker` × 7 (~70 LOC each) | `engine.dispatch_download(plugin, target_id)` |
| Search retry ladder × 7 | `engine.search(query)` with shared retry policy |
| Quality filter × 7 | `engine.filter_by_quality(results, prefs)` |
| Result dedup × 7 | `engine.dedup(results)` |
| Hybrid fallback (search only) | `engine.fallback_chain(operation)` (search AND download) |

## New plugin contract (much smaller)

```python
class DownloadSourcePlugin(Protocol):
    # Identity
    name: str

    # Lifecycle
    def is_configured(self) -> bool: ...
    async def check_connection(self) -> bool: ...
    def reload_settings(self) -> None: ...

    # Search — DUMB. Just hit the API.
    async def search_raw(self, query: str) -> List[RawSearchResult]: ...

    # Download — DUMB. Just download the bytes to a file.
    # The engine handles thread spawning, state tracking, rate limits.
    # Plugin returns the final file path on success or raises.
    async def download_raw(self, target_id: str, dest_dir: Path) -> Path: ...

    # Cancel — best-effort. Engine handles state cleanup.
    async def cancel_raw(self, target_id: str) -> bool: ...
```

Compare to today's plugin protocol (which my Phase 0 PR introduced) — that one was wrapped around fat clients. This one is dumber. Clients shrink dramatically (estimated 40-60% LOC reduction per file).

## Special cases

- **Soulseek** — slskd is event-driven, NOT thread-based. Engine's BackgroundDownloadWorker doesn't apply. Keep Soulseek's path special: `download_raw` returns immediately; engine subscribes to slskd events for state updates instead of running a thread.
- **YouTube/SoundCloud** — yt-dlp is a subprocess. The "thread" is really `subprocess.run(['yt-dlp', ...]).wait()`. Engine handles thread; plugin's `download_raw` just runs subprocess and returns file path.
- **Lidarr** — album-grabber, not track-grabber. Different contract. Either separate `AlbumOnlyPlugin` interface OR plugin declares `supports_track_search: bool = False`. Decide during migration.

## Phased commit plan

Each phase is one or more commits. Each commit independently revertable. Tests stay green between commits — never ship a half-broken state.

### Phase A — Behavior pinning tests (BEFORE any code moves)

**Goal:** Baseline tests for what each source's download path currently does. Catches regressions during extraction.

**Commit A1:** `tests/downloads/test_soulseek_download_path.py` — pin Soulseek's download lifecycle (search → download → completion → file path returned).
**Commit A2:** Same for YouTube. Mock yt-dlp subprocess.
**Commit A3:** Same for Tidal. Mock tidalapi.Session.
**Commit A4:** Same for Qobuz. Mock Qobuz REST API.
**Commit A5:** Same for HiFi. Mock hifi-api instance.
**Commit A6:** Same for Deezer. Mock Deezer GW API + Blowfish stream.
**Commit A7:** Same for SoundCloud. Mock yt-dlp scsearch.
**Commit A8:** Same for Lidarr. Mock Lidarr REST API.

After Phase A: ~50 new tests pinning current behavior. We can refactor with confidence.

### Phase B — Engine skeleton + state lift

**Commit B1:** Create `core/download_engine/` package with `DownloadEngine` class. Engine starts EMPTY — just exposes `register_plugin(plugin)`, `active_downloads` dict, `state_lock`. Orchestrator gets a `self.engine` reference but doesn't use it yet.

**Commit B2:** Move `active_downloads` state out of every client into `engine.active_downloads`. Each client's `download()` now updates engine state via callback instead of `self.active_downloads[id] = ...`. Backward compat: each client's `self.active_downloads` becomes a property that delegates to `engine.active_downloads.filter(source=self.name)`.

**Commit B3:** Move `get_all_downloads` / `get_download_status` / `cancel_download` dispatch from orchestrator (which iterates plugins) into engine (which queries unified state). Orchestrator's methods become thin pass-throughs.

### Phase C — Background download worker lift

**Commit C1:** New `core/download_engine/worker.py` — `BackgroundDownloadWorker` class. Owns semaphore, rate-limit sleep, state-update lock pattern. Provides `dispatch(plugin, target_id, display_name) → download_id`.

**Commit C2:** Migrate YouTube to use BackgroundDownloadWorker. Strip `_download_thread_worker` from `youtube_client.py`. Add `download_raw(video_id, dest) → Path`. Tests stay green (Phase A pinned them).

**Commit C3:** Same for Tidal.
**Commit C4:** Same for Qobuz.
**Commit C5:** Same for HiFi.
**Commit C6:** Same for Deezer.
**Commit C7:** Same for SoundCloud.

After Phase C: ~490 LOC of duplicated thread management deleted. Each affected client shrinks.

### Phase D — SKIPPED

**Original intent:** Lift search retry / query normalization / quality filter into engine. **Dropped after surveying actual per-source search code.** Search is 90% source-specific (slskd event subscription vs yt-dlp subprocess vs HTTP REST vs HLS quality map), not 60% like the original plan estimated. Lifting would be either lossy (force per-source quirks through a uniform interface) or bloated (adapter code bigger than the original). The shared portion is ~10 LOC per source — not worth a SearchOrchestrator. Per-source search stays per-source.

### Phase D (original — kept for reference, NOT executed)

**Commit D1:** New `core/download_engine/search.py` — `SearchOrchestrator`. Owns: query normalization, shortened-query retry ladder, quality filter, dedup. Calls `plugin.search_raw(query)` for the actual API hit.

**Commit D2:** Migrate Tidal's search. Strip `_generate_shortened_queries`, quality filter, dedup from client. Add `search_raw(query) → List[RawResult]`.
**Commit D3:** Same for Qobuz.
**Commit D4:** Same for HiFi.
**Commit D5:** Same for YouTube.
**Commit D6:** Same for Deezer.
**Commit D7:** Same for SoundCloud.
**Commit D8:** Same for Soulseek (keep slskd event-driven specifics, but the post-search filter/dedup moves out).
**Commit D9:** Same for Lidarr.

### Phase E — Rate-limit pool

**Commit E1:** New `core/download_engine/rate_limit.py` — per-source rate limiter registry. Spotify limit, Qobuz 1/sec, etc. Each plugin declares its limits in its registry spec.
**Commit E2:** Strip per-client rate-limit state. Replace with `await engine.rate_limit.acquire(self.name)` at the top of `search_raw` / `download_raw`.

### Phase F — Fallback chain into engine

**Commit F1:** Engine owns fallback: `engine.search_with_fallback(query, source_chain)` and `engine.download_with_fallback(target_id, source_chain)`. Search hybrid behavior preserved; download hybrid newly works (today it silently routes to one source).
**Commit F2:** Orchestrator's `search` and `download` methods delegate to engine's fallback methods. Hybrid mode logic moves out of orchestrator.

### Phase G — Plugin contract narrows

**Commit G1:** Update `DownloadSourcePlugin` Protocol — narrow to the small surface (`search_raw`, `download_raw`, `cancel_raw`, `is_configured`, `check_connection`, `reload_settings`). Conformance tests updated.
**Commit G2:** Remove dead methods from clients that the engine now owns (`_download_thread_worker`, `_filter_results_by_quality`, etc.). Clean up imports.

### Phase H — Cleanup + WHATS_NEW + version bump

**Commit H1:** Final cleanup pass — remove backward-compat shims that are no longer needed (legacy `self.active_downloads` properties etc., once nothing reaches in for them).
**Commit H2:** WHATS_NEW entry, PR description.

## Total estimated scope

- ~25-30 commits
- ~2000 LOC removed (duplicated thread workers, search retries, etc.)
- ~1200 LOC added (engine + per-source slim adapters)
- Net reduction: ~800 LOC
- ~50 new tests (Phase A pinning) + ~20 engine-level tests
- 1-2 days of focused work

## Risk profile

**Low risk:**
- Phase A (only adds tests, never changes behavior)
- Phase B1 (new file, doesn't touch existing code)
- Phase H (cleanup of dead code)

**Medium risk:**
- Phase B2-B3 (state lift — race conditions, lock contention)
- Phase C (thread worker extraction — semaphore semantics, exception propagation)
- Phase G (contract narrows — anything reaching in for removed methods breaks)

**High risk:**
- Phase D (search retry — easy to subtly change retry ladder shape)
- Phase E (rate-limit — wrong order can cause deadlocks or under-limit violations)
- Phase F (fallback — easy to accidentally change hybrid mode behavior)

**Mitigation:** Phase A pinning tests catch behavior drift in every later phase. Each commit must pass full suite. Manual smoke test per source after Phase C and again at end.

## Coordination with Cin

- Cin's metadata engine PR will likely set the precedent for HOW abstractions look (Protocol vs ABC, sync vs async, state location). This plan defaults to Protocol + async (matches what we already have) but easy to mirror Cin's exact pattern when his PR lands.
- If his pattern differs significantly, we may need to redo some commits. Best mitigation: don't dig too deep on contract shape (Phase G) until his PR is visible. Phases A-C don't depend on contract shape; they're safe to do regardless.
- Send Cin a heads-up before starting — he may have feedback on the plan that saves a redesign later.

## Compatibility commitments

- **Originally** the plan was to preserve `orchestrator.soulseek` / `orchestrator.youtube` / etc. attribute aliases through every phase. Cin's review pass removed them in favor of the generic `orchestrator.client('<name>')` accessor — this is a breaking change for any external code that reached into per-source attributes directly. Anything in-tree was migrated as part of the same PR; in-flight branches will need to update.
- The legacy `soulseek_client` global handle was renamed to `download_orchestrator` in the same review pass. Any module that imported / referenced `soulseek_client` was migrated; in-flight branches will need to update.
- Soulseek-specific helper methods (`clear_all_searches`, `_make_request`, `signal_download_completion`, etc.) still live on the orchestrator and continue to work — but reach the underlying SoulseekClient via `orchestrator.client('soulseek')` instead of `orchestrator.soulseek`.
- Frontend status dashboard keys (`deezer_dl` alias) preserved.
- Config format unchanged.
- DB schema unchanged.
- API endpoint surface unchanged.

## What's NOT in this PR

- Cin's metadata engine work (separate, his domain)
- Media server client refactor (different subsystem, separate PR)
- Match engine refactor (different subsystem, separate PR)
- Adding new download sources (out of scope; the new contract makes them easier later)
