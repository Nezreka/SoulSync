"""Download source plugin contract + registry.

This package defines the canonical interface every download source
(Soulseek, YouTube, Tidal, Qobuz, HiFi, Deezer, Lidarr, SoundCloud,
and future additions like Usenet) must satisfy. The orchestrator
dispatches through this contract instead of hardcoded
`if self.youtube ... elif self.tidal ...` chains.

This is the foundation step of a multi-commit refactor. Subsequent
commits extract shared logic (background download worker, search
query normalization, post-processing context building) into the
contract so adding a new source becomes a one-class plugin instead
of a 700+ LOC copy-paste loop.

See `core/download_plugins/base.py` for the protocol contract and
`core/download_plugins/registry.py` for the dispatch entry point.
"""

from core.download_plugins.base import DownloadSourcePlugin

# NOTE: DownloadPluginRegistry is intentionally NOT re-exported here.
# Importing the registry triggers eager imports of every client class
# (see registry.py for why eager — test fixtures inject mock modules
# at collection time and we need real bindings before that). Clients
# inherit from DownloadSourcePlugin (Cin's review feedback — visible
# contract conformance), so importing the package via ``from
# core.download_plugins import DownloadSourcePlugin`` from a client
# file would create a circular import if registry came along for the
# ride. Callers that need the registry import it directly:
#     from core.download_plugins.registry import DownloadPluginRegistry

__all__ = [
    "DownloadSourcePlugin",
]
