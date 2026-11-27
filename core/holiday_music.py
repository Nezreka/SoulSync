"""holiday music (#2332).

christmas songs carry ordinary genres (pop, soul, jazz), so every genre mix
and generated playlist picked them up all year. this module is the one place
that knows what a holiday song is and when it may play:

- detection: a provider genre or mood (christmas, holiday, xmas...), a known
  title or keyword in the track or album title (jingle bells, navidad,
  hanukkah...), or a hand-set flag on the artist, album or track. the hand
  flag always wins: track over album over artist over the automatic guess.
- the hand flags live in ``holiday_overrides``, keyed by names rather than
  row ids, so a rescan, reimport or retag never undoes them.
- the policy: ``holiday_music.mode`` is ``season`` (default, only between
  ``season_start`` and ``season_end``, Nov 15 to Jan 6), ``always`` or
  ``never``. every generator asks ``policy().keep(track)``.
- ``holiday_music.write_tag`` (off by default) adds a "Holiday" genre to the
  files, so Navidrome, AudioMuse and the rest can filter too.

tracks arrive in many shapes (track_name/title/name, album dict or string,
artist dict, list or string, genres as a list or a json string); ``fields``
reads them all.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("holiday_music")

MODES = ("season", "always", "never")
DEFAULT_MODE = "season"
DEFAULT_START = "11-15"
DEFAULT_END = "01-06"
TAG_GENRE = "Holiday"
SCOPES = ("track", "album", "artist")

# a word or phrase in a track or album title, matched as a whole phrase. the
# bare word "holiday" is never here (madonna's and green day's holiday are not
# christmas songs), and neither is a word that is also a name on its own
# ("noel" gallagher, "rudolph", "carol"): those need their seasonal context
_TITLE_WORDS = (
    r"christmas", r"x-?mas", r"navidad", r"noël", r"(?:first|joyeux|petit papa|the) no[eë]l",
    r"weihnacht\w*", r"buon natale", r"hanukk?ah?", r"chanukk?ah", r"kwanzaa",
    r"yule(?:tide)?", r"mistletoe", r"jingle bells?", r"silent night", r"o holy night",
    r"deck the halls?", r"sleigh ride", r"sleigh bells?", r"winter wonderland",
    r"let it snow", r"rudolph the red", r"frosty the snowman", r"little drummer boy",
    r"joy to the world", r"santa claus", r"santa baby", r"here comes santa",
    r"carol of the bells", r"christmas carols?", r"carols (?:from|for|of|at|by candlelight)",
    r"happy holidays", r"holiday season",
    r"holiday (?:album|songs?|classics|favou?rites|hits|music|edition|special|cheer|party)",
    r"twelve days of", r"12 days of", r"away in a manger", r"hark the herald",
    r"o come all ye faithful", r"god rest ye", r"o tannenbaum", r"stille nacht",
    r"have yourself a merry", r"merry little", r"feliz navidad", r"villancicos?",
)
_TITLE_RE = re.compile(r"(?<![\w])(?:" + "|".join(_TITLE_WORDS) + r")(?![\w])", re.I)
# a title that is only this word is a carol ("Noël", "Noel", "Yule")
_TITLE_ALONE_RE = re.compile(r"\s*(?:no[eë]l(?: no[eë]l)*|natale|yule)\s*[!.]*\s*", re.I)

# people whose name holds a holiday word. their names are taken out of titles
# and tags before anything is matched, as is the track's own artist
_NAMES_WITH_HOLIDAY_WORDS = ("billie holiday", "jennifer holiday", "doc holiday",
                             "the holidays", "holiday sidewinder", "noel gallagher",
                             "noel redding", "noel coward", "carol king", "carole king")

# a featured or credited name inside a title: "(feat. X)", "[with X]", "ft. X"
_CREDIT_RE = re.compile(
    r"[\(\[](?:feat\.?|ft\.?|featuring|with|prod\.?(?: by)?)\s[^\)\]]*[\)\]]"
    r"|\s(?:feat\.?|ft\.?|featuring)\s.*$", re.I)

# a genre or mood tag. a word that can only mean the season counts anywhere in
# a tag ("christmas", "christmas pop", "xmas jazz")
_GENRE_STRONG_RE = re.compile(
    r"(?<![\w])(?:christmas|x-?mas|navidad|noël|weihnacht\w*|hanukk?ah|chanukk?ah|kwanzaa|"
    r"villancicos?|christmas carols?)(?![\w])",
    re.I,
)
# a word that is also a name or an ordinary word ("billie holiday", "holiday
# inn", "noel") counts only when the whole tag is a holiday genre: "holiday",
# "holidays", "holiday music", "happy holidays", "yuletide", "carols"
_GENRE_WHOLE_RE = re.compile(
    r"\s*(?:happy |winter )?(?:holiday|holidays|yule|yuletide|noel|carols)(?: (?:music|songs?|pop|classics|jazz|standards|season|favou?rites|hits|soul|"
    r"rock|album|special|cheer|spirit|carols?|choral))*\s*",
    re.I,
)

_GENRE_KEYS = ("genres", "genre", "album_genres", "track_genres", "tags", "mood", "moods",
               "style", "styles")
# the artist's genres say what the artist usually makes, not what this song
# is: mariah carey is tagged christmas somewhere and still sings in july. only
# the artist's first (main) genre counts, for an act that only makes holiday
# music (mannheim steamroller)
_ARTIST_GENRE_KEYS = ("_artist_genres_raw", "artist_genres")


# ── reading a track, whatever its shape ─────────────────────────────────────

def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "")
    if isinstance(value, (list, tuple)):
        return _text(value[0]) if value else ""
    return "" if value is None else str(value)


def _genre_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                return _genre_list(json.loads(raw))
            except ValueError:
                pass
        return [g.strip() for g in re.split(r"[,;/|]", raw) if g.strip()]
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            out.extend(_genre_list(item.get("name") if isinstance(item, dict) else item))
        return out
    return [str(value)]


def fields_full(track: Any) -> Tuple[str, str, str, List[str], List[str]]:
    """(title, album, artist, genres, artist genres) from any track dict or
    object. the artist genres are each artist field's first genre only"""
    if not isinstance(track, dict):
        track = getattr(track, "__dict__", {}) or {}
    title = _text(track.get("track_name") or track.get("title") or track.get("name"))
    album = _text(track.get("album_name") or track.get("album_title") or track.get("album"))
    artist = _text(track.get("artist_name") or track.get("artist") or track.get("artists")
                   or track.get("album_artist"))
    genres: List[str] = []
    for key in _GENRE_KEYS:
        genres.extend(_genre_list(track.get(key)))
    artist_genres: List[str] = []
    for key in _ARTIST_GENRE_KEYS:
        artist_genres.extend(_genre_list(track.get(key))[:1])
    album_value = track.get("album")
    if isinstance(album_value, dict):
        genres.extend(_genre_list(album_value.get("genres")))
    rich = track.get("track_data_json")
    if isinstance(rich, str) and rich.startswith("{"):
        try:
            rich = json.loads(rich)
        except ValueError:
            rich = None
    if isinstance(rich, dict) and rich is not track:
        r_title, r_album, r_artist, r_genres, r_artist_genres = fields_full(
            {k: v for k, v in rich.items() if k != "track_data_json"})
        title, album, artist = title or r_title, album or r_album, artist or r_artist
        genres.extend(r_genres)
        artist_genres.extend(r_artist_genres)
    return title, album, artist, genres, artist_genres


def fields(track: Any) -> Tuple[str, str, str, List[str]]:
    """(title, album, artist, genres) from any track dict or object"""
    return fields_full(track)[:4]


# ── detection (pure) ────────────────────────────────────────────────────────

def _without_names(text: Any, *names: Any) -> str:
    """a title or tag with the credited names, the track's artists and the
    people whose names hold a holiday word taken out. "The Billie Holiday
    Collection" is not a holiday collection, and "Song (feat. Mike
    Christmas)" is not a christmas song"""
    out = _CREDIT_RE.sub(" ", str(text or ""))
    for name in (*names, *_NAMES_WITH_HOLIDAY_WORDS):
        name = str(name or "").strip()
        if len(name) >= 3:
            out = re.sub(r"(?<![\w])" + re.escape(name) + r"(?![\w])", " ", out, flags=re.I)
    return out


def title_says_holiday(*titles: Any, artist: Any = "") -> bool:
    """a holiday phrase in a track or album title, as a whole phrase. the
    artist's name never counts (billie holiday, mike christmas)"""
    for raw in titles:
        text = _without_names(raw, artist)
        if _TITLE_RE.search(text) or (text.strip() and _TITLE_ALONE_RE.fullmatch(text)):
            return True
    return False


def _tag_says_holiday(tag: Any, artist: Any = "", title: Any = "") -> bool:
    raw = str(tag or "").strip()
    if not raw:
        return False
    # a tag that is the artist's name or the song's title says nothing about
    # the season (last.fm tags billie holiday "billie holiday" and madonna's
    # holiday "holiday")
    if norm(raw) in {norm(artist), norm(title)} - {""}:
        return False
    text = _without_names(raw, artist).strip()
    if not text:
        return False
    return bool(_GENRE_STRONG_RE.search(text) or _GENRE_WHOLE_RE.fullmatch(text))


def genres_say_holiday(genres: Iterable[Any], *, artist: Any = "", title: Any = "") -> bool:
    """a holiday genre or mood among these tags"""
    return any(_tag_says_holiday(g, artist, title) for g in genres or ())


def detect(title: Any = "", album: Any = "", genres: Iterable[Any] = (), *, artist: Any = "",
           artist_genres: Iterable[Any] = ()) -> bool:
    """the automatic guess. genre and mood evidence first: a holiday genre on
    the track or album, or an artist whose main genre is holiday music. then a
    holiday phrase in the track or album title. the artist's name never
    counts, and neither does the bare word "holiday" in a title"""
    return (genres_say_holiday(genres, artist=artist, title=title)
            or genres_say_holiday(artist_genres, artist=artist, title=title)
            or title_says_holiday(title, album, artist=artist))


def album_says_holiday(album: Any = "", genres: Iterable[Any] = (), *, artist: Any = "",
                       artist_genres: Iterable[Any] = ()) -> bool:
    """the same guess for a whole album: only what is true of every track on
    it (the album title, the album's genres, the artist's main genre). a
    track title never decides for its album, so one carol on a pop record
    doesn't move the record"""
    return (genres_say_holiday(genres, artist=artist, title=album)
            or genres_say_holiday(artist_genres, artist=artist, title=album)
            or title_says_holiday(album, artist=artist))


def norm(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_key(scope: str, artist: Any = "", album: Any = "", title: Any = "") -> str:
    """the name key an override is stored under. a track is its artist and
    title (the same song on a single and an album is one song)"""
    if scope == "artist":
        return norm(artist)
    if scope == "album":
        return f"{norm(artist)}|{norm(album)}"
    if scope == "track":
        return f"{norm(artist)}|{norm(title)}"
    raise ValueError(f"unknown scope {scope!r}")


def resolve(title: Any, album: Any, artist: Any, genres: Iterable[Any],
            overrides: Optional[Dict[Tuple[str, str], bool]] = None,
            artist_genres: Iterable[Any] = ()) -> Tuple[bool, str]:
    """(is holiday, why). why is 'track', 'album' or 'artist' when a hand
    flag decided, else 'auto'"""
    if overrides:
        for scope in SCOPES:
            key = match_key(scope, artist, album, title)
            if key.strip("|") and (scope, key) in overrides:
                return overrides[(scope, key)], scope
    return detect(title, album, genres, artist=artist, artist_genres=artist_genres), "auto"


# ── the season (pure) ───────────────────────────────────────────────────────

def parse_day(value: Any, default: str) -> Tuple[int, int]:
    """'MM-DD' as (month, day); a bad value falls back to the default"""
    for raw in (value, default):
        match = re.fullmatch(r"\s*(\d{1,2})-(\d{1,2})\s*", str(raw or ""))
        if match:
            month, day = int(match.group(1)), int(match.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return month, day
    return 11, 15


def in_window(today: date, start: Any = DEFAULT_START, end: Any = DEFAULT_END) -> bool:
    """is today inside the season, ends included. a season that runs over
    new year (Nov 15 to Jan 6) wraps"""
    first = parse_day(start, DEFAULT_START)
    last = parse_day(end, DEFAULT_END)
    now = (today.month, today.day)
    if first <= last:
        return first <= now <= last
    return now >= first or now <= last


# ── the policy ──────────────────────────────────────────────────────────────

@dataclass
class Policy:
    mode: str = DEFAULT_MODE
    start: str = DEFAULT_START
    end: str = DEFAULT_END
    write_tag: bool = False
    overrides: Dict[Tuple[str, str], bool] = field(default_factory=dict)
    today: Optional[date] = None

    def _today(self) -> date:
        return self.today or date.today()

    @property
    def in_season(self) -> bool:
        return in_window(self._today(), self.start, self.end)

    @property
    def allows_holiday(self) -> bool:
        """may a holiday song play in an everyday mix today"""
        if self.mode == "always":
            return True
        if self.mode == "never":
            return False
        return self.in_season

    @property
    def shows_holiday_mix(self) -> bool:
        """the Holiday mix and row: in season, unless holiday music is off"""
        return self.mode != "never" and self.in_season

    def is_holiday(self, track: Any) -> bool:
        title, album, artist, genres, artist_genres = fields_full(track)
        return resolve(title, album, artist, genres, self.overrides, artist_genres)[0]

    def keep(self, track: Any) -> bool:
        return self.allows_holiday or not self.is_holiday(track)

    def filter(self, tracks: Iterable[Any]) -> List[Any]:
        tracks = list(tracks or [])
        if self.allows_holiday:
            return tracks
        return [t for t in tracks if not self.is_holiday(t)]

    def to_json(self) -> Dict[str, Any]:
        return {"mode": self.mode, "season_start": self.start, "season_end": self.end,
                "write_tag": self.write_tag, "in_season": self.in_season,
                "allows_holiday": self.allows_holiday,
                "shows_holiday_mix": self.shows_holiday_mix}


def settings(config: Any = None) -> Dict[str, Any]:
    """the saved holiday settings, every key with its default (an install
    that predates them has none)"""
    if config is None:
        try:
            from core.settings import config_manager as config
        except Exception:  # noqa: BLE001 - no settings, the defaults
            config = None
    get = getattr(config, "get", None)

    def read(key: str, default: Any) -> Any:
        if get is None:
            return default
        try:
            value = get(f"holiday_music.{key}", default)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else value

    mode = str(read("mode", DEFAULT_MODE)).lower()
    start, end = read("season_start", DEFAULT_START), read("season_end", DEFAULT_END)
    return {
        "mode": mode if mode in MODES else DEFAULT_MODE,
        "season_start": "%02d-%02d" % parse_day(start, DEFAULT_START),
        "season_end": "%02d-%02d" % parse_day(end, DEFAULT_END),
        "write_tag": read("write_tag", False) is True,
    }


# overrides are read once a minute at most: every generator run asks
_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "db": None, "overrides": {}}
_CACHE_SECONDS = 60.0


def _database(database: Any = None) -> Any:
    if database is not None:
        return database
    try:
        from database.music_database import get_database
        return get_database()
    except Exception as exc:  # noqa: BLE001 - no db, no overrides
        logger.debug("holiday overrides: no database: %s", exc)
        return None


def load_overrides(database: Any = None) -> Dict[Tuple[str, str], bool]:
    database = _database(database)
    if database is None:
        return {}
    key = id(database)
    with _cache_lock:
        if _cache["db"] == key and time.monotonic() - _cache["at"] < _CACHE_SECONDS:
            return dict(_cache["overrides"])
    out: Dict[Tuple[str, str], bool] = {}
    try:
        with database._get_connection() as conn:
            rows = conn.execute("SELECT scope, match_key, holiday FROM holiday_overrides").fetchall()
        out = {(str(r[0]), str(r[1])): bool(r[2]) for r in rows}
    except Exception as exc:  # noqa: BLE001 - a db without the table has none
        logger.debug("holiday overrides unreadable: %s", exc)
    with _cache_lock:
        _cache.update(at=time.monotonic(), db=key, overrides=dict(out))
    return out


def forget_cache() -> None:
    """a hand flag or a setting changed: the next read sees it"""
    with _cache_lock:
        _cache.update(at=0.0, db=None, overrides={})
        _settings_cache.update(at=0.0, value=None)
        _library_cache.update(at=0.0, db=None, rows=None)


_settings_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def _saved_settings(config: Any) -> Dict[str, Any]:
    """the saved settings, read at most once a minute when no config is
    handed in (the generators ask per track)"""
    if config is not None:
        return settings(config)
    with _cache_lock:
        if _settings_cache["value"] and time.monotonic() - _settings_cache["at"] < _CACHE_SECONDS:
            return dict(_settings_cache["value"])
    value = settings(None)
    with _cache_lock:
        _settings_cache.update(at=time.monotonic(), value=dict(value))
    return value


def policy(database: Any = None, config: Any = None, today: Optional[date] = None) -> Policy:
    saved = _saved_settings(config)
    return Policy(mode=saved["mode"], start=saved["season_start"], end=saved["season_end"],
                  write_tag=saved["write_tag"], overrides=load_overrides(database),
                  today=today)


def keep(track: Any, today: Optional[date] = None) -> bool:
    """one track, the saved policy"""
    return policy(today=today).keep(track)


def filter_tracks(tracks: Iterable[Any], today: Optional[date] = None,
                  database: Any = None) -> List[Any]:
    """the saved policy over a list. never raises: a broken read keeps the
    list as it was"""
    tracks = list(tracks or [])
    if not tracks:
        return tracks
    try:
        return policy(database=database, today=today).filter(tracks)
    except Exception as exc:  # noqa: BLE001
        logger.debug("holiday filter skipped: %s", exc)
        return tracks


# ── hand flags ──────────────────────────────────────────────────────────────

def set_override(database: Any, scope: str, value: Optional[bool], *, artist: Any = "",
                 album: Any = "", title: Any = "") -> Dict[str, Any]:
    """on (True), off (False) or automatic (None) for an artist, album or
    track. automatic removes the hand flag"""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    if not norm(artist):
        raise ValueError("an artist name is needed")
    if scope == "album" and not norm(album):
        raise ValueError("an album title is needed")
    if scope == "track" and not norm(title):
        raise ValueError("a track title is needed")
    key = match_key(scope, artist, album, title)
    database = _database(database)
    with database._get_connection() as conn:
        if value is None:
            conn.execute("DELETE FROM holiday_overrides WHERE scope = ? AND match_key = ?",
                         (scope, key))
        else:
            conn.execute(
                "INSERT INTO holiday_overrides (scope, match_key, holiday, artist, album, title,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)"
                " ON CONFLICT(scope, match_key) DO UPDATE SET holiday = excluded.holiday,"
                " artist = excluded.artist, album = excluded.album, title = excluded.title,"
                " updated_at = CURRENT_TIMESTAMP",
                (scope, key, 1 if value else 0, str(artist or ""), str(album or ""),
                 str(title or "")))
        conn.commit()
    forget_cache()
    return {"scope": scope, "match_key": key, "holiday": value}


def list_overrides(database: Any = None) -> List[Dict[str, Any]]:
    database = _database(database)
    if database is None:
        return []
    try:
        with database._get_connection() as conn:
            rows = conn.execute(
                "SELECT scope, match_key, holiday, artist, album, title, updated_at"
                " FROM holiday_overrides ORDER BY updated_at DESC").fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("holiday overrides unreadable: %s", exc)
        return []
    return [{"scope": r[0], "match_key": r[1], "holiday": bool(r[2]), "artist": r[3],
             "album": r[4], "title": r[5], "updated_at": r[6]} for r in rows]


def status(database: Any = None, *, artist: Any = "", album: Any = "", title: Any = "",
           genres: Iterable[Any] = ()) -> Dict[str, Any]:
    """what an artist, album or track page shows: is it holiday music, and
    is that a hand flag or the automatic guess. the hand flag at this exact
    level is 'override' (None means automatic)"""
    overrides = load_overrides(database)
    holiday, why = resolve(title, album, artist, list(genres or ()), overrides)
    if title:
        scope = "track"
    elif album:
        scope = "album"
    else:
        scope = "artist"
    own = overrides.get((scope, match_key(scope, artist, album, title)))
    return {"holiday": holiday, "decided_by": why, "scope": scope, "override": own,
            "detected": detect(title, album, genres, artist=artist)}


# ── the tag ─────────────────────────────────────────────────────────────────

def tag_genres(genres: Any, track: Dict[str, Any], *, config: Any = None,
               database: Any = None) -> Any:
    """the genres to write to a file: with "Holiday" added when the setting
    is on and the track is holiday music. off (the default) hands the
    genres back untouched. the same shape comes back (a list or a string)"""
    try:
        saved = _saved_settings(config)
        if not saved["write_tag"]:
            return genres
        listed = _genre_list(genres)
        if any(g.strip().lower() == TAG_GENRE.lower() for g in listed):
            return genres
        title, album, artist, own = fields({**track, "genres": [*listed, *_genre_list(track.get("genres"))]})
        if not resolve(title, album, artist, own, load_overrides(database))[0]:
            return genres
    except Exception as exc:  # noqa: BLE001 - a tag write never fails on this
        logger.debug("holiday tag skipped: %s", exc)
        return genres
    if isinstance(genres, str):
        return f"{genres}, {TAG_GENRE}" if genres.strip() else TAG_GENRE
    return [*listed, TAG_GENRE]


def today_for(now: Any = None) -> date:
    if isinstance(now, datetime):
        return now.date()
    if isinstance(now, date):
        return now
    return date.today()


# ── the library's holiday music ─────────────────────────────────────────────

# the words a LIKE prefilter looks for; resolve() has the last word
_LIKE_WORDS = ("christmas", "xmas", "x-mas", "navidad", "noel", "noël", "weihnacht", "natale",
               "hanukkah", "chanukah", "hanuka", "kwanzaa", "yule", "mistletoe", "jingle",
               "silent night", "holy night", "deck the hall", "sleigh", "winter wonderland",
               "let it snow", "rudolph", "frosty", "drummer boy", "joy to the world", "santa",
               "carol", "holiday", "manger", "herald", "tannenbaum", "stille nacht", "merry")

_library_cache: Dict[str, Any] = {"at": 0.0, "db": None, "rows": None}
_LIBRARY_SECONDS = 600.0


def library_tracks(database: Any = None, *, limit: int = 0, use_cache: bool = True) -> List[Dict[str, Any]]:
    """every owned library track that is holiday music: the keyword guess on
    titles and genres, plus the hand flags (a flag off takes a track out).
    rows: id, title, album, artist, album_id, artist_id, duration, cover"""
    database = _database(database)
    if database is None:
        return []
    key = id(database)
    with _cache_lock:
        if (use_cache and _library_cache["db"] == key and _library_cache["rows"] is not None
                and time.monotonic() - _library_cache["at"] < _LIBRARY_SECONDS):
            rows = list(_library_cache["rows"])
            return rows[:limit] if limit else rows
    overrides = load_overrides(database)
    # one string a row, so each word is one LIKE (LIKE is case blind for ascii)
    text = ("(COALESCE(t.title, '') || ' ' || COALESCE(al.title, '') || ' ' || COALESCE(al.genres, '')"
            " || ' ' || COALESCE(ar.genres, '') || ' ' || COALESCE(ar.mood, '') || ' '"
            " || COALESCE(ar.style, ''))")
    likes = [f"{text} LIKE ?" for _ in _LIKE_WORDS]
    params = [f"%{word}%" for word in _LIKE_WORDS]
    on = {"artist": set(), "album": set(), "track": set()}
    for (scope, mkey), value in overrides.items():
        if value:
            on[scope].add(mkey)
    select = (
        "SELECT t.id, t.title, al.title AS album, ar.name AS artist, t.album_id,"
        " al.primary_artist_id AS artist_id, t.duration, COALESCE(al.image_url, ar.image_url) AS cover,"
        " al.genres AS album_genres, ar.genres AS artist_genres, ar.mood, ar.style"
        " FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id"
        " JOIN lib2_artists ar ON ar.id = al.primary_artist_id"
        " WHERE EXISTS (SELECT 1 FROM lib2_track_files f WHERE f.track_id = t.id"
        " AND COALESCE(f.file_state, 'active') = 'active' AND f.path IS NOT NULL AND TRIM(f.path) <> '')")
    found: Dict[int, Dict[str, Any]] = {}
    try:
        with database._get_connection() as conn:
            def fetch(sql: str, args: List[Any]) -> List[Dict[str, Any]]:
                cur = conn.execute(sql, args)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]

            conn_rows = fetch(f"{select} AND ({' OR '.join(likes)})", params)
            # a hand flag no keyword would catch: every track of the flagged
            # artist comes back, and resolve() keeps the flagged ones
            names = {k.split("|")[0] for s in on.values() for k in s if k}
            extra = []
            if names:
                ids = [r[0] for r in conn.execute("SELECT id, name FROM lib2_artists").fetchall()
                       if norm(r[1]) in names]
                for i in range(0, len(ids), 400):
                    chunk = ids[i:i + 400]
                    extra.extend(fetch(
                        f"{select} AND al.primary_artist_id IN ({','.join('?' * len(chunk))})",
                        chunk))
    except Exception as exc:  # noqa: BLE001 - no library tables, no holiday music
        logger.debug("holiday library read failed: %s", exc)
        return []
    for row in conn_rows + extra:
        if row["id"] in found:
            continue
        genres = _genre_list(row.pop("album_genres"))
        artist_genres = [*_genre_list(row.pop("artist_genres"))[:1],
                         *_genre_list(row.pop("mood"))[:1], *_genre_list(row.pop("style"))[:1]]
        if resolve(row["title"], row["album"], row["artist"], genres, overrides, artist_genres)[0]:
            found[row["id"]] = row
    rows = sorted(found.values(), key=lambda r: (norm(r["artist"]), norm(r["album"]), r["id"]))
    with _cache_lock:
        _library_cache.update(at=time.monotonic(), db=key, rows=list(rows))
    return rows[:limit] if limit else rows


def holiday_mix(database: Any = None, *, size: int = 50, seed: Any = None) -> List[Dict[str, Any]]:
    """a shuffled mix of the library's holiday music, three songs an artist
    at most. the same day gives the same mix"""
    import random
    rows = library_tracks(database)
    rng = random.Random(str(seed if seed is not None else date.today().isoformat()))
    rng.shuffle(rows)
    out, per = [], {}
    for r in rows:
        who = norm(r["artist"])
        if per.get(who, 0) >= 3:
            continue
        per[who] = per.get(who, 0) + 1
        out.append(r)
        if len(out) >= size:
            break
    return out


def holiday_album_ids(database: Any = None) -> List[int]:
    """the releases that are holiday music: half or more of their owned
    tracks are (one christmas bonus track doesn't make an album festive).
    the library's Holiday filter reads this"""
    rows = library_tracks(database)
    if not rows:
        return []
    per: Dict[int, int] = {}
    for r in rows:
        per[int(r["album_id"])] = per.get(int(r["album_id"]), 0) + 1
    ids = sorted(per)
    totals: Dict[int, int] = {}
    database = _database(database)
    try:
        with database._get_connection() as conn:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                for album_id, n in conn.execute(
                        "SELECT t.album_id, COUNT(*) FROM lib2_tracks t"
                        f" WHERE t.album_id IN ({','.join('?' * len(chunk))})"
                        " AND EXISTS (SELECT 1 FROM lib2_track_files f WHERE f.track_id = t.id"
                        " AND COALESCE(f.file_state, 'active') = 'active'"
                        " AND f.path IS NOT NULL AND TRIM(f.path) <> '')"
                        " GROUP BY t.album_id", chunk).fetchall():
                    totals[int(album_id)] = int(n)
    except Exception as exc:  # noqa: BLE001
        logger.debug("holiday album counts failed: %s", exc)
        return ids
    return [a for a in ids if per[a] * 2 >= totals.get(a, per[a])]
