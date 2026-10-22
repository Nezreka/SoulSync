"""the tools an ai gets. each is a /api/v1 call, or a small custom handler.

left out on purpose: settings, api keys, profiles, cancel-all, clearing the
blocklist, retag deletes. an ai that gets those wrong costs a library, an
ai that gets a search wrong costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

STR = {"type": "string"}
INT = {"type": "integer", "minimum": 1}


def _obj(props: Dict[str, Any], required: Tuple[str, ...] = ()) -> Dict[str, Any]:
    out = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = list(required)
    return out


def _desc(schema, text):
    return {**schema, "description": text}


PAGE = _desc(INT, "page number, starts at 1")
LIMIT = _desc({**INT, "maximum": 200}, "how many to return")


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    schema: Dict[str, Any]
    # an /api/v1 call: method + path with {placeholders} filled from args
    method: str = "GET"
    path: str = ""
    # custom tools run this instead of an api call: handler(args, ctx) -> data
    handler: Optional[Callable[[Dict[str, Any], Any], Any]] = None
    # custom tools say up front whether a read key may use them
    read_ok: bool = False
    read_only: bool = True
    destructive: bool = False
    open_world: bool = False
    path_args: Tuple[str, ...] = field(default=())

    def annotations(self) -> Dict[str, Any]:
        return {"title": self.title, "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive, "idempotentHint": self.read_only,
                "openWorldHint": self.open_world}

    def listing(self) -> Dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": self.schema, "annotations": self.annotations()}

    def sample_path(self) -> str:
        """the path with placeholders filled, for the read-key check."""
        path = self.path
        for arg in self.path_args:
            path = path.replace("{" + arg + "}", "x")
        return "/api/v1" + path


def _api(name, title, description, method, path, props=None, required=(), *, read_only=None,
         destructive=False, open_world=False):
    path_args = tuple(p.strip("{}") for p in path.split("/") if p.startswith("{"))
    return Tool(name=name, title=title, description=description, schema=_obj(props or {}, required),
                method=method, path=path, path_args=path_args,
                read_only=(method == "GET") if read_only is None else read_only,
                destructive=destructive, open_world=open_world)


API_TOOLS: List[Tool] = [
    # status
    _api("get_server_status", "Server status", "Uptime, connected services and version.",
         "GET", "/system/status"),
    _api("get_recent_activity", "Recent activity", "The recent activity feed: downloads, scans, syncs.",
         "GET", "/system/activity"),
    _api("get_library_stats", "Library stats", "Counts of artists, albums and tracks in the music library.",
         "GET", "/library/stats"),

    # the music library (what's already owned)
    _api("search_library_artists", "Search library artists",
         "Artists already in the music library. Leave search empty to list them.",
         "GET", "/library/artists", {"search": STR, "page": PAGE, "limit": LIMIT}),
    _api("get_library_artist", "Library artist", "One library artist by its library id.",
         "GET", "/library/artists/{artist_id}", {"artist_id": STR}, ("artist_id",)),
    _api("list_artist_albums", "Artist's library albums", "The albums of a library artist.",
         "GET", "/library/artists/{artist_id}/albums", {"artist_id": STR}, ("artist_id",)),
    _api("search_library_albums", "Search library albums", "Albums already in the music library.",
         "GET", "/library/albums", {"search": STR, "artist_id": STR, "year": STR, "page": PAGE, "limit": LIMIT}),
    _api("get_album_tracks", "Library album tracks", "Tracks of a library album by its library id.",
         "GET", "/library/albums/{album_id}/tracks", {"album_id": STR}, ("album_id",)),
    _api("search_library_tracks", "Search library tracks", "Owned tracks matching a title and/or artist.",
         "GET", "/library/tracks", {"title": STR, "artist": STR, "limit": LIMIT}),
    _api("recently_added", "Recently added", "What was added to the library most recently.",
         "GET", "/library/recently-added",
         {"type": {"type": "string", "enum": ["albums", "artists", "tracks"]}, "limit": LIMIT}),
    _api("recently_played", "Recently played", "What was played most recently.",
         "GET", "/library/recently-played", {"limit": LIMIT}),

    # queues
    _api("list_wishlist", "Wishlist", "Tracks waiting to be downloaded.",
         "GET", "/wishlist", {"category": {"type": "string", "enum": ["singles", "albums"]},
                              "page": PAGE, "limit": LIMIT}),
    _api("list_watchlist", "Watchlist", "Artists watched for new releases.", "GET", "/watchlist"),
    _api("list_downloads", "Downloads", "Active and recent downloads.",
         "GET", "/downloads", {"status": _desc(STR, "comma separated, e.g. downloading,queued"),
                               "limit": LIMIT, "offset": {"type": "integer", "minimum": 0}}),
    _api("list_playlists", "Playlists", "Playlists from a connected source.",
         "GET", "/playlists", {"source": _desc(STR, "e.g. spotify, tidal, deezer")}),
    _api("get_request_status", "Request status", "Where a request_track request is at.",
         "GET", "/request/{request_id}", {"request_id": STR}, ("request_id",)),

    # discovery
    _api("recent_releases", "Recent releases", "New releases from artists in the library.",
         "GET", "/discover/recent-releases", {"limit": LIMIT}),
    _api("similar_artists", "Similar artists", "Artists similar to the ones in the library.",
         "GET", "/discover/similar-artists", {"limit": LIMIT}),

    # video
    _api("video_search", "Search movies and shows", "TMDB search for movies, shows and people.",
         "GET", "/video/search", {"q": STR}, ("q",), open_world=True),
    _api("video_library", "Video library", "Movies and shows already in the library.",
         "GET", "/video/library", {"kind": {"type": "string", "enum": ["movies", "shows"]},
                                   "search": STR, "page": PAGE, "limit": LIMIT}),
    _api("video_wishlist", "Video wishlist", "Movies and episodes waiting to be downloaded.",
         "GET", "/video/wishlist", {"kind": {"type": "string", "enum": ["movie", "show"]},
                                    "search": STR, "page": PAGE, "limit": LIMIT}),
    _api("video_downloads", "Video downloads", "Active video downloads.",
         "GET", "/video/downloads", {"status": STR}),
    _api("video_calendar", "Video calendar", "Upcoming and recent episodes and releases.",
         "GET", "/video/calendar", {"start": _desc(STR, "ISO date"), "end": _desc(STR, "ISO date")}),

    # doing things
    _api("search_music", "Search for music",
         "Search the metadata sources for music that isn't owned yet. kind is tracks, albums or artists.",
         "POST", "/search/{kind}", {"kind": {"type": "string", "enum": ["tracks", "albums", "artists"]},
                                    "query": STR, "limit": {**INT, "maximum": 50}}, ("kind", "query"),
         read_only=True, open_world=True),
    _api("request_track", "Download a track",
         "Find and download one track from text like \"Artist - Title\". Returns a request_id.",
         "POST", "/request", {"query": STR}, ("query",), open_world=True),
    _api("process_wishlist", "Run the wishlist", "Start downloading what's on the wishlist now.",
         "POST", "/wishlist/process"),
    _api("remove_from_wishlist", "Remove from wishlist", "Take one track off the wishlist by its track id.",
         "DELETE", "/wishlist/{track_id}", {"track_id": STR}, ("track_id",), destructive=True),
    _api("watch_artist", "Watch an artist",
         "Watch an artist for new releases. artist_id and source come from search_music (kind artists).",
         "POST", "/watchlist", {"artist_id": STR, "artist_name": STR, "source": STR},
         ("artist_id", "artist_name")),
    _api("unwatch_artist", "Stop watching an artist", "Remove an artist from the watchlist.",
         "DELETE", "/watchlist/{artist_id}", {"artist_id": STR}, ("artist_id",), destructive=True),
    _api("scan_watchlist", "Scan the watchlist", "Check watched artists for new releases now.",
         "POST", "/watchlist/scan"),
    _api("cancel_download", "Cancel a download", "Cancel one download. Both values come from list_downloads.",
         "POST", "/downloads/{download_id}/cancel", {"download_id": STR, "username": STR},
         ("download_id", "username"), destructive=True),
    _api("video_wishlist_add", "Want a movie or show",
         "Put a movie, or episodes of a show, on the video wishlist. tmdb ids come from video_search. "
         "Body is {movie: {tmdb_id, title, year}} or {show: {tmdb_id, title}, episodes: [{season_number, "
         "episode_number}]}.",
         "POST", "/video/wishlist", {"movie": {"type": "object"}, "show": {"type": "object"},
                                     "episodes": {"type": "array", "items": {"type": "object"}}}),
    _api("video_follow", "Follow a show, person or studio", "Add to the video watchlist.",
         "POST", "/video/watchlist", {"kind": {"type": "string", "enum": ["show", "person", "studio"]},
                                      "tmdb_id": {"type": "integer"}, "title": STR},
         ("kind", "tmdb_id", "title")),
]


def custom_tools(find_album, request_album) -> List[Tool]:
    return [
        Tool(name="find_album", title="Find an album",
             description="Look an album up on MusicBrainz. Use \"Artist - Album\" for the best match. "
                         "Returns ids for request_album, with track counts so you can pick the right one.",
             schema=_obj({"query": STR, "limit": {**INT, "maximum": 25}}, ("query",)),
             handler=find_album, read_ok=True, open_world=True),
        Tool(name="request_album", title="Download an album",
             description="Put every track of an album on the wishlist so it downloads. album_id comes "
                         "from find_album. Tracks already owned are skipped.",
             schema=_obj({"album_id": STR}, ("album_id",)),
             handler=request_album, read_ok=False, read_only=False, open_world=True),
    ]


def build_request(tool: Tool, args: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any], Optional[Dict[str, Any]]]:
    """(method, path, query, body) for an api tool. path args are taken out of
    the rest. GET sends the rest as query, everything else as a json body."""
    from urllib.parse import quote

    rest = dict(args or {})
    path = tool.path
    for arg in tool.path_args:
        value = str(rest.pop(arg, "") or "")
        path = path.replace("{" + arg + "}", quote(value, safe=""))
    path = "/api/v1" + path
    if tool.method == "GET":
        query = {k: v for k, v in rest.items() if v is not None and v != ""}
        return tool.method, path, query, None
    return tool.method, path, {}, rest


def check_args(tool: Tool, args: Dict[str, Any]) -> Optional[str]:
    """the obvious mistakes, said plainly. the api checks the rest."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    props = tool.schema.get("properties", {})
    for key in tool.schema.get("required", []):
        if args.get(key) in (None, ""):
            return f"{key} is required"
    for key in tool.path_args:
        # an id is one path segment. "../settings" must not walk to another route
        value = str(args.get(key, ""))
        if "/" in value or "\\" in value or value in (".", ".."):
            return f"{key} is not a valid id"
    for key, value in args.items():
        if key not in props:
            return f"unknown argument {key}"
        spec = props[key]
        if "enum" in spec and value not in spec["enum"]:
            return f"{key} must be one of {', '.join(spec['enum'])}"
        kind = spec.get("type")
        if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"{key} must be a whole number"
        if kind == "string" and not isinstance(value, (str, int)):
            return f"{key} must be text"
    return None
