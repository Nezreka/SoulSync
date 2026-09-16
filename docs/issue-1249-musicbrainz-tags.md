# MusicBrainz release tagging (#1249)

SoulSync now keeps a concrete MusicBrainz release ID alongside the album/release-group ID. Search results, album import contexts, download preflight, per-track enrichment, and final album consistency preserve that edition. Explicit selections bypass album-name caches and sibling-file adoption. A failed release lookup does not silently select another edition, and a subsequent lookup can retry.

MusicBrainz tagging now writes the selected release's date separately from the release group's original date/year. It preserves year-only and year/month dates without inventing a day. Added artist and album-artist sort names, the Artists list, record labels, and multiple recording ISRCs. Release status/type use Picard's lowercase values. New settings appear under MusicBrainz metadata tags.

The import and album-completion writers share Picard-compatible ID3, Vorbis (including Opus), and MP4 mappings. Album completion reads older SoulSync release-ID aliases but writes the canonical MusicBrainz album-ID tag. Explicit release-track assignment checks the song title as well as its position.

Validation covers competing editions with the same name, failed/retried release lookups, context propagation, dates, tag settings, legacy aliases, and actual MP3/FLAC/Opus/M4A files saved and reopened through Mutagen. Existing metadata/import/album-consistency tests were also run.

This affects files processed after the change; it does not automatically retag the existing library. Missing upstream metadata is not fabricated, and this does not attempt full parity with every optional Picard relationship or plugin tag. Audio content and duration are not changed.

Reference: [Picard tag mappings](https://picard-docs.musicbrainz.org/en/latest/appendices/tag_mapping.html).
