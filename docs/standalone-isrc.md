# Preserve embedded ISRC during Standalone Full Refresh

The Standalone tag reader did not return ISRC and the Full Refresh track INSERT
omitted `tracks.isrc`. Consequently even FLAC and MP3 files with readable ISRC
tags were indexed with NULL ISRC.

The reader now reuses the existing `core.metadata.source._isrc_tag_value` probe,
which understands easy keys and raw ID3 `TSRC`. ISRC values are trimmed and
uppercased, and Full Refresh inserts them on both its normal and track-ID
collision paths. Missing values remain NULL. The Library tag inspector also
recognizes `TSRC`. No schema migration or audio-file changes are needed.

This branch starts directly from `dev` and works independently of
`fix/aiff-wav-metadata`. That branch repairs artist/title extraction and reader
extension support; this branch changes ISRC extraction and persistence only.

After deployment and restart, another Standalone Full Refresh rebuilds the
shared Standalone library and populates ISRC for previously indexed tracks.

Tests generate FLAC, MP3, AIFF and WAV files, verify extraction and the tag
inspector, check missing ISRC becomes NULL, run two refreshes, and check that
audio bytes do not change. Collision tests also cover ISRC persistence on the
retry path. Validation uses temporary databases and generated audio files.
