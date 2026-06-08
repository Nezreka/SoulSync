"""Tag-preservation policy for the import pipeline.

Tiny, pure, and deliberately its own seam: it encodes one rule that, when it
was wrong, silently destroyed users' metadata (#804). Keeping it here with a
regression test stops anyone from re-introducing the unconditional wipe.
"""

from __future__ import annotations


def should_wipe_tags_on_enhancement_failure(has_clean_metadata: bool) -> bool:
    """Whether to strip the file's tags after metadata enhancement raised.

    Enhancement throwing means NO new tags were written, so wiping just
    destroys whatever the file already had. For a clean/matched import that's
    catastrophic — #804: already-tagged files (Bruno Mars, Coldplay) got
    blanked into an "Unknown Artist" folder by a transient enhancement error.

    So: only wipe for UNMATCHED downloads (no clean/matched metadata), where
    the tags are likely source junk anyway. NEVER wipe a clean/matched import —
    preserve the user's existing tags.
    """
    return not bool(has_clean_metadata)
