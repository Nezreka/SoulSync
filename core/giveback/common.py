"""what every give back part shares: the settings, the user agent, the pace,
and the shape checks for the ids it sends."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Optional

# Settings, Connections, Give back. all off until the person turns them on
SETTINGS_ROOT = 'giveback'
ACOUSTID_SETTING = 'giveback.acoustid'
ISRC_SETTING = 'giveback.isrc'
RELEASES_SETTING = 'giveback.releases'

PROJECT_URL = 'https://github.com/Nezreka/SoulSync'

_version = ['3']


def set_app_version(version: str) -> None:
    """web_server hands over SOULSYNC_VERSION at boot"""
    if isinstance(version, str) and version.strip():
        _version[0] = version.strip()


def app_version() -> str:
    return _version[0]


def user_agent() -> str:
    """musicbrainz asks for "name/version ( contact )":
    https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting"""
    return f"SoulSync/{app_version()} ( {PROJECT_URL} )"


def client_param() -> str:
    """the ``client`` musicbrainz wants on a submission: "app-version", and the
    version may not hold a "-" """
    return 'soulsync-' + app_version().split('+')[0].replace('-', '.')


def cfg_of(cfg=None):
    if cfg is not None:
        return cfg
    from core.settings import config_manager
    return config_manager


def enabled(setting: str, cfg=None) -> bool:
    try:
        return cfg_of(cfg).get(setting, False) is True
    except Exception:  # noqa: BLE001 - no config means off
        return False


_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_ISRC = re.compile(r'^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$')


def mbid(value: Any) -> str:
    """a musicbrainz id, lower case, or '' when it isn't one"""
    text = str(value or '').strip().lower()
    return text if _UUID.match(text) else ''


def isrc(value: Any) -> str:
    """an isrc in its 12 character form (CC-XXX-YY-NNNNN loses its dashes),
    or '' when it isn't one"""
    text = re.sub(r'[\s-]', '', str(value or '')).upper()
    return text if _ISRC.match(text) else ''


class Pacer:
    """at most one call every ``interval`` seconds, across threads"""

    def __init__(self, interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval = float(interval)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: Optional[float] = None

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                gap = self._clock() - self._last
                if gap < self.interval:
                    self._sleep(self.interval - gap)
            self._last = self._clock()


# musicbrainz: one request a second on average. a little under it, so a
# slow clock never tips it over
MUSICBRAINZ_PACE = Pacer(1.1)
# acoustid allows three a second; a submission is one big request, so one
# a second is plenty
ACOUSTID_PACE = Pacer(1.0)
