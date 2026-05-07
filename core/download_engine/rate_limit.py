"""Per-source rate-limit policy declarations.

Today's per-source download throttling is scattered:

- YouTube: ``self._download_delay = config_manager.get('youtube.download_delay', 3)``
  set in ``__init__``, applied in ``set_engine`` via worker.set_delay.
- Qobuz: module-level ``_qobuz_api_lock`` + ``_QOBUZ_MIN_INTERVAL`` for
  search-side throttling, no download-side throttle.
- Other sources: no explicit declarations — default to 0s delay /
  concurrency=1, which works because the streaming APIs have their
  own gateway-level rate limits.

Phase E centralizes this into one place: each plugin declares a
``RateLimitPolicy`` (either as a class attribute or returned from a
``rate_limit_policy()`` method), and the engine reads + applies the
policy to ``engine.worker`` at ``register_plugin`` time.

Adding a new source = declaring its policy alongside the rest of
the source's auth/config — no longer a hidden line in __init__ or a
module-level constant in the client file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitPolicy:
    """Per-source download throttling policy.

    Attributes:
        download_concurrency: Max number of concurrent downloads
            from this source. Default 1 (serial). Most streaming
            APIs prefer serial transfers because parallel just
            trades rate-limit errors for thread overhead.
        download_delay_seconds: Minimum gap between successive
            downloads from this source. YouTube uses 3s today
            (legacy ``_download_delay`` config key) to avoid
            yt-dlp 429s. Most other sources use 0.
    """

    download_concurrency: int = 1
    download_delay_seconds: float = 0.0


# Sentinel default — most plugins want this. Plugins that need
# tighter throttling override by exposing ``RATE_LIMIT_POLICY`` as
# a class attribute or returning a custom one from
# ``rate_limit_policy()``.
DEFAULT_POLICY = RateLimitPolicy()


def resolve_policy(plugin) -> RateLimitPolicy:
    """Read a plugin's declared rate-limit policy. Checks (in order):
    1. ``plugin.rate_limit_policy()`` method (returns a RateLimitPolicy)
    2. ``plugin.RATE_LIMIT_POLICY`` class attribute
    3. ``DEFAULT_POLICY``
    """
    method = getattr(plugin, 'rate_limit_policy', None)
    if callable(method):
        try:
            policy = method()
            if isinstance(policy, RateLimitPolicy):
                return policy
        except Exception as e:
            logger.debug("plugin rate_limit_policy() call failed: %s", e)

    declared = getattr(plugin, 'RATE_LIMIT_POLICY', None)
    if isinstance(declared, RateLimitPolicy):
        return declared

    return DEFAULT_POLICY
