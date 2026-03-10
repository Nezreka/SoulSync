# Documentation Page — Images Needed

Place all images in: `webui/static/docs/`

## Required Images

### Page Icon (Required)
- `help.png` — Sidebar nav icon for the Help & Docs page (512x512 PNG, same style as other sidebar icons)

### Screenshots (Optional but Recommended)
These are optional — the docs page works without them, but screenshots make it much more professional. Capture at ~1200px wide, PNG format.

| Filename | What to Capture |
|----------|----------------|
| `dashboard-overview.png` | Full dashboard page showing tool cards, stats, activity feed |
| `dashboard-workers.png` | Close-up of enrichment worker tooltips in the header |
| `sync-spotify.png` | Sync page with Spotify playlists loaded |
| `sync-youtube.png` | YouTube tab with URL input and parsed playlist |
| `sync-mirrored.png` | Mirrored playlists tab with cards |
| `search-enhanced.png` | Enhanced search dropdown showing artists/albums/tracks |
| `search-basic.png` | Basic search with filters expanded and results |
| `search-download-modal.png` | Album download modal with track list |
| `search-download-manager.png` | Download manager sidebar with active downloads |
| `discover-hero.png` | Discover page hero slider with featured artist |
| `discover-playlists.png` | Build playlist section or discovery pool playlists |
| `artists-search.png` | Artists page search with results |
| `artists-watchlist.png` | Watchlist scan in progress with live activity |
| `artists-settings.png` | Per-artist or global watchlist settings modal |
| `automations-list.png` | Automations page with cards showing triggers/actions |
| `automations-builder.png` | Automation builder with WHEN/DO/THEN filled in |
| `library-cards.png` | Library page with artist cards and badges |
| `library-detail.png` | Artist detail page with discography |
| `library-enhanced.png` | Enhanced library manager with track table |
| `library-tags.png` | Write Tags preview modal showing diff |
| `import-matching.png` | Import page with album/track matching |
| `settings-services.png` | Settings page showing service credentials |
| `settings-download.png` | Settings page download/quality section |
| `player-sidebar.png` | Sidebar media player in playing state |
| `player-modal.png` | Now Playing modal with queue visible |
| `profiles-picker.png` | Profile picker overlay |

### Setup Instructions
1. Create the folder: `webui/static/docs/`
2. Place `help.png` (required) and any screenshots you want
3. The docs page will gracefully handle missing screenshots — they just won't show
