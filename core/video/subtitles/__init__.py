"""Download external subtitle .srt files and drop them next to the imported video as
``<video stem>.<lang>.srt``.

This used to be a single module; Phase 1 ("Replace Bazarr") converted it into a
package with a provider interface (``core.video.subtitles.providers``). This
``__init__`` is a thin compatibility shim: it re-exports everything the old module
exported, with identical behavior, so existing callers
(``download_monitor.write_subtitles_for``, ``organization``) and
``tests/test_video_subtitles.py`` keep working UNCHANGED.

New code should use the provider interface instead:
``from core.video.subtitles.providers import fetch_subtitle, get_providers``
and ``from core.video.subtitles.providers.base import SubtitleQuery``.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

from .helpers import (
    MAX_LANG_CODES,
    SubtitleLookupError,
    effective_subtitle_languages,
    parse_langs,
    parse_provider_order,
    show_override_for_download,
    srt_name,
    valid_lang_code,
    validate_lang_codes,
    write_subtitles,
)
from .providers.opensubtitles import opensubtitles_fetcher, pick_best_file, search_params

__all__ = ["parse_langs", "parse_provider_order", "pick_best_file", "srt_name",
           "search_params", "opensubtitles_fetcher", "write_subtitles",
           "validate_lang_codes", "valid_lang_code", "MAX_LANG_CODES",
           "effective_subtitle_languages",
           "show_override_for_download", "SubtitleLookupError"]
