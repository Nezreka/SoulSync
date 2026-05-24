"""Static block definitions for the automation builder UI.

Returned verbatim by `/api/automations/blocks` (with `known_signals`
injected by the route from `signals.collect_known_signals`).

Three top-level lists:
- `TRIGGERS` — WHEN blocks: schedule, daily/weekly time, app started,
  event triggers (track_downloaded, batch_complete, etc.), signal_received,
  webhook_received.
- `ACTIONS` — DO blocks: process_wishlist, scan_library, etc.
- `NOTIFICATIONS` — THEN blocks: discord/pushbullet/telegram/webhook,
  plus fire_signal and run_script then-actions.
"""

from __future__ import annotations

TRIGGERS: list[dict] = [
    {"type": "schedule", "label": "Schedule", "icon": "clock", "description": "Run on a timer interval", "available": True,
     "config_fields": [
         {"key": "interval", "type": "number", "label": "Every", "default": 6, "min": 1},
         {"key": "unit", "type": "select", "label": "Unit",
          "options": [{"value": "minutes", "label": "Minutes"}, {"value": "hours", "label": "Hours"}, {"value": "days", "label": "Days"}],
          "default": "hours"}
     ]},
    {"type": "daily_time", "label": "Daily Time", "icon": "clock", "description": "Run every day at a specific time", "available": True,
     "config_fields": [
         {"key": "time", "type": "time", "label": "At", "default": "03:00"}
     ]},
    {"type": "weekly_time", "label": "Weekly Schedule", "icon": "calendar", "description": "Run on specific days of the week at a set time", "available": True,
     "config_fields": [
         {"key": "time", "type": "time", "label": "At", "default": "03:00"},
         {"key": "days", "type": "multi_select", "label": "Days",
          "options": [{"value": "mon", "label": "Mon"}, {"value": "tue", "label": "Tue"}, {"value": "wed", "label": "Wed"},
                      {"value": "thu", "label": "Thu"}, {"value": "fri", "label": "Fri"}, {"value": "sat", "label": "Sat"}, {"value": "sun", "label": "Sun"}]}
     ]},
    {"type": "app_started", "label": "App Started", "icon": "power", "description": "When SoulSync starts up", "available": True},
    {"type": "track_downloaded", "label": "Track Downloaded", "icon": "download", "description": "When a track finishes downloading", "available": True,
     "has_conditions": True,
     "condition_fields": ["artist", "title", "album", "quality"],
     "variables": ["artist", "title", "album", "quality"]},
    {"type": "batch_complete", "label": "Batch Complete", "icon": "check-circle", "description": "When an album/playlist download finishes", "available": True,
     "has_conditions": True,
     "condition_fields": ["playlist_name"],
     "variables": ["playlist_name", "total_tracks", "completed_tracks", "failed_tracks"]},
    {"type": "watchlist_new_release", "label": "New Release Found", "icon": "bell", "description": "When watchlist detects new music", "available": True,
     "has_conditions": True,
     "condition_fields": ["artist"],
     "variables": ["artist", "new_tracks", "added_to_wishlist"]},
    {"type": "playlist_synced", "label": "Playlist Synced", "icon": "refresh", "description": "When a playlist sync completes", "available": True,
     "has_conditions": True,
     "condition_fields": ["playlist_name"],
     "variables": ["playlist_name", "total_tracks", "matched_tracks", "synced_tracks", "failed_tracks"]},
    {"type": "playlist_changed", "label": "Playlist Changed", "icon": "edit", "description": "When a mirrored playlist detects track changes from source", "available": True,
     "has_conditions": True,
     "condition_fields": ["playlist_name"],
     "variables": ["playlist_name", "old_count", "new_count", "added", "removed"]},
    {"type": "discovery_completed", "label": "Discovery Complete", "icon": "search", "description": "When playlist track discovery finishes", "available": True,
     "has_conditions": True,
     "condition_fields": ["playlist_name"],
     "variables": ["playlist_name", "total_tracks", "discovered_count", "failed_count", "skipped_count"]},
    # Phase 3 triggers
    {"type": "wishlist_processing_completed", "label": "Wishlist Processed", "icon": "check-circle",
     "description": "When auto-wishlist processing finishes", "available": True,
     "variables": ["tracks_processed", "tracks_found", "tracks_failed"]},
    {"type": "watchlist_scan_completed", "label": "Watchlist Scan Done", "icon": "check-circle",
     "description": "When watchlist scan finishes", "available": True,
     "variables": ["artists_scanned", "new_tracks_found", "tracks_added"]},
    {"type": "database_update_completed", "label": "Database Updated", "icon": "database",
     "description": "When library database refresh finishes", "available": True,
     "variables": ["total_artists", "total_albums", "total_tracks"]},
    {"type": "library_scan_completed", "label": "Library Scan Done", "icon": "hard-drive",
     "description": "When media library scan finishes", "available": True,
     "variables": ["server_type"]},
    {"type": "download_failed", "label": "Download Failed", "icon": "x-circle",
     "description": "When a track permanently fails to download", "available": True,
     "has_conditions": True, "condition_fields": ["artist", "title", "reason"],
     "variables": ["artist", "title", "reason"]},
    {"type": "download_quarantined", "label": "File Quarantined", "icon": "alert-triangle",
     "description": "When AcoustID verification fails", "available": True,
     "has_conditions": True, "condition_fields": ["artist", "title"],
     "variables": ["artist", "title", "reason"]},
    {"type": "wishlist_item_added", "label": "Wishlist Item Added", "icon": "plus-circle",
     "description": "When a track is added to wishlist", "available": True,
     "has_conditions": True, "condition_fields": ["artist", "title"],
     "variables": ["artist", "title", "reason"]},
    {"type": "watchlist_artist_added", "label": "Artist Watched", "icon": "user-plus",
     "description": "When an artist is added to watchlist", "available": True,
     "has_conditions": True, "condition_fields": ["artist"],
     "variables": ["artist", "artist_id"]},
    {"type": "watchlist_artist_removed", "label": "Artist Unwatched", "icon": "user-minus",
     "description": "When an artist is removed from watchlist", "available": True,
     "has_conditions": True, "condition_fields": ["artist"],
     "variables": ["artist", "artist_id"]},
    {"type": "import_completed", "label": "Import Complete", "icon": "upload",
     "description": "When album/track import finishes", "available": True,
     "has_conditions": True, "condition_fields": ["artist", "album_name"],
     "variables": ["track_count", "album_name", "artist"]},
    {"type": "mirrored_playlist_created", "label": "Playlist Mirrored", "icon": "copy",
     "description": "When a new playlist is mirrored", "available": True,
     "has_conditions": True, "condition_fields": ["playlist_name", "source"],
     "variables": ["playlist_name", "source", "track_count"]},
    {"type": "quality_scan_completed", "label": "Quality Scan Done", "icon": "bar-chart",
     "description": "When quality scan finishes", "available": True,
     "variables": ["quality_met", "low_quality", "total_scanned"]},
    {"type": "duplicate_scan_completed", "label": "Duplicate Scan Done", "icon": "layers",
     "description": "When duplicate cleaner finishes", "available": True,
     "variables": ["files_scanned", "duplicates_found", "space_freed"]},
    # Signal trigger
    {"type": "signal_received", "label": "Signal Received", "icon": "zap",
     "description": "When another automation fires a named signal", "available": True,
     "config_fields": [
         {"key": "signal_name", "type": "signal_input", "label": "Signal Name"}
     ],
     "variables": ["signal_name"]},
    # Webhook trigger
    {"type": "webhook_received", "label": "Webhook Received", "icon": "globe",
     "description": "When an external API request is received (POST /api/v1/request)", "available": True,
     "variables": ["query", "request_id", "source"]},
]


ACTIONS: list[dict] = [
    {"type": "process_wishlist", "label": "Process Wishlist", "icon": "list", "description": "Retry failed downloads from wishlist", "available": True,
     "config_fields": [{"key": "category", "type": "select", "label": "Category", "options": [{"value": "all", "label": "All"}, {"value": "albums", "label": "Albums"}, {"value": "singles", "label": "Singles"}], "default": "all"}]},
    {"type": "scan_watchlist", "label": "Scan Watchlist", "icon": "eye", "description": "Check watched artists for new releases", "available": True},
    {"type": "scan_library", "label": "Scan Library", "icon": "refresh", "description": "Trigger media server library scan", "available": True},
    {"type": "refresh_mirrored", "label": "Refresh Mirrored Playlist", "icon": "copy", "description": "Re-fetch playlist from source and update mirror", "available": True,
     "config_fields": [
         {"key": "playlist_id", "type": "mirrored_playlist_select", "label": "Playlist"},
         {"key": "all", "type": "checkbox", "label": "Refresh all mirrored playlists", "default": False}
     ]},
    {"type": "sync_playlist", "label": "Sync Playlist", "icon": "sync", "description": "Sync mirrored playlist to media server", "available": True,
     "config_fields": [
         {"key": "playlist_id", "type": "mirrored_playlist_select", "label": "Playlist"}
     ]},
    {"type": "discover_playlist", "label": "Discover Playlist", "icon": "search", "description": "Find official Spotify/iTunes metadata for mirrored playlist tracks", "available": True,
     "config_fields": [
         {"key": "playlist_id", "type": "mirrored_playlist_select", "label": "Playlist"},
         {"key": "all", "type": "checkbox", "label": "Discover all mirrored playlists", "default": False}
     ]},
    {"type": "playlist_pipeline", "label": "Playlist Pipeline", "icon": "rocket",
     "description": "Full lifecycle: refresh → discover → sync → download missing. One automation for the entire flow.",
     "available": True,
     "config_fields": [
         {"key": "playlist_id", "type": "mirrored_playlist_select", "label": "Playlist"},
         {"key": "all", "type": "checkbox", "label": "Process all mirrored playlists", "default": False},
         {"key": "skip_wishlist", "type": "checkbox", "label": "Skip wishlist processing", "default": False},
     ]},
    {"type": "personalized_pipeline", "label": "Personalized Playlist Pipeline", "icon": "sparkles",
     "description": "Sync personalized / discover-page playlists (Hidden Gems, Time Machine, Fresh Tape, etc.) to your media server + queue missing tracks for download.",
     "available": True,
     "config_fields": [
         {"key": "kinds", "type": "personalized_playlist_select", "label": "Playlists to sync",
          "description": "Multi-select: Hidden Gems, Discovery Shuffle, Time Machine (per decade), Genre playlists, Fresh Tape, The Archives, Seasonal Mix (per season)"},
         {"key": "refresh_first", "type": "checkbox", "label": "Refresh playlists before sync (regenerate snapshots)", "default": False},
         {"key": "skip_wishlist", "type": "checkbox", "label": "Skip wishlist processing", "default": False},
     ]},
    {"type": "notify_only", "label": "Notify Only", "icon": "bell", "description": "No action — just send notification", "available": True},
    # Phase 3 actions
    {"type": "start_database_update", "label": "Update Database", "icon": "database",
     "description": "Trigger library database refresh", "available": True,
     "config_fields": [
         {"key": "full_refresh", "type": "checkbox", "label": "Full refresh (slower)", "default": False}
     ]},
    {"type": "run_duplicate_cleaner", "label": "Run Duplicate Cleaner", "icon": "layers",
     "description": "Scan for and remove duplicate files", "available": True},
    {"type": "clear_quarantine", "label": "Clear Quarantine", "icon": "trash",
     "description": "Delete all quarantined files", "available": True},
    {"type": "cleanup_wishlist", "label": "Clean Up Wishlist", "icon": "filter",
     "description": "Remove duplicate/owned tracks from wishlist", "available": True},
    {"type": "update_discovery_pool", "label": "Update Discovery", "icon": "compass",
     "description": "Refresh discovery pool with new tracks", "available": True},
    {"type": "start_quality_scan", "label": "Run Quality Scan", "icon": "bar-chart",
     "description": "Scan for low-quality audio files", "available": True,
     "config_fields": [
         {"key": "scope", "type": "select", "label": "Scope",
          "options": [{"value": "watchlist", "label": "Watchlist Artists"}, {"value": "library", "label": "Full Library"}],
          "default": "watchlist"}
     ]},
    {"type": "backup_database", "label": "Backup Database", "icon": "save",
     "description": "Create timestamped database backup", "available": True},
    {"type": "refresh_beatport_cache", "label": "Refresh Beatport Cache", "icon": "music",
     "description": "Scrape Beatport homepage and warm the cache", "available": True},
    {"type": "clean_search_history", "label": "Clean Search History", "icon": "trash-2",
     "description": "Remove old searches from Soulseek", "available": True},
    {"type": "clean_completed_downloads", "label": "Clean Completed Downloads", "icon": "check-square",
     "description": "Clear completed downloads and empty directories", "available": True},
    {"type": "full_cleanup", "label": "Full Cleanup", "icon": "trash",
     "description": "Clear quarantine, download queue, import folder, and search history in one sweep", "available": True},
    {"type": "deep_scan_library", "label": "Deep Scan Library", "icon": "search",
     "description": "Full library comparison without losing enrichment data", "available": True},
    {"type": "run_script", "label": "Run Script", "icon": "terminal",
     "description": "Execute a script from the scripts folder", "available": True},
    {"type": "search_and_download", "label": "Search & Download", "icon": "download",
     "description": "Search for a track and download the best match", "available": True,
     "config_fields": [
         {"key": "query", "type": "text", "label": "Search Query",
          "placeholder": "Artist - Track (leave empty to use trigger's query)"}
     ]},
]


NOTIFICATIONS: list[dict] = [
    {"type": "discord_webhook", "label": "Discord Webhook", "icon": "message", "description": "Send a Discord notification", "available": True,
     "variables": ["time", "name", "run_count", "status"]},
    {"type": "pushbullet", "label": "Pushbullet", "icon": "push", "description": "Push notification to phone/desktop", "available": True,
     "variables": ["time", "name", "run_count", "status"]},
    {"type": "telegram", "label": "Telegram", "icon": "message", "description": "Send a Telegram message", "available": True,
     "variables": ["time", "name", "run_count", "status"]},
    {"type": "webhook", "label": "Webhook (POST)", "icon": "globe", "description": "Send a POST request to any URL", "available": True,
     "variables": ["time", "name", "run_count", "status"]},
    # Signal fire action
    {"type": "fire_signal", "label": "Fire Signal", "icon": "zap",
     "description": "Fire a signal that other automations can listen for", "available": True,
     "config_fields": [
         {"key": "signal_name", "type": "signal_input", "label": "Signal Name"}
     ]},
    # Run script then-action
    {"type": "run_script", "label": "Run Script", "icon": "terminal",
     "description": "Execute a script after the action completes", "available": True,
     "config_fields": [
         {"key": "script_name", "type": "script_select", "label": "Script"}
     ]},
]
