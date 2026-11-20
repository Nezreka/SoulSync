"""Tell the listener's scrobble accounts what the web player is playing.

The accounts are the same ones the play will later be scrobbled to (see
``ListeningStatsWorker._scrobble_new_events``): the shared pile goes to the
admin's Last.fm / ListenBrainz from Settings, a profile with its own pile
to the accounts that profile connected for scrobbling. A profile's
listening never reaches someone else's account.

Sending runs on one background thread so a slow scrobble service never
holds a request open, and a burst of skips can't pile up threads.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("playback.now_playing")

_executor: Optional[ThreadPoolExecutor] = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="now-playing")
    return _executor


def scrobble_targets(database: Any, config_manager: Any, profile_id: Optional[int]) -> List[Dict[str, Any]]:
    """The accounts this profile's plays scrobble to.

    Each target is ``{lb_token, lb_base_url, lastfm_session}`` (either side
    may be None). Empty when nothing is set up to scrobble.
    """
    from core.listening_scope import SHARED_OWNER, listening_owner

    owner = listening_owner(database, profile_id)
    if owner == SHARED_OWNER:
        lb_token = (config_manager.get('listenbrainz.token', '')
                    if config_manager.get('listenbrainz.scrobble_enabled', False) else '')
        session = (config_manager.get('lastfm.session_key', '')
                   if config_manager.get('lastfm.scrobble_enabled', False) else '')
        if not lb_token and not session:
            return []
        return [{'lb_token': lb_token or None, 'lb_base_url': None,
                 'lastfm_session': session or None}]
    try:
        profiles = database.profiles_scrobbling()
    except Exception as e:  # noqa: BLE001
        logger.debug("now playing: scrobbling profiles unavailable: %s", e)
        return []
    for prof in profiles:
        if prof.get('profile_id') == owner:
            if not prof.get('listenbrainz_token') and not prof.get('lastfm_session'):
                return []
            return [{'lb_token': prof.get('listenbrainz_token'),
                     'lb_base_url': prof.get('listenbrainz_base_url'),
                     'lastfm_session': prof.get('lastfm_session')}]
    return []


def send_now_playing(target: Dict[str, Any], config_manager: Any, track: Dict[str, Any]) -> Dict[str, bool]:
    """Send one now-playing update to one target's accounts. Never raises."""
    artist = (track.get('artist') or '').strip()
    title = (track.get('title') or '').strip()
    album = (track.get('album') or '').strip()
    try:
        duration = int(round(float(track.get('duration_seconds') or 0)))
    except (TypeError, ValueError):
        duration = 0
    sent = {'listenbrainz': False, 'lastfm': False}
    if not artist or not title:
        return sent
    if target.get('lb_token'):
        try:
            from core.listenbrainz_client import ListenBrainzClient
            base = target.get('lb_base_url')
            client = (ListenBrainzClient(token=target['lb_token'], base_url=base) if base
                      else ListenBrainzClient(token=target['lb_token']))
            sent['listenbrainz'] = bool(client.submit_playing_now(artist, title, album, duration))
        except Exception as e:  # noqa: BLE001
            logger.debug("ListenBrainz now playing failed: %s", e)
    if target.get('lastfm_session'):
        api_key = config_manager.get('lastfm.api_key', '')
        api_secret = config_manager.get('lastfm.api_secret', '')
        if api_key and api_secret:
            try:
                from core.lastfm_client import LastFMClient
                client = LastFMClient(api_key=api_key, api_secret=api_secret,
                                      session_key=target['lastfm_session'])
                sent['lastfm'] = bool(client.update_now_playing(artist, title, album, duration))
            except Exception as e:  # noqa: BLE001
                logger.debug("Last.fm now playing failed: %s", e)
    return sent


def queue_now_playing(database: Any, config_manager: Any, profile_id: Optional[int],
                      track: Dict[str, Any]) -> int:
    """Queue now-playing updates for this profile's accounts. Returns how
    many targets were queued (0 when nothing scrobbles)."""
    targets = scrobble_targets(database, config_manager, profile_id)
    for target in targets:
        _pool().submit(send_now_playing, target, config_manager, dict(track))
    return len(targets)
