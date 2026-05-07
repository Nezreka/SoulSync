# Media Server Engine Refactor Plan

## Goal

Same playbook as the download engine refactor, applied to media servers. Replace 33 `if active_server == 'plex' / 'jellyfin' / 'navidrome' / 'soulsync'` dispatch sites in web_server.py with a central `MediaServerEngine` that owns server selection + cross-server query dispatch. Each per-server client stays as-is for its protocol-specific work (Plex API SDK, Jellyfin REST, Navidrome OpenSubsonic, SoulSync filesystem walk).

**The smell:** Adding a 5th server (recent SoulSync standalone showed how) requires hunting through 33 web_server.py dispatch sites + DatabaseUpdateWorker branching + UI sync-button visibility hacks. Plex assumptions had leaked into nominally generic code paths.

## Architecture target

```
┌───────────┐                         ┌──────────────────┐
│  feature  │ ─search/scan/sync─▶ ┌──│ MediaServerEngine │ ─▶ Plex (PlexAPI SDK)
│           │ ◀── results ──────  │  │ ◆ active selection│ ─▶ Jellyfin (REST)
└───────────┘                     │  │ ◆ dispatch        │ ─▶ Navidrome (Subsonic)
                                  │  │ ◆ shared shape   │ ─▶ SoulSync (filesystem)
                                  │  └──────────────────┘
                                  │  (no need for per-server thread pool — server APIs
                                  │   are sync-call shaped, not stream-shaped like downloads)
```

## What clients keep (per-server, legitimately)

- **Auth** — Plex token, Jellyfin API key + user ID + library ID, Navidrome user/pass + salt, SoulSync filesystem path
- **Wire protocol** — PlexAPI SDK objects vs Jellyfin REST `/Items` vs Navidrome XML/JSON Subsonic vs filesystem walk
- **ID schemes** — Plex ratingKey (int), Jellyfin GUID (hex), Navidrome string, SoulSync MD5
- **Connection state** — each client owns its session / token / connection object
- **Cache strategy** — Jellyfin's aggressive pre-cache, Navidrome's folder filter, SoulSync's 5-min TTL
- **Wrapper objects** — `JellyfinTrack`, `NavidromeTrack`, etc. stay in client modules

## What moves into the engine

| Today (web_server.py duplicated) | Tomorrow (MediaServerEngine, single dispatch site) |
|---|---|
| `if active_server == 'plex': plex_client.X() elif 'jellyfin': jellyfin_client.X() ...` | `engine.X()` — engine reads `active_server` once, routes |
| Status check 4-way branch | `engine.is_connected()` |
| Library scan trigger 3-way | `engine.trigger_library_scan()` |
| Search dispatch in 3 sites | `engine.search_tracks(title, artist)` |
| Play history 4-way | `engine.get_play_history()` |
| Metadata writeback 4-way (genre / poster / bio) | `engine.update_artist_genres()` etc. — engine routes, plugin no-ops where unsupported |
| `DatabaseUpdateWorker.server_type` branching | `engine` injected, no per-server branching |

## Plugin contract (ABC, narrower than the download contract)

```python
class MediaServerClient(ABC):
    # Connection
    @abstractmethod
    def is_connected(self) -> bool: ...
    @abstractmethod
    def ensure_connection(self) -> bool: ...

    # Library reads (required)
    @abstractmethod
    def get_all_artists(self): ...
    @abstractmethod
    def get_all_album_ids(self): ...
    @abstractmethod
    def search_tracks(self, title, artist, limit=15): ...
    @abstractmethod
    def get_recently_added_albums(self, max_results=400): ...

    # Library writes (required for the scan endpoints — may no-op for SoulSync)
    @abstractmethod
    def trigger_library_scan(self) -> bool: ...
    @abstractmethod
    def is_library_scanning(self) -> bool: ...

    # Playlist sync (optional — Navidrome stub returns True)
    def create_playlist(self, name, tracks) -> bool: return True
    def update_playlist(self, name, tracks) -> bool: return True
    def copy_playlist(self, source, dest_name) -> bool: return True

    # Analytics (optional)
    def get_play_history(self, limit=500) -> list: return []
    def get_track_play_counts(self) -> dict: return {}

    # Metadata writeback (optional — clients no-op where API doesn't support it)
    def update_artist_genres(self, artist, genres) -> bool: return True
    def update_artist_poster(self, artist, image_data) -> bool: return True
    def update_album_poster(self, album, image_data) -> bool: return True
    def update_artist_biography(self, artist) -> bool: return True
```

## Phased commit plan

### Phase 0 — Foundation
- **0.1** Plugin contract (`core/media_server/contract.py`) + ABC
- **0.2** Registry + dispatch helpers (`core/media_server/registry.py`)
- **0.3** Conformance tests — every registered client satisfies the ABC

### Phase A — Behavior pinning
- **A1** Pin Plex client surface (status check, search, scan trigger, metadata writeback shape)
- **A2** Pin Jellyfin client surface
- **A3** Pin Navidrome client surface
- **A4** Pin SoulSync standalone client surface

### Phase B — Engine skeleton
- **B1** `MediaServerEngine` skeleton — holds clients, exposes `active_server` selection
- **B2** Engine dispatch methods (`is_connected`, `search_tracks`, `trigger_library_scan`, `is_library_scanning`, etc.)
- **B3** Engine-level conformance tests

### Phase C — Migrate web_server.py dispatch sites
Each commit migrates a logical cluster (status, scan, playlist, metadata, history) so the diff per commit is small + reviewable.

- **C1** Status / connection-check sites
- **C2** Library scan trigger + scanning-state polling
- **C3** Search dispatch (3 sites)
- **C4** Playlist sync / apply / suggest
- **C5** Play history + track play counts
- **C6** Metadata writeback (genres / posters / bio)

### Phase D — DatabaseUpdateWorker
- **D1** Inject engine into `DatabaseUpdateWorker`
- **D2** Strip `self.server_type` branching (use `engine.get_active_client_type()` where shape genuinely differs)

### Phase E — Cleanup + ship
- **E1** Drop unused imports / dead code
- **E2** WHATS_NEW + PR description

**Total estimated:** 15-18 commits. ~600 LOC moved, ~200 LOC deleted.

## Risk profile

**Low:**
- Phase 0 (pure additive — new contract, no existing code touched)
- Phase A (tests only)
- Phase B (engine exists but nothing routes through it yet)

**Medium:**
- Phase C (each commit changes 5-10 dispatch sites in web_server.py — cross-cutting)
- Phase D (DatabaseUpdateWorker is a hot path during library refresh)

**Mitigation:**
- Phase A pinning catches per-client behavior drift
- Phase B engine tested in isolation before C wires it in
- Phase C commits are small + reversible (one cluster per commit)
- Suite green at every commit

## What's NOT in this PR

- Unifying track ID schemes (Plex int vs Jellyfin GUID vs Navidrome string vs SoulSync MD5) — separate data model refactor
- Merging Plex/Jellyfin/Navidrome wrapper objects (each tied to its API)
- Extracting common metadata writeback logic — too server-specific
- Adding new server types (Subsonic, MusicBrainz local) — out of scope; this PR makes them easier later

## Coordination with other work

- Independent of the download engine refactor (PR #494) — different subsystem
- Cin's metadata engine work also independent
- No collisions expected

## Compatibility commitments

- All existing config keys preserved
- All existing API endpoints preserved
- Plex/Jellyfin/Navidrome/SoulSync clients keep their public methods (engine wraps, doesn't replace)
- Active-server config (`server.active`) unchanged
- `DatabaseUpdateWorker.server_type` available for external readers (deprecated but not removed)
