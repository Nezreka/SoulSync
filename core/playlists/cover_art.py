"""generate cover art for a playlist with pollinations.

the user asks for it from the playlist's ⋯ menu, nothing runs on its own. we
read what the playlist sounds like (genres, mood, decade) from the library,
turn that into an image prompt, and fetch one image. it lands as a preview
first; only "use this cover" puts it on the playlist.

pollinations works with no key: one image every 45s per ip, their small model,
and their logo in the corner. a key from enter.pollinations.ai lifts all three.
we never crop the logo off the free images, it's their attribution, and the
name we draw on (add_name) stays at the top, clear of it.

prompt rules learned the hard way on the free model: it can't do negation
("no people" draws people), so the unwanted stuff goes in negative_prompt and
the prompt only names light, texture and landscape. words like "rave" or
"party" pull in crowds, so tags like that get dropped.
"""

from __future__ import annotations

import io
import json
import os
import random
import re
import threading
import time
import urllib.parse
from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.playlists import user_playlists as up
from utils.logging_config import get_logger

logger = get_logger("playlists.cover_art")

FREE_URL = "https://image.pollinations.ai/prompt/"
KEYED_URL = "https://gen.pollinations.ai/image/"
# the free tier says 45s. a second of slack so we never eat their 402
FREE_SPACING_SECONDS = 46
REQUEST_TIMEOUT_SECONDS = 90
IMAGE_SIDE = 1024

NEGATIVE_PROMPT = (
    "text, letters, words, typography, watermark, signature, logo, person, people, "
    "face, portrait, woman, man, body, hands, crowd, grid, frame, border"
)

# tags that say nothing visual, or that the model reads as "draw a person"
_DROP_TAG = re.compile(
    r"vocal|singer|songwriter|band|boy|girl|female|male|rapper|choir|crowd|party|rave|"
    r"seen live|favou?rite|beautiful|awesome|under \d|^\d+s?$|unknown|^misc|^other$",
    re.I,
)

# genre words → a scene. matched by substring, first hit wins per genre, so
# the specific ones go before the broad ones (synthwave before wave, etc).
_SCENES: List[tuple] = [
    # what a title says it's for. these never show up as genres, only names
    (("driving", "drive", "road trip", "highway", "cruising"), "an empty highway at dusk with long light trails"),
    (("space", "cosmic", "galaxy", "stars", "astral"), "a deep space nebula and distant stars"),
    (("sleep", "night", "midnight", "late night"), "a quiet starry night over dark hills"),
    (("rain", "rainy", "storm"), "rain on glass with blurred city lights behind it"),
    (("summer", "beach", "sunshine", "pool"), "a sunlit beach with bright turquoise water"),
    (("winter", "snow", "christmas", "holiday"), "a snowy forest under soft blue light"),
    (("workout", "gym", "running", "hype", "power"), "explosive bursts of color and motion"),
    (("focus", "study", "morning", "coffee"), "soft morning light through a quiet window"),
    (("synthwave", "retrowave", "outrun", "vaporwave"), "a retro neon grid under a magenta sunset"),
    (("ambient", "drone", "new age", "meditation"), "drifting mist and soft glowing light over still water"),
    (("lo-fi", "lofi", "chillhop", "chill"), "a cozy window at dusk, soft grain, pastel haze"),
    (("techno", "trance", "electro", "house", "edm", "dance", "drum and bass", "dubstep"),
     "neon geometric shapes and laser beams cutting through dark haze"),
    (("piano", "classical", "orchestral", "baroque", "chamber"), "a calm misty lake in pale morning light"),
    (("soundtrack", "score", "video game", "game", "cinematic", "anime"), "an epic vista under a vast dramatic sky"),
    (("jazz", "swing", "bossa", "blues"), "smoky amber light and art deco curves"),
    (("soul", "r&b", "rnb", "funk", "disco", "motown"), "warm velvet tones and a soft golden glow"),
    (("hip hop", "hip-hop", "rap", "trap", "drill", "grime"), "city lights at night on wet concrete, bold graphic shapes"),
    (("metal", "doom", "sludge"), "dark iron, embers and smoke"),
    (("punk", "grunge", "garage", "hardcore"), "torn paper textures and raw spray-paint color"),
    (("rock", "indie", "alternative", "shoegaze", "post-rock"), "a storm-lit open road with dramatic contrast"),
    (("folk", "country", "americana", "acoustic", "bluegrass"), "golden hour over open fields, wood grain warmth"),
    (("latin", "reggae", "reggaeton", "salsa", "afro", "tropical", "dancehall"), "tropical color, palm shadows and bright sun"),
    (("k-pop", "j-pop", "pop", "dance-pop"), "bright saturated color and glossy floating shapes"),
    (("electronic", "idm", "experimental", "avant-garde", "glitch"), "abstract flowing forms and luminous gradients"),
]

# the art style, rotated per attempt so "try another" really is another
MEDIUMS = (
    "abstract digital painting",
    "textured oil painting",
    "dreamlike landscape photograph",
    "minimal geometric poster art",
    "soft watercolor",
    "surreal 3d render",
)

_DECADE_LOOK = {
    1950: "a faded 1950s print palette",
    1960: "a warm 1960s film palette",
    1970: "a warm grainy 1970s film palette",
    1980: "a bold 1980s neon palette",
    1990: "a hazy 1990s film palette",
    2000: "a glossy early-2000s palette",
}


class CoverArtError(Exception):
    """something the user can read. retry_after is set when waiting fixes it."""

    def __init__(self, message: str, *, retry_after: Optional[int] = None, status: int = 502):
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status


# ── what the playlist sounds like ──────────────────────────────────────────


def _parse_tags(value: Any) -> List[str]:
    """genres/mood columns hold a json list, a json string or a comma list."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        text = str(value).strip()
        items = None
        if text[:1] in ('[', '"'):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    items = [str(v) for v in parsed]
                elif isinstance(parsed, str):
                    items = [parsed]
            except (json.JSONDecodeError, TypeError):
                items = None
        if items is None:
            items = re.split(r"[;,/]", text)
    return [i.strip().lower() for i in items if i and i.strip()]


def _year(value: Any) -> Optional[int]:
    m = re.match(r"\s*(\d{4})", str(value or ''))
    if not m:
        return None
    year = int(m.group(1))
    return year if 1900 <= year <= 2100 else None


def _chunks(items: List[Any], size: int = 400) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def collect_vibe(db: Any, playlist: Dict[str, Any]) -> Dict[str, Any]:
    """top genres, moods and the decade for a playlist.

    tracks in the library give the most (album + artist genres, audiodb mood,
    year). tracks that aren't still tell us their artist, and if we know that
    artist from somewhere else in the library we borrow its genres. the decade
    also comes from discovery's matched release dates. anything missing just
    means a thinner prompt, the name still carries it.
    """
    genres: Counter = Counter()
    moods: Counter = Counter()
    years: List[int] = []
    lib_ids: List[str] = []
    loose_artists: Counter = Counter()

    for row in db.get_mirrored_playlist_tracks(playlist['id']):
        extra = row.get('extra_data')
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (json.JSONDecodeError, TypeError):
                extra = None
        extra = extra if isinstance(extra, dict) else {}
        # library ids are text: plex's are digits, jellyfin and navidrome's aren't
        lib_id = str(extra.get('library_track_id') or '').strip() or None
        if lib_id is not None:
            lib_ids.append(lib_id)
            continue
        artist = str(row.get('artist_name') or '').split(',')[0].strip()
        if artist:
            loose_artists[artist.lower()] += 1
        matched = extra.get('matched_data') if isinstance(extra.get('matched_data'), dict) else {}
        album = matched.get('album') if isinstance(matched.get('album'), dict) else {}
        year = _year(album.get('release_date'))
        if year:
            years.append(year)

    try:
        with db._get_connection() as conn:
            cur = conn.cursor()
            for chunk in _chunks(lib_ids):
                marks = ",".join("?" * len(chunk))
                cur.execute(
                    "SELECT COALESCE(NULLIF(al.genres, ''), ar.genres), ar.style, "
                    "COALESCE(t.mood, al.mood, ar.mood), COALESCE(t.year, al.year) "
                    "FROM tracks t LEFT JOIN albums al ON al.id = t.album_id "
                    "LEFT JOIN artists ar ON ar.id = t.artist_id "
                    f"WHERE t.id IN ({marks})",
                    chunk,
                )
                for g, style, mood, year in cur.fetchall():
                    for tag in _parse_tags(g) + _parse_tags(style):
                        genres[tag] += 1
                    for tag in _parse_tags(mood):
                        moods[tag] += 1
                    y = _year(year)
                    if y:
                        years.append(y)
            # the 200 most common artists is plenty to read a vibe
            names = [n for n, _ in loose_artists.most_common(200)]
            for chunk in _chunks(names):
                marks = ",".join("?" * len(chunk))
                cur.execute(
                    "SELECT LOWER(name), genres, style, mood FROM artists "
                    f"WHERE LOWER(name) IN ({marks})",
                    chunk,
                )
                seen = set()
                for name, g, style, mood in cur.fetchall():
                    # one row per artist, a library can hold the same name per server
                    if name in seen:
                        continue
                    seen.add(name)
                    weight = loose_artists.get(name, 1)
                    for tag in _parse_tags(g) + _parse_tags(style):
                        genres[tag] += weight
                    for tag in _parse_tags(mood):
                        moods[tag] += weight
    except Exception as e:  # noqa: BLE001
        logger.debug("cover art vibe lookup failed for playlist %s: %s", playlist.get('id'), e)

    decade = None
    if years:
        decades = Counter(y // 10 * 10 for y in years)
        top, count = decades.most_common(1)[0]
        # only when the playlist really leans one way
        if count / len(years) >= 0.5:
            decade = top

    top_mood = moods.most_common(1)[0][1] if moods else 0
    return {
        'name': str(playlist.get('custom_name') or playlist.get('name') or '').strip(),
        'genres': [g for g, _ in genres.most_common(12) if not _DROP_TAG.search(g)][:4],
        # audiodb moods are noisy, a mood one track has says nothing
        'moods': [m for m, n in moods.most_common(8)
                  if n >= 2 and n * 4 >= top_mood and not _DROP_TAG.search(m)][:3],
        'decade': decade,
    }


# ── the prompt ─────────────────────────────────────────────────────────────


# genres that fit everything. they only pick the scene when nothing else does
_BROAD = {"electronic", "pop", "rock", "alternative", "indie", "urban", "world", "dance"}


def _matches(key: str, text: str) -> bool:
    # whole words, so "electro" doesn't catch "electronic"
    return re.search(r"(?<![a-z])" + re.escape(key) + r"(?![a-z])", text) is not None


def _scene_for(genres: List[str], name: str = '') -> Optional[str]:
    """the scene that fits best. the name counts most, it's what the person
    called it ("Calm Piano"), then genres by rank, broad ones at a quarter."""
    scores: Counter = Counter()
    order = {}
    for i, (keys, scene) in enumerate(_SCENES):
        order[scene] = i
        if name and any(_matches(k, name.lower()) for k in keys):
            scores[scene] += 10
        for rank, genre in enumerate(genres):
            if any(_matches(k, genre) for k in keys):
                weight = len(genres) - rank
                scores[scene] += weight / 4 if genre in _BROAD else weight
    if not scores:
        return None
    # ties go to the earlier, more specific scene
    return max(scores, key=lambda sc: (scores[sc], -order[sc]))


def _decade_from_name(name: str) -> Optional[int]:
    m = re.search(r"(?<!\d)(19|20)?(\d)0'?s\b", name)
    if not m:
        return None
    century = m.group(1) or ('20' if m.group(2) in '012' else '19')
    return int(century + m.group(2) + '0')


def _clean_name(name: str) -> str:
    # quotes and brackets make the model letter the title onto the image
    name = re.sub(r"[\"'“”‘’()\[\]{}<>#@|_*~—–-]+", " ", name)
    return " ".join(name.split())[:80]


def build_prompt(vibe: Dict[str, Any], attempt: int = 0) -> str:
    """the image prompt. positive words only, see the module note."""
    genres = vibe.get('genres') or []
    moods = vibe.get('moods') or []
    # the name can carry the whole vibe ("Calm Piano", "Techno Tunnel")
    name = _clean_name(vibe.get('name') or '')
    scene = _scene_for(genres, name)
    medium = MEDIUMS[attempt % len(MEDIUMS)]

    parts = [f"{medium} of {scene}" if scene else f"{medium}, an atmospheric abstract scene"]
    if moods:
        parts.append(f"mood: {', '.join(moods)}")
    if genres:
        parts.append(f"evoking {', '.join(genres)} music")
    if name:
        parts.append(f"inspired by the feeling of {name}")
    look = _DECADE_LOOK.get(_decade_from_name(name) or vibe.get('decade') or 0)
    if look:
        parts.append(look)
    parts.append("square composition, rich color, high detail, album artwork")
    return ". ".join(parts)


# ── pollinations ───────────────────────────────────────────────────────────

_throttle_lock = threading.Lock()
_next_free_at = 0.0


def _retry_after(text: str, default: int) -> int:
    m = re.search(r"retry after (\d+)\s*s", text or '', re.I)
    return int(m.group(1)) if m else default


def _reset_throttle() -> None:
    """tests only."""
    global _next_free_at
    with _throttle_lock:
        _next_free_at = 0.0


def _claim_free_slot(now: float) -> None:
    """one free image per 46s for the whole server: pollinations counts per ip,
    and every profile on this box shares one."""
    global _next_free_at
    with _throttle_lock:
        if now < _next_free_at:
            wait = int(_next_free_at - now) + 1
            raise CoverArtError(
                f"Free images are one every 45 seconds. Try again in {wait}s.",
                retry_after=wait, status=429,
            )
        _next_free_at = now + FREE_SPACING_SECONDS


def _push_free_slot(now: float, seconds: int) -> None:
    global _next_free_at
    with _throttle_lock:
        _next_free_at = max(_next_free_at, now + seconds)


def generate_image(
    prompt: str,
    *,
    api_key: Optional[str] = None,
    seed: Optional[int] = None,
    http_get: Optional[Callable[..., Any]] = None,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """one image from pollinations, as raw bytes. raises CoverArtError."""
    if http_get is None:
        import requests
        http_get = requests.get
    key = (api_key or '').strip()
    seed = int(seed if seed is not None else random.randint(1, 2_000_000_000))
    path = urllib.parse.quote(prompt, safe='')
    if key:
        url = KEYED_URL + path
        params = {'width': IMAGE_SIDE, 'height': IMAGE_SIDE, 'seed': seed}
        headers = {'Authorization': f'Bearer {key}'}
    else:
        _claim_free_slot(clock())
        url = FREE_URL + path
        params = {'width': IMAGE_SIDE, 'height': IMAGE_SIDE, 'seed': seed,
                  'negative_prompt': NEGATIVE_PROMPT}
        headers = {}

    try:
        resp = http_get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001
        logger.warning("pollinations request failed: %s", e)
        raise CoverArtError("Couldn't reach Pollinations. Try again in a bit.") from None

    status = getattr(resp, 'status_code', 0)
    ctype = str((getattr(resp, 'headers', None) or {}).get('content-type', '')).lower()
    body = getattr(resp, 'content', b'') or b''
    if status == 200 and ctype.startswith('image/') and len(body) > 1000:
        return body

    text = ''
    try:
        text = str((resp.json().get('error') or {}).get('message') or '')
    except Exception:  # noqa: BLE001
        text = (getattr(resp, 'text', '') or '')[:300]
    logger.info("pollinations said %s: %s", status, text[:200])

    if status == 402 and not key:
        wait = _retry_after(text, FREE_SPACING_SECONDS)
        _push_free_slot(clock(), wait)
        raise CoverArtError(
            f"Free images are one every 45 seconds. Try again in {wait}s.",
            retry_after=wait, status=429,
        )
    if status == 503:
        wait = _retry_after(text, 10)
        if not key:
            # a 503 still spends the free slot: their next answer was "wait 31s",
            # not the 10s this one promised
            wait = max(wait, FREE_SPACING_SECONDS)
            _push_free_slot(clock(), wait)
        raise CoverArtError(
            f"Pollinations is busy right now. Try again in {wait}s.",
            retry_after=wait, status=503,
        )
    if status in (401, 403):
        raise CoverArtError("Pollinations didn't accept the API key. Check it in Settings.", status=400)
    if status == 402:
        raise CoverArtError("Your Pollinations key is out of pollen.", status=400)
    if status == 429:
        wait = _retry_after(text, 30)
        raise CoverArtError(f"Too many images at once. Try again in {wait}s.", retry_after=wait, status=429)
    raise CoverArtError("Pollinations couldn't make that one. Try again.")


# ── preview, apply, restore ────────────────────────────────────────────────
# generated covers live beside the uploaded ones and serve from the same
# route, so the sync push already knows how to hand them to plex as a file.


KEY_CHECK_URL = "https://gen.pollinations.ai/account/key"
# public, needs no key: just proves pollinations answers
MODELS_URL = "https://gen.pollinations.ai/image/models"
CHECK_TIMEOUT_SECONDS = 15


def check_key(
    api_key: Optional[str] = None,
    *,
    http_get: Optional[Callable[..., Any]] = None,
) -> tuple:
    """the settings test button: (ok, message). asks pollinations about the
    key itself, so it never spends pollen or the free tier's one slot. no key
    is fine, the free tier needs none, so then it only checks they answer"""
    if http_get is None:
        import requests
        http_get = requests.get
    key = (api_key or '').strip()
    try:
        if key:
            resp = http_get(KEY_CHECK_URL, headers={'Authorization': f'Bearer {key}'},
                            timeout=CHECK_TIMEOUT_SECONDS)
        else:
            resp = http_get(MODELS_URL, timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001
        logger.warning("pollinations check failed: %s", e)
        return False, "Couldn't reach Pollinations."
    status = getattr(resp, 'status_code', 0)
    if not key:
        if status == 200:
            return True, ("Pollinations is reachable. No key: one image every 45 seconds, "
                          "with a small Pollinations mark.")
        return False, f"Pollinations answered {status}. Try again in a bit."
    if status == 200:
        return True, "Pollinations accepted the key."
    if status == 401:
        return False, "Pollinations didn't accept the API key."
    if status == 403:
        return False, "That key can't make images. Give it image access at enter.pollinations.ai."
    return False, f"Pollinations answered {status}. Try again in a bit."


# ── the name on the cover ───────────────────────────────────────────────
#
# the playlist's name in big type top-left, a small source line above it, on
# a dark fade from the top. the bottom of the image is never touched, so the
# free tier's mark stays as pollinations drew it.

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "video", "overlays", "fonts")
TITLE_FONT = "Inter-ExtraBold.ttf"
LABEL_FONT = "Inter-SemiBold.ttf"

SOURCE_LABELS = {
    "spotify": "Spotify playlist",
    "spotify_public": "Spotify playlist",
    "tidal": "Tidal playlist",
    "deezer": "Deezer playlist",
    "qobuz": "Qobuz playlist",
    "youtube": "YouTube playlist",
    "ytmusic": "YouTube Music playlist",
    "beatport": "Beatport chart",
    "lastfm": "Last.fm playlist",
    "listenbrainz": "ListenBrainz playlist",
    "metasync": "SoulSync Charts",
    "soulsync_discovery": "SoulSync mix",
    "newpipe": "NewPipe playlist",
}


def source_label(playlist: Dict[str, Any]) -> str:
    """the small line over the name. yours are soulsync playlists."""
    if up.is_user_playlist(playlist):
        return "SoulSync playlist"
    return SOURCE_LABELS.get(str(playlist.get("source") or "").lower(), "Playlist")


def _wrap(draw: Any, text: str, font: Any, width: float) -> List[str]:
    lines: List[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if not line or draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit_title(draw: Any, text: str, side: int, width: float) -> tuple:
    """the biggest size the name fits in 3 lines at, down to a floor. past
    that the last line gets an ellipsis."""
    from PIL import ImageFont

    path = os.path.join(FONT_DIR, TITLE_FONT)
    size = round(side * 0.097)
    floor = round(side * 0.06)
    while True:
        font = ImageFont.truetype(path, size)
        lines = _wrap(draw, text, font, width)
        fits = len(lines) <= 3 and all(draw.textlength(ln, font=font) <= width for ln in lines)
        if fits or size <= floor:
            break
        size -= 2
    if not fits:
        lines = lines[:3]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return font, lines, size


def add_name(image: bytes, name: str, label: str) -> bytes:
    """the cover with its name on it. image is a square webp from _encode_cover."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(io.BytesIO(image)).convert("RGBA")
    side = img.width
    name = " ".join((name or "").split()) or "Playlist"

    # a fade from the top: dark behind the text, gone by halfway down
    reach = side // 2
    fade = Image.new("L", (1, side), 0)
    for y in range(reach):
        fade.putpixel((0, y), int(255 * 0.78 * (1 - y / reach) ** 1.6))
    shade = Image.new("RGBA", (side, side), (8, 8, 12, 255))
    shade.putalpha(fade.resize((side, side)))
    img = Image.alpha_composite(img, shade)

    draw = ImageDraw.Draw(img)
    pad = round(side * 0.0625)
    label_font = ImageFont.truetype(os.path.join(FONT_DIR, LABEL_FONT), round(side * 0.027))
    draw.text((pad, pad), label.upper(), font=label_font, fill=(235, 235, 240, 255))
    font, lines, size = _fit_title(draw, name, side, side - 2 * pad)
    y = pad + round(side * 0.047)
    for line in lines:
        draw.text((pad, y), line, font=font, fill=(255, 255, 255, 255))
        y += round(size * 1.1)

    out = io.BytesIO()
    img.save(out, "WEBP", quality=88)
    return out.getvalue()


def preview_path(playlist_id: int, base_dir: Optional[str] = None, *, mkdir: bool = True) -> str:
    return os.path.join(up.cover_dir(base_dir, mkdir=mkdir), f"preview-{int(playlist_id)}.webp")


def raw_preview_path(playlist_id: int, base_dir: Optional[str] = None, *, mkdir: bool = True) -> str:
    """the image as pollinations made it, so the name can come off and go back
    on without spending another image."""
    return os.path.join(up.cover_dir(base_dir, mkdir=mkdir), f"preview-{int(playlist_id)}-raw.webp")


def _storage_denied(err: OSError, base_dir: Optional[str]) -> "CoverArtError":
    """the cover folder isn't writable. on docker that's an owner that doesn't
    match PUID/PGID, which used to surface as "Couldn't make a cover" and read
    like pollinations being down (SeadogsBooty: /app/storage/playlist_covers
    owned 1000:1000, the app running as 1026:100)."""
    folder = os.path.dirname(getattr(err, "filename", "") or "") or up.cover_dir(base_dir, mkdir=False)
    who = ""
    if hasattr(os, "getuid"):
        who = " (SoulSync runs as %s:%s)" % (os.getuid(), os.getgid())
    return CoverArtError(
        "SoulSync can't save covers: %s isn't writable%s. Give it the same owner "
        "as your PUID/PGID, or restart the container to let it fix that." % (folder, who),
        status=500)


def _write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _render_preview(playlist: Dict[str, Any], raw: bytes, show_name: bool,
                    base_dir: Optional[str]) -> None:
    data = add_name(raw, playlist.get("name") or "", source_label(playlist)) if show_name else raw
    _write(preview_path(playlist['id'], base_dir), data)


def set_preview_name(playlist: Dict[str, Any], show_name: bool, *,
                     base_dir: Optional[str] = None) -> Dict[str, Any]:
    """put the name on the preview or take it off. no new image."""
    raw_path = raw_preview_path(playlist['id'], base_dir, mkdir=False)
    if not os.path.isfile(raw_path):
        raise CoverArtError("That preview is gone. Generate a new one.", status=404)
    with open(raw_path, "rb") as fh:
        raw = fh.read()
    try:
        _render_preview(playlist, raw, show_name, base_dir)
    except PermissionError as e:
        raise _storage_denied(e, base_dir) from None
    return {'preview_url': preview_url(playlist['id']), 'show_name': bool(show_name)}


def preview_url(playlist_id: int) -> str:
    return f"/api/mirrored-playlists/{int(playlist_id)}/cover-art/preview?v={int(time.time() * 1000)}"


def make_preview(
    db: Any,
    playlist: Dict[str, Any],
    *,
    api_key: Optional[str] = None,
    attempt: int = 0,
    show_name: bool = True,
    base_dir: Optional[str] = None,
    http_get: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """generate one image and park it as this playlist's preview. nothing on
    the playlist changes until apply_preview."""
    vibe = collect_vibe(db, playlist)
    prompt = build_prompt(vibe, attempt)
    raw = generate_image(prompt, api_key=api_key, http_get=http_get)
    try:
        data = up._encode_cover(raw)
    except up.UserPlaylistError:
        raise CoverArtError("Pollinations sent back something that isn't an image. Try again.") from None
    try:
        _write(raw_preview_path(playlist['id'], base_dir), data)
        _render_preview(playlist, data, show_name, base_dir)
    except PermissionError as e:
        raise _storage_denied(e, base_dir) from None
    logger.info("cover art preview for playlist %s (attempt %s)", playlist['id'], attempt)
    return {
        'preview_url': preview_url(playlist['id']),
        'vibe': vibe,
        'prompt': prompt,
        'keyed': bool((api_key or '').strip()),
        'show_name': bool(show_name),
    }


def apply_preview(db: Any, playlist: Dict[str, Any], *, base_dir: Optional[str] = None) -> str:
    """the preview becomes the cover. a mirror's is marked custom so a refresh
    from the source keeps it; a user playlist has no source, same as an upload."""
    src = preview_path(playlist['id'], base_dir, mkdir=False)
    if not os.path.isfile(src):
        raise CoverArtError("That preview is gone. Generate a new one.", status=404)
    try:
        os.replace(src, up.cover_path(playlist['id'], base_dir))
    except PermissionError as e:
        raise _storage_denied(e, base_dir) from None
    try:
        os.remove(raw_preview_path(playlist['id'], base_dir, mkdir=False))
    except OSError:
        pass
    url = up.cover_url(playlist['id'])
    if up.is_user_playlist(playlist):
        ok = db.set_mirrored_playlist_image_url(playlist['id'], url, profile_id=playlist.get('profile_id'))
    else:
        ok = db.set_mirrored_playlist_custom_cover(playlist['id'], url, profile_id=playlist.get('profile_id'))
    if not ok:
        raise RuntimeError('could not save the cover')
    return url


def discard_preview(playlist_id: int, base_dir: Optional[str] = None) -> None:
    for path in (preview_path(playlist_id, base_dir, mkdir=False),
                 raw_preview_path(playlist_id, base_dir, mkdir=False)):
        try:
            os.remove(path)
        except OSError:
            pass


def restore_original(db: Any, playlist: Dict[str, Any], *, base_dir: Optional[str] = None) -> None:
    """a mirror goes back to the source's own cover. the generated file is
    deleted, nothing else points at it."""
    if not db.restore_mirrored_playlist_source_cover(playlist['id'], profile_id=playlist.get('profile_id')):
        raise RuntimeError('could not restore the cover')
    try:
        os.remove(up.cover_path(playlist['id'], base_dir, mkdir=False))
    except OSError:
        pass
