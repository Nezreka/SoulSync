"""Metadata field identities shared by decisions, previews and execution."""

POSITION_FIELDS = frozenset({'track_number', 'disc_number', 'total_discs',
                            'library_track_number', 'library_disc_number'})
FILE_POSITION_FIELDS = frozenset({'track_number', 'disc_number', 'total_discs'})
ARTIST_FIELDS = frozenset({'track_artist', 'library_track_artist'})
RECORDING_FIELDS = frozenset({'musicbrainz_recording_id', 'library_recording_mbid'})
FIELD_ALIASES = {'disc_total': 'total_discs', 'disctotal': 'total_discs', 'totaldiscs': 'total_discs'}


def canonical_field(name):
    return FIELD_ALIASES.get(name, name)
