# AIFF and WAV metadata during Standalone Full Refresh

## Confirmed cause and workflow

The Tools database updater sends `POST /api/database/update` with
`full_refresh: true`. `api/database_admin.py:start_database_update` selects the
active server and submits `_run_db_update_task`. For `soulsync`, that function
calls `_run_soulsync_full_refresh`, rather than the media-server update worker.

The refresh walks the configured `soulseek.transfer_path` recursively. The
`.aif`, `.aiff` and `.wav` extensions are already accepted, including uppercase variants. It calls
`core.soulsync_client._read_tags` for every file, groups by album artist (or
artist) and album, and writes the resulting records.

`_read_tags` requested `mutagen.File(path, easy=True)` and only looked for easy
keys such as `artist`, `title` and `album`. Mutagen 1.47.0 returns raw ID3 tags
for AIFF and WAV even with that flag: `TPE1`, `TIT2`, `TALB`, `TSRC` and `TDRC`, rather
than easy keys. The reader therefore returned empty fields, substituted the
filename stem for title, and the refresh substituted `Unknown Artist` and
`Unknown Album`. FLAC exposes Vorbis comment keys directly; MP3 has an EasyID3
wrapper. Those formats do not encounter this mismatch.

## Targeted fix and other readers

`core.metadata.easy_tags.open_easy` provides a read-only easy-key view of raw ID3 tags.
It maps the frames above, plus album artist, track/disc numbers, genres and
MusicBrainz release/recording identifiers. Other formats retain their existing
tag mappings. Empty fields are omitted; invalid numeric tags retain each
caller's existing defaults. Audio duration and bitrate still come from stream
information, including when there are no tags.

The following readers use that normalization:

- Standalone `_read_tags`, used by Full Refresh and `SoulSyncClient` filesystem scans.
- Auto Import `_read_file_tags`, used for grouping and identification.
- Import staging metadata and suggestion readers.
- Import embedded album, album artist and track-number readers.

The Fix Unknown Artists job uses a different abstraction:
`core.tag_writer.read_file_tags`. It already understands raw ID3, but its
extension guard rejected AIFF and WAV. Both are now allowed for reads through that
existing implementation. Write support is unchanged.

The normalizer exposes `TSRC` to the Import reader's existing ISRC field.
Standalone ISRC extraction, database persistence and the Library inspector's
`TSRC` mapping are handled by the separate `fix/standalone-isrc` branch.
These changes perform no audio renaming, moving, conversion or tag writing.

## Repairing previously indexed tracks

After deploying the fix and restarting SoulSync, run **Standalone → Tools →
SoulSync Database Updater → Full Refresh** again. A refresh calls
`clear_server_data('soulsync')` and rebuilds the shared Standalone library from
disk; it does not skip files merely because they were indexed before. The old
shared Unknown Artist/Unknown Album records are removed with that library's
rows, and corrected artist/album associations are rebuilt. Other servers and
profile-owned libraries are outside that clear operation.

For the reported tags, the expected result is artist `A Hundred Drums, VEIL`,
title `Shockwave` and year `2024`. Album remains
`Unknown Album` if `TALB` is absent. Track IDs continue to derive from the file
path, subject to the existing collision handling. Files stay at their current
paths. No manual SQL or re-import is required.

Fix Unknown Artists can now resolve these files from their tags too, but that
job has separate tag-writing and reorganization options enabled by default.
The tested recovery route here is Full Refresh.

## WAV tagging scope

The WAV confirmation used a real PCM WAV containing ID3 frames: Mutagen's
`easy=True` reader returned a `WAVE` object with raw ID3 tags, no `artist` key,
and a valid `TPE1` artist. The shared normalization already fixes Standalone and
Import for this case. The remaining WAV extension guard in the maintenance
reader is now fixed too.

WAV can also contain RIFF LIST INFO metadata (`IART`, `INAM`, `IPRD`, etc.).
Mutagen 1.47.0's WAVE reader only loads ID3 metadata. RIFF INFO-only files are
outside this ID3 fix; their artist/title would still require a separate reader.
No RIFF parser or audio-writing behavior is added by this change.

## Verification

179 targeted tests passed, including real generated AIFF/WAV containers with `.aif`,
`.aiff`, `.AIFF`, `.wav` and `.WAV` suffixes, missing/invalid tags, corrupt files, Import and
maintenance readers, existing FLAC/MP3 coverage and refresh collision handling.
The refresh regression uses a temporary MusicDatabase seeded with an incorrectly
indexed track, runs two refreshes, and checks the corrected associations,
removal of old placeholder records and unchanged audio bytes. Ruff and
`git diff --check` also pass. Validation used generated fixtures, rather than the
user's actual 59 audio files. No real library refresh was performed.
