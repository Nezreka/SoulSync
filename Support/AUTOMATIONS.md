# Automations Guide

## Overview

The Automations page lets you build custom workflows that run automatically. Each automation connects a **trigger** (when to run) to an **action** (what to do), with optional **conditions** (filters) and **notifications** (alerts when it runs).

Navigate to the Automations page from the sidebar. You'll see your automation cards and a builder panel for creating new ones.

---

## Building an Automation

### The Builder

The builder has three slots:

- **WHEN** — drag a trigger here (required)
- **DO** — drag an action here (required)
- **NOTIFY** — drag a notification method here (optional)

Drag blocks from the sidebar into the slots. Each block expands to show its configuration fields. Give your automation a name and click **Save**.

### Conditions

Event-based triggers support conditions to filter when they fire. For example, a "Track Downloaded" trigger can have a condition like `artist contains "Taylor"` so it only fires for specific artists.

- **Match mode**: "All" (every condition must pass) or "Any" (at least one must pass)
- **Operators**: contains, equals, starts_with, not_contains

### Delay

Action blocks have an optional **Delay** field (in minutes). The action waits that long after the trigger fires before executing. Useful for letting other processes finish first.

---

## Triggers

### Timer-Based

| Trigger | Description | Configuration |
|---------|-------------|---------------|
| **Schedule** | Run on a repeating interval | Interval + unit (minutes/hours/days) |
| **Daily Time** | Run every day at a specific time | Time picker (e.g., 03:00) |
| **Weekly Schedule** | Run on specific days at a set time | Day selector + time picker |

### Event-Based

| Trigger | Fires When | Condition Fields | Variables |
|---------|-----------|-----------------|-----------|
| **App Started** | SoulSync starts up | — | — |
| **Track Downloaded** | A track finishes downloading | artist, title, album, quality | artist, title, album, quality |
| **Batch Complete** | An album/playlist download finishes | playlist_name | playlist_name, total_tracks, completed_tracks, failed_tracks |
| **New Release Found** | Watchlist detects new music | artist | artist, new_tracks, added_to_wishlist |
| **Playlist Synced** | A playlist sync completes | playlist_name | playlist_name, total_tracks, matched_tracks, synced_tracks, failed_tracks |
| **Playlist Changed** | A mirrored playlist detects track changes | playlist_name | playlist_name, old_count, new_count, added, removed |
| **Discovery Complete** | Playlist track discovery finishes | playlist_name | playlist_name, total_tracks, discovered_count, failed_count, skipped_count |
| **Wishlist Processed** | Auto-wishlist processing finishes | — | tracks_processed, tracks_found, tracks_failed |
| **Watchlist Scan Done** | Watchlist scan finishes | — | artists_scanned, new_tracks_found, tracks_added |
| **Database Updated** | Library database refresh finishes | — | total_artists, total_albums, total_tracks |
| **Download Failed** | A track permanently fails to download | artist, title, reason | artist, title, reason |
| **File Quarantined** | AcoustID verification fails | artist, title | artist, title, reason |
| **Wishlist Item Added** | A track is added to wishlist | artist, title | artist, title, reason |
| **Artist Watched** | An artist is added to watchlist | artist | artist, artist_id |
| **Artist Unwatched** | An artist is removed from watchlist | artist | artist, artist_id |
| **Import Complete** | Album/track import finishes | artist, album_name | track_count, album_name, artist |
| **Playlist Mirrored** | A new playlist is mirrored | playlist_name, source | playlist_name, source, track_count |
| **Quality Scan Done** | Quality scan finishes | — | quality_met, low_quality, total_scanned |
| **Duplicate Scan Done** | Duplicate cleaner finishes | — | files_scanned, duplicates_found, space_freed |

---

## Actions

| Action | Description | Configuration |
|--------|-------------|---------------|
| **Process Wishlist** | Retry failed downloads from wishlist | Category: All, Albums, or Singles |
| **Scan Watchlist** | Check watched artists for new releases | — |
| **Scan Library** | Trigger media server library scan | — |
| **Refresh Mirrored Playlist** | Re-fetch playlist from source (Spotify/Tidal/YouTube) and update the mirror | Select playlist or "Refresh all" |
| **Discover Playlist** | Find official Spotify/iTunes metadata for mirrored playlist tracks | Select playlist or "Discover all" |
| **Sync Playlist** | Sync mirrored playlist to media server (only discovered tracks are included) | Select playlist |
| **Notify Only** | No action — just send the notification | — |
| **Update Database** | Trigger library database refresh | Full refresh checkbox |
| **Run Duplicate Cleaner** | Scan for and remove duplicate files | — |
| **Clear Quarantine** | Delete all quarantined files | — |
| **Clean Up Wishlist** | Remove duplicate/already-owned tracks from wishlist | — |
| **Update Discovery** | Refresh discovery pool with new tracks | — |
| **Run Quality Scan** | Scan for low-quality audio files | Scope: Watchlist Artists or Full Library |
| **Backup Database** | Create timestamped database backup | — |

---

## Notifications

Add a notification block to get alerted when an automation runs.

| Method | Configuration | Notes |
|--------|---------------|-------|
| **Discord Webhook** | Webhook URL + message template | Posts to a Discord channel |
| **Pushbullet** | Access token + title + message | Push to phone/desktop |
| **Telegram** | Bot token + chat ID + message | Sends via Telegram Bot API |

### Variable Substitution

Notification messages support `{variable}` placeholders that get replaced with actual values when the automation runs.

**Always available**: `{time}`, `{name}` (automation name), `{run_count}`, `{status}`

**Event-specific**: Each trigger provides additional variables (see the Variables column in the triggers table above). For example, a "Track Downloaded" trigger provides `{artist}`, `{title}`, `{album}`, `{quality}`.

**Example message**:
```
Downloaded {title} by {artist} from {album} — quality: {quality}
```

---

## System Automations

SoulSync includes two built-in system automations that cannot be deleted:

| Automation | Schedule | Initial Delay |
|-----------|----------|---------------|
| **Auto-Process Wishlist** | Every 30 minutes | 1 minute after startup |
| **Auto-Scan Watchlist** | Every 24 hours | 5 minutes after startup |

These appear with a "System" badge on their cards. You can:
- Change the interval
- Enable or disable them
- Add notifications

You cannot:
- Delete them
- Change the trigger or action type

---

## Mirrored Playlist Sync Pipeline

For mirrored playlists (especially from YouTube and Tidal), a multi-step automation chain ensures tracks are synced with proper metadata:

### The Problem

YouTube and Tidal playlists have raw metadata — cleaned video titles, uploader names. If you sync these directly, unmatched tracks hit the wishlist with garbage data (no Spotify ID, wrong album, no cover art). Downloads would fail or get the wrong track.

### The Solution

Three automations chained via events:

**Step 1: Refresh** — Re-fetch the playlist from its source
```
WHEN: Schedule (every 6 hours)
DO:   Refresh Mirrored Playlist (all)
```
This detects added/removed tracks by comparing source track IDs. If changes are found, it emits a "Playlist Changed" event.

**Step 2: Discover** — Match raw tracks to official Spotify/iTunes metadata
```
WHEN: Playlist Changed
DO:   Discover Playlist (all)
```
For each undiscovered track, the discovery pipeline:
1. Checks the discovery cache (instant if previously matched)
2. Searches Spotify (preferred) or iTunes (fallback) using the matching engine
3. Scores candidates with title/artist fuzzy matching
4. Stores the official match (Spotify ID, proper title, artist, album) on the track

When done, emits a "Discovery Complete" event.

**Step 3: Sync** — Push to media server with verified metadata
```
WHEN: Discovery Complete
DO:   Sync Playlist (select playlist)
```
Only discovered tracks are included in the sync. Undiscovered tracks are skipped entirely — they never reach the wishlist with bad data. Unmatched discovered tracks go to the wishlist with proper Spotify/iTunes IDs and album context.

### Spotify Playlists

Spotify-sourced mirrored playlists skip Step 2 automatically. Their data is already official, so tracks are marked as discovered during refresh with confidence 1.0. You can go directly from "Playlist Changed" to "Sync Playlist".

### Discovery Caching

Discovery results are cached globally. If the same track appears in multiple playlists, or was discovered previously, the cache provides instant results without hitting the Spotify/iTunes API again. The cache persists across restarts.

---

## Examples

### Get notified when a watched artist drops new music
```
WHEN: New Release Found (artist contains "Kendrick")
DO:   Notify Only
NOTIFY: Discord Webhook — "{artist} dropped {new_tracks} new tracks!"
```

### Nightly library maintenance
```
WHEN: Daily Time (03:00)
DO:   Update Database (full refresh)
```

### Auto-download wishlist failures every hour
```
WHEN: Schedule (every 1 hour)
DO:   Process Wishlist (all)
NOTIFY: Telegram — "Wishlist processed: {tracks_found} found, {tracks_failed} failed"
```

### Quality upgrade pipeline
```
WHEN: Database Updated
DO:   Run Quality Scan (watchlist artists)
```

### Discord alert on download failures
```
WHEN: Download Failed
DO:   Notify Only
NOTIFY: Discord Webhook — "Failed to download {title} by {artist}: {reason}"
```

### Weekly database backup
```
WHEN: Weekly Schedule (Sun at 02:00)
DO:   Backup Database
```

---

## Tips

- **Test with "Run Now"**: Every automation card has a play button that triggers it immediately, regardless of its schedule. Use this to verify your setup before waiting for the timer.
- **Check the activity feed**: The Dashboard activity feed shows when automations run and their results.
- **Conditions narrow, not widen**: Without conditions, an event trigger fires for every event of that type. Conditions filter it down to specific cases.
- **Delay is per-execution**: If you set a 5-minute delay, the action waits 5 minutes after each trigger fire, not 5 minutes after the last execution.
- **Cross-guards**: The system automations (wishlist/watchlist) have mutual exclusion — if one is running, the other waits until the next scheduled time rather than queueing up.
- **Discovery is incremental**: Running "Discover Playlist" only processes tracks that haven't been discovered yet. Already-discovered tracks are skipped. Failed tracks are re-attempted on subsequent runs.
