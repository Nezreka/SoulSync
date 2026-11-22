"""may this profile play this item, and where are its bytes.

one check for every way in: the player's own session and a paired
device's token both arrive here as a profile (web_server's device gate
turns a token into its profile and its permissions before any route
runs), so nothing below knows or cares which it was.

    resolve(item_type, item_id, part=None) -> Source   or raises Refused

the order never changes:
  1. a profile: none (signed out, or a device that hasn't picked one) is 401
  2. the section: the profile's permission for the kind (music.access,
     movies.access, ...); a paired device only plays what its kind of app is
     for (core/connect/pick.KIND_ITEM_TYPES)
  3. the item: found, inside the profile's limits (a kids rating cap, hidden
     explicit music), with a file on disk or on the media server

each kind finds its own bytes in core/player/sources/<kind>.py. they share
nothing but the Source they return, so a new kind is one module and one
line in KINDS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class Refused(Exception):
    """why an item can't be played. the message is safe to show."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Source:
    """where an item's bytes are and what the player needs to know first.

    mode: 'file' (a path on this box, byte-served with Range), 'relay' (the
    media server's url, fetched server side because it carries its token;
    never sent to a client), or 'external' (a url the client may load
    itself, like a podcast enclosure through the checked audio proxy).
    """

    mode: str
    mime: str = "application/octet-stream"
    path: Optional[str] = None
    url: Optional[str] = None
    # what the client is told, the url never among it
    duration: Optional[float] = None
    verdict: Optional[Dict[str, Any]] = None
    parts: List[Dict[str, Any]] = field(default_factory=list)
    chapters: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    # facts the server uses (a video's codecs, for the transcoder) and a
    # client never sees
    private: Dict[str, Any] = field(default_factory=dict)

    def public(self) -> Dict[str, Any]:
        """what a client may see: how to play it, never where the file is"""
        out: Dict[str, Any] = {"mode": "external" if self.mode == "external" else "stream",
                               "mime": self.mime}
        if self.mode == "external":
            out["url"] = self.url
        for key in ("duration", "verdict"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.parts:
            out["parts"] = self.parts
        if self.chapters:
            out["chapters"] = self.chapters
        out.update(self.extra)
        return out


@dataclass(frozen=True)
class Kind:
    """an item type the player can play"""

    permissions: tuple            # any one of these lets a profile in
    locate: Callable[..., Source]


def _music(item_id, part=None):
    from core.player.sources import music
    return music.locate(item_id)


def _video(kind):
    def run(item_id, part=None):
        from core.player.sources import video
        return video.locate(kind, item_id, part)
    return run


def _youtube(item_id, part=None):
    from core.player.sources import youtube
    return youtube.locate(item_id)


def _audiobook(item_id, part=None):
    from core.player.sources import audiobook
    return audiobook.locate(item_id, part)


KINDS: Dict[str, Kind] = {
    "track": Kind(("music.access",), _music),
    "movie": Kind(("movies.access",), _video("movie")),
    "episode": Kind(("tv.access",), _video("episode")),
    "youtube": Kind(("movies.access", "tv.access"), _youtube),
    "audiobook": Kind(("books.access",), _audiobook),
}


def current_profile_id() -> Optional[int]:
    from core.profile_context import get_current_profile_id
    try:
        pid = get_current_profile_id()
    except Exception:  # noqa: BLE001 - no context is no profile
        return None
    return int(pid) if pid is not None else None


def _device() -> Optional[Dict[str, Any]]:
    try:
        from flask import g
        return g.get("device") if g.get("device_auth") else None
    except RuntimeError:  # outside a request
        return None


def _may(permission: str) -> bool:
    from core.permissions import can
    return can(None, permission)


def check(item_type: str) -> Kind:
    """steps 1 and 2: a profile, allowed this kind of thing"""
    kind = KINDS.get(item_type)
    if kind is None:
        raise Refused(404, "unknown_kind", "The player can't play that kind of thing.")
    if current_profile_id() is None:
        raise Refused(401, "no_profile", "Pick a profile first.")
    device = _device()
    if device is not None:
        # what each kind of paired app may play lives with the device code
        from core.connect.pick import KIND_ITEM_TYPES
        if item_type not in KIND_ITEM_TYPES.get(device.get("kind") or "", ()):
            raise Refused(403, "not_for_this_app", "This app can't play that.")
    if not any(_may(p) for p in kind.permissions):
        raise Refused(403, "not_allowed", "That isn't part of your profile.")
    return kind


def resolve(item_type: str, item_id: Any, part: Any = None) -> Source:
    """the whole check, then the item's own lookup (step 3)"""
    kind = check(item_type)
    text = str(item_id or "").strip()
    if not text or len(text) > 200:
        raise Refused(400, "bad_id", "That isn't an item id.")
    return kind.locate(text, part)
