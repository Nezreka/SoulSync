"""Conservative, profile-aware decisions for replacing library audio (#1270)."""

from core.imports.file_ops import probe_audio_quality
from core.quality.model import rank_candidate
from core.quality.selection import targets_from_profile
from utils.logging_config import get_logger

logger = get_logger("imports.quality_replace")
_LOSSY = {"mp3", "aac", "opus", "ogg", "wma"}


def is_profile_upgrade(existing_path: str, incoming_path: str, profile: dict) -> bool:
    """Require measured improvement and an incoming format the profile accepts.

    Replacement is stricter than download fallback: an off-profile FLAC must
    never replace an MP3 for an MP3-only user. Missing probe facts are not
    evidence that the library copy is worse. Compare targets first, then
    measured quality within the same format/target.
    """
    try:
        targets, _ = targets_from_profile(profile)
        if not targets:
            return False
        existing = probe_audio_quality(existing_path)
        incoming = probe_audio_quality(incoming_path)
        if existing is None or incoming is None:
            return False
        for quality in (existing, incoming):
            if quality.format.lower() in _LOSSY and not quality.bitrate:
                return False
        old_index, old_score = rank_candidate(existing, targets)
        new_index, new_score = rank_candidate(incoming, targets)
        if new_index == len(targets):
            return False
        if new_index != old_index:
            return new_index < old_index
        if existing.format.lower() != incoming.format.lower():
            return False
        if existing.format.lower() not in _LOSSY:
            if not all((existing.sample_rate, existing.bit_depth,
                        incoming.sample_rate, incoming.bit_depth)):
                return False
        return new_score > old_score
    except Exception:
        # A probe/profile error must never fall through the pipeline's
        # legacy metadata-error handler into an unconditional overwrite.
        logger.warning("Could not establish a quality improvement for %s",
                       existing_path, exc_info=True)
        return False
