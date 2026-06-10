"""Verification-status vocabulary for imported tracks.

Three states, persisted in the DB (``tracks.verification_status``) AND as an
embedded file tag (``SOULSYNC_VERIFICATION``) so the information survives DB
resets and travels with the file:

- ``verified``       — clean AcoustID PASS at import time.
- ``unverified``     — AcoustID SKIP (cross-script / ambiguous / no match in
                       the AcoustID DB). Imported, but not hard-confirmed.
- ``force_imported`` — accepted via the version-mismatch fallback after the
                       retry budget was exhausted (user opted in via
                       ``post_processing.accept_version_mismatch_fallback``).
                       A later library scan will still re-check these but
                       reports them as informational, clearly marked.

Quarantined files are never imported, so they carry no status.
"""

VERIFIED = 'verified'
UNVERIFIED = 'unverified'
FORCE_IMPORTED = 'force_imported'
# Set by the user via the review queue ("yes, this file IS the right track").
# Outranks machine states: the scanner skips these entirely.
HUMAN_VERIFIED = 'human_verified'

ALL_STATUSES = (VERIFIED, UNVERIFIED, FORCE_IMPORTED, HUMAN_VERIFIED)

# The file tag name (Vorbis comment key / ID3 TXXX desc / MP4 freeform).
TAG_NAME = 'SOULSYNC_VERIFICATION'


def status_from_acoustid_result(result_value):
    """Map an AcoustID verification result string ('pass'/'skip'/...) to a
    status. 'disabled'/'error'/unknown return None — no claim either way."""
    if result_value == 'pass':
        return VERIFIED
    if result_value == 'skip':
        return UNVERIFIED
    return None


def status_for_import(context: dict):
    """Status for a just-imported file from its pipeline context.

    The version-mismatch fallback flag wins: a force-accepted file is
    ``force_imported`` regardless of what the (earlier, failed) verification
    said about the candidate.
    """
    if context.get('_version_mismatch_fallback'):
        return FORCE_IMPORTED
    return status_from_acoustid_result(context.get('_acoustid_result'))
