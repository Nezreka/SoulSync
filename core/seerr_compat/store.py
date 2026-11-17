"""what the video db knows about a batch of tmdb titles, for seerr's media
status (#2151). the store lives with the shared title status (#2160) so the
video pages and seerr read the same thing.
"""

from __future__ import annotations

from core.video.title_status import TitleStatusStore as SeerrStore

__all__ = ["SeerrStore"]
