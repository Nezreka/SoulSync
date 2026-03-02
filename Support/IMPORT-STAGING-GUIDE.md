# Import & Staging Folder Guide

## Overview

Got a mess of audio files — ripped CDs, old downloads, files from other apps — that you want in your library with proper metadata? That's what Import is for.

Drop your unorganized files into the staging folder, open the Import page, and match them to albums or tracks. SoulSync takes care of the rest: full metadata tagging (artist, album, track number, genres), album art embedding, lyrics fetching, renaming to your path template, and moving everything into your organized library. Files go from a chaotic pile to properly tagged, organized tracks in your transfer folder.

---

## Setup

### 1. Configure the Staging Path

In **Settings**, find the **"Import Staging Dir"** field. The default is `./Staging`.

For Docker, map a host folder to the container:

```yaml
volumes:
  - /path/to/your/staging:/app/Staging
```

Then set the staging path in SoulSync settings to `/app/Staging`.

### 2. Add Files to the Staging Folder

Drop audio files into your staging folder. Supported formats:

| Format | Extension |
|--------|-----------|
| FLAC | `.flac` |
| MP3 | `.mp3` |
| AAC | `.aac`, `.m4a` |
| OGG Vorbis | `.ogg` |
| Opus | `.opus` |
| WAV | `.wav` |
| WMA | `.wma` |
| AIFF | `.aiff`, `.aif` |
| Monkey's Audio | `.ape` |

You can organize files in subfolders — SoulSync reads folder names as hints for album suggestions (e.g., a folder named `Artist - Album` improves matching).

---

## Using the Import Page

Click **Import** in the sidebar to open the Import page. The top bar shows your staging folder path, file count, and total size. Click **Refresh** to re-scan if you've added new files.

There are two modes: **Albums** and **Singles**.

---

### Album Mode

Use this when your staging files belong to a complete album (or part of one).

#### Step 1: Find the Album

SoulSync automatically suggests albums based on your files' metadata tags and folder structure. These appear as album cards with cover art.

If the right album isn't suggested, use the **search bar** to find it by name.

#### Step 2: Select an Album

Click an album card to select it. You'll see:
- Album cover art, title, artist, track count, and release year
- The full tracklist with track numbers and names
- Automatic file-to-track matching with confidence percentages

#### Step 3: Review Matches

SoulSync matches your staging files to album tracks using title similarity and track numbering. Each match shows a confidence percentage:

- **70%+** — High confidence, likely correct
- **Below 70%** — Worth double-checking
- **100%** — You assigned it manually

Unmatched files appear in an **"Unmatched Files"** pool at the bottom.

#### Step 4: Fix Mismatches (Drag-and-Drop)

If a file was matched to the wrong track:

1. **Drag** a file from the unmatched pool
2. **Drop** it onto the correct track row
3. The previous match (if any) returns to the unmatched pool

To remove a match, click the **X** button next to the matched file.

You can also click **"Re-match Automatically"** to reset all manual overrides and let SoulSync re-run its matching.

**On mobile:** Tap a file chip to select it, then tap the track row to assign it.

#### Step 5: Process

The bottom of the page shows how many tracks are matched (e.g., "8 of 12 tracks matched"). Click **"Process X Tracks"** to start importing.

---

### Singles Mode

Use this for individual tracks that aren't part of an album.

#### Step 1: Browse Files

All staging files appear as a list showing filename and any metadata tags found.

#### Step 2: Identify Tracks

Click **"Identify"** next to a file to search for the matching Spotify track. Select the correct result.

#### Step 3: Select and Process

Check the boxes next to files you want to import. Use **"Select All"** to toggle everything. Click **"Process Selected (N)"** to queue them.

---

## Processing Queue

When you start an import, a processing queue appears showing real-time progress:

- **Progress bar** fills as tracks complete
- **Status** shows counts like "3/10" (processed/total)
- **Errors** are shown inline (e.g., "8/10 (2 err)")
- **"Clear finished"** button removes completed/errored jobs

Processing continues in the background even if you navigate away from the Import page. After all jobs finish, the staging folder is automatically re-scanned and suggestions refresh.

### What Processing Does

For each matched track, SoulSync:
1. Enriches the file with full Spotify metadata (artist, album, track number, disc number, genres)
2. Embeds album artwork
3. Fetches synchronized lyrics (LRC) when available
4. Renames and moves the file to your transfer folder using your configured path template
5. Triggers a media server library scan (if connected)

---

## Tips

- **Organize by album** — Putting files in an `Artist - Album` subfolder significantly improves automatic suggestions and matching
- **Tag your files first** — Files with proper ID3/metadata tags get better automatic matches
- **Start with Album mode** — It's faster for grouped files since you match a whole album at once
- **Use Singles mode for loose tracks** — Mixtapes, random downloads, one-offs
- **Check confidence scores** — Low percentages mean the match might be wrong
- **Drag-and-drop is your friend** — Faster than re-searching when a match is close but wrong

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| No files showing up | Check that your staging path is correct in Settings and the folder isn't empty. Click Refresh. |
| No album suggestions | Files may lack metadata tags. Try searching manually by album name. |
| Wrong track matched | Drag the correct file from the unmatched pool onto the track, or click X to unmatch and try again. |
| Processing fails | Check that your transfer path is writable. Enable DEBUG logging in Settings and check `logs/app.log`. |
| Files not disappearing after import | Successfully processed files are moved to your transfer folder. Check there. Failed files remain in staging. |
| Docker: staging folder empty | Verify your volume mapping points to the right host folder and the container path matches your Settings value. |

---

## Docker Example

```yaml
volumes:
  # Your staging folder for imports
  - /mnt/user/Music/Staging:/app/Staging

  # Where processed files end up
  - /mnt/user/Music/Library:/app/Transfer:rw
```

**SoulSync Settings:**
- Import Staging Dir: `/app/Staging`
- Transfer Path: `/app/Transfer`

**Workflow:**
```
1. You drop files into /mnt/user/Music/Staging (host)
                     → /app/Staging (container)

2. Open Import page → Match to albums/tracks

3. Process → Files move to /app/Transfer (container)
                         → /mnt/user/Music/Library (host)
                         → Media server picks them up
```
