"""The download tagging pipeline, held to what it says it does.

Eleven faults found in a line-by-line audit of post-processing and tagging,
each proved here against REAL audio files (tests/fixtures/audio: one second
of a sine wave as FLAC, MP3, M4A, Ogg Vorbis and Opus) rather than stand-ins,
because most of them only show on a real container:

 1. the MusicBrainz duration gate (#1509) and the file's own ISRC (#1705)
    never ran: the probe asked mutagen to open ``str(<opened file>)``;
 2. the release's album artist (#2259) was worked out and never written;
 3. "Tyler, The Creator" was looked up as "Tyler";
 4. enrichment with no ids to embed (Last.fm genres, a Qobuz label) was
    dropped by an early return;
 5. Ogg Vorbis and Opus lost their cover and were never given one;
 6. a JPEG served as image/jpg, and any WebP, went into an M4A marked PNG;
 7. ReplayGain was wiped, the compilation flag never written, and a file's
    APE tags were stripped before it was known the tagging could go ahead;
 8. an M4A's artist repeated the guests already in its title;
 9. a real track 1 was renumbered from its file name, and an unknown track
    number was written as 0;
10. "EDM" became "Edm", "6 ft Under" hid a guest, and a reference artist
    left the artist list behind;
11. read_tag couldn't read what write_tag wrote.
"""

from __future__ import annotations

import base64
import io
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("mutagen")
import mutagen  # noqa: E402
from mutagen.flac import Picture  # noqa: E402

import core.metadata.enrichment as enrichment  # noqa: E402
from core.metadata import source as ms  # noqa: E402
from core.metadata.common import get_mutagen_symbols  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "audio"
FORMATS = ("flac", "mp3", "m4a", "ogg", "opus")
SYMBOLS = get_mutagen_symbols()


def _audio(tmp_path, ext, name="track"):
    dest = tmp_path / f"{name}.{ext}"
    shutil.copy(FIXTURES / f"silence.{ext}", dest)
    return str(dest)


def _open(path):
    audio = mutagen.File(path)
    if audio.tags is None:
        audio.add_tags()
    return audio


def _tag(audio, id3, vorbis, mp4):
    """one tag's values, whatever the container"""
    if isinstance(audio.tags, SYMBOLS.ID3):
        frames = audio.tags.getall(id3)
        return [str(t) for f in frames for t in getattr(f, "text", [])]
    if isinstance(audio, SYMBOLS.MP4):
        value = audio.get(mp4)
        if value is None:
            return []
        return [v.decode("utf-8") if isinstance(v, bytes) else v for v in value]
    return list(audio.get(vorbis) or [])


class Cfg:
    def __init__(self, **values):
        self.values = {k.replace("__", "."): v for k, v in values.items()}

    def get(self, key, default=None):
        # no cover is looked up unless a test asks: the network isn't in these
        if key == "metadata_enhancement.embed_album_art" and key not in self.values:
            return False
        return self.values.get(key, default)


CORE = {"title": "Yellow", "artist": "Coldplay", "album_artist": "Coldplay", "album": "Parachutes",
        "track_number": 5, "total_tracks": 10, "disc_number": 1}


def _enhance(path, metadata, *, cfg=None, embed=None, art=None, extract=None, strip=None):
    """the real enhance_file_metadata on a real file; only the network-facing
    steps are stood in for"""
    cfg = cfg or Cfg()
    with patch.object(enrichment, "get_config_manager", return_value=cfg), \
         patch.object(enrichment, "strip_all_non_audio_tags", side_effect=strip) as stripped, \
         patch.object(enrichment, "extract_source_metadata",
                      side_effect=extract or (lambda *a, **k: dict(metadata) if metadata else None)), \
         patch.object(enrichment, "embed_source_ids", side_effect=embed), \
         patch.object(enrichment, "verify_metadata_written", return_value=True), \
         patch.object(enrichment, "embed_album_art_metadata", side_effect=art, return_value=False):
        ok = enrichment.enhance_file_metadata(path, context={}, artist={"name": "Coldplay"}, album_info={})
    return ok, stripped


def _jpeg(size=(40, 40)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _png(size=(40, 40)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 200, 30)).save(buf, "PNG")
    return buf.getvalue()


def _webp(size=(40, 40)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 30, 200)).save(buf, "WEBP")
    return buf.getvalue()


# ── 1. the duration gate and the file's own ISRC ────────────────────────────

@pytest.mark.parametrize("ext", FORMATS)
def test_the_probe_reads_the_opened_file_it_is_given(tmp_path, ext):
    """enrichment hands the probe the OPENED mutagen file, with its tags
    already cleared in memory. str() of that is its tag list, not its path,
    so the probe never opened anything and both gates were dead."""
    path = _audio(tmp_path, ext)
    on_disk = _open(path)
    from core.metadata.musicbrainz_tags import write_tag
    write_tag(on_disk, "ISRC", "GBAYE0000351", SYMBOLS)
    on_disk.save()

    audio = _open(path)
    audio.tags.clear()                       # as enrichment has done by then; not saved
    isrc, duration_ms = ms._audio_file_probes(audio)
    assert isrc == "GBAYE0000351"            # the file on disk still carries it
    assert 900 <= duration_ms <= 1200
    assert 900 <= ms._audio_file_duration_ms(audio) <= 1200


def test_the_probe_still_takes_a_path(tmp_path):
    path = _audio(tmp_path, "flac")
    isrc, duration_ms = ms._audio_file_probes(path)
    assert isrc is None and 900 <= duration_ms <= 1200
    assert ms._audio_file_probes(Path(path))[1] == duration_ms
    assert ms._audio_file_probes(None) == (None, None)
    assert ms._audio_file_probes(str(tmp_path / "missing.flac")) == (None, None)


# ── 2. the release's album artist reaches the file ──────────────────────────

@pytest.mark.parametrize("ext", FORMATS)
def test_an_album_artist_corrected_during_enrichment_is_written(tmp_path, ext):
    """#2259: MusicBrainz says the EP is by 12th Planet though this track is
    DJ Evol's. the correction landed in the metadata after the album artist
    tag had been written, so the file kept the track artist and the EP split."""
    path = _audio(tmp_path, ext)
    meta = {**CORE, "artist": "DJ Evol", "album_artist": "DJ Evol", "album": "The End"}

    def embed(audio_file, metadata, context, runtime=None, on_resolved=None):
        metadata["album_artist"] = "12th Planet"

    ok, _ = _enhance(path, meta, embed=embed)
    assert ok
    audio = _open(path)
    assert _tag(audio, "TPE2", "albumartist", "aART") == ["12th Planet"]
    assert _tag(audio, "TPE1", "artist", "\xa9ART") == ["DJ Evol"]


def test_an_unchanged_album_artist_is_left_as_written(tmp_path):
    path = _audio(tmp_path, "flac")
    ok, _ = _enhance(path, dict(CORE), embed=lambda *a, **k: None)
    assert ok and _tag(_open(path), "TPE2", "albumartist", "aART") == ["Coldplay"]


# ── 3. an artist's own comma ────────────────────────────────────────────────

def test_a_name_with_a_comma_is_looked_up_whole():
    names = ms._mb_artist_lookup_names
    # the artist list says who the first artist is, comma and all
    assert names({"artist": "Tyler, The Creator", "_artists_list": ["Tyler, The Creator"]}, "x") == \
        ["Tyler, The Creator"]
    assert names({"artist": "The Weeknd, Daft Punk", "_artists_list": ["The Weeknd", "Daft Punk"]}, "x") == \
        ["The Weeknd"]
    # no list: the whole name first, its first part only as a fallback
    assert names({"artist": "Earth, Wind & Fire"}, "x") == ["Earth, Wind & Fire", "Earth"]
    assert names({"artist": "Coldplay"}, "x") == ["Coldplay"]
    assert names({}, "Fallback Artist") == ["Fallback Artist"]


def test_the_lookup_tries_the_whole_name_before_its_first_part(monkeypatch):
    asked = []

    def lookup(_service, name):
        asked.append(name)
        return {"mbid": "m-ewf"} if name == "Earth, Wind & Fire" else None

    monkeypatch.setattr(ms, "_cached_mb_artist", lookup)
    assert ms._resolve_mb_artist(object(), {"artist": "Earth, Wind & Fire"}, "x") == {"mbid": "m-ewf"}
    assert asked == ["Earth, Wind & Fire"]
    asked.clear()
    monkeypatch.setattr(ms, "_cached_mb_artist",
                        lambda _s, name: (asked.append(name), {"mbid": "m-a"} if name == "A" else None)[1])
    assert ms._resolve_mb_artist(object(), {"artist": "A, B"}, "x") == {"mbid": "m-a"}
    assert asked == ["A, B", "A"]


# ── 4. enrichment with no ids to embed ──────────────────────────────────────

def _blank_pp(**over):
    pp = {"id_tags": {}, "mb_genres": [], "isrc": None, "deezer_bpm": None, "deezer_isrc": None,
          "tidal_bpm": None, "hifi_bpm": None, "hifi_copyright": None, "audiodb_mood": None,
          "audiodb_style": None, "audiodb_genre": None, "tidal_isrc": None, "tidal_copyright": None,
          "hifi_isrc": None, "qobuz_isrc": None, "qobuz_copyright": None, "qobuz_label": None,
          "lastfm_tags": [], "lastfm_url": None, "genius_url": None, "release_year": None,
          "bandcamp_url": None, "bandcamp_tags": [], "bandcamp_label": None, "beatport": None}
    pp.update(over)
    return pp


@pytest.mark.parametrize("field,value", [
    ("lastfm_tags", ["shoegaze"]), ("lastfm_url", "https://last.fm/x"), ("genius_url", "https://genius.com/x"),
    ("qobuz_isrc", "GB1"), ("qobuz_copyright", "(C) 2020"), ("qobuz_label", "4AD"),
    ("tidal_isrc", "GB2"), ("tidal_copyright", "(P) 2020"), ("hifi_isrc", "GB3"),
    ("audiodb_genre", "Rock"), ("mb_genres", ["rock"]), ("isrc", "GB4"), ("release_year", "1999"),
    ("bandcamp_label", "Sub Pop"), ("deezer_bpm", 120), ("audiodb_mood", "Happy"),
    ("id_tags", {"MUSICBRAINZ_RECORDING_ID": "x"}), ("beatport", {"key": "Am"}),
])
def test_anything_the_writer_writes_keeps_the_writer_running(field, value):
    """a Soulseek download with no ids and Last.fm genres: the early return
    listed a dozen fields by hand and these weren't among them, so the genres
    (or the Qobuz label, or the MusicBrainz ISRC) were fetched and dropped."""
    assert ms._has_embedded_metadata_to_write(_blank_pp(**{field: value}), {}, False) is True


def test_nothing_to_write_still_returns_early():
    assert ms._has_embedded_metadata_to_write(_blank_pp(), {}, False) is False
    assert ms._has_embedded_metadata_to_write(_blank_pp(), {}, True) is True      # the genre fallback is on
    assert ms._has_embedded_metadata_to_write(_blank_pp(_depot={"marks": []}), {}, False) is True


# ── 5. ogg and opus keep, and get, their cover ──────────────────────────────

def _ogg_picture(audio):
    blocks = audio.get("metadata_block_picture") or []
    return Picture(base64.b64decode(blocks[0])) if blocks else None


@pytest.mark.parametrize("ext", ("ogg", "opus"))
def test_ogg_and_opus_are_given_their_cover(tmp_path, ext):
    """there was no Ogg branch: nothing was written, the log said "Album art
    successfully embedded" and the caller was told True."""
    from core.metadata import artwork
    path = _audio(tmp_path, ext)
    audio = _open(path)
    assert artwork._write_embedded_art(audio, _jpeg((64, 48)), "image/jpeg", SYMBOLS) is True
    audio.save()
    pic = _ogg_picture(_open(path))
    assert pic is not None and pic.mime == "image/jpeg" and pic.type == 3
    assert (pic.width, pic.height) == (64, 48) and pic.data[:2] == b"\xff\xd8"


@pytest.mark.parametrize("ext", ("ogg", "opus"))
def test_ogg_and_opus_keep_the_cover_they_came_with(tmp_path, ext):
    """the tag clear wipes an Ogg file's picture (it is a comment there), and
    the snapshot skipped Ogg, so a file that arrived with a cover lost it."""
    from core.metadata import artwork
    path = _audio(tmp_path, ext)
    audio = _open(path)
    artwork._write_embedded_art(audio, _png(), "image/png", SYMBOLS)
    audio.save()

    ok, _ = _enhance(path, dict(CORE))          # no new art is found
    assert ok
    after = _open(path)
    pic = _ogg_picture(after)
    assert pic is not None and pic.mime == "image/png"
    assert _tag(after, "TIT2", "title", "\xa9nam") == ["Yellow"]


def test_a_container_that_takes_no_cover_is_not_called_a_success(tmp_path):
    from core.metadata import artwork

    class Wav:
        tags = None

    assert artwork._write_embedded_art(Wav(), _jpeg(), "image/jpeg", SYMBOLS) is False


# ── 6. the cover is labelled as what it is ──────────────────────────────────

def test_a_cover_is_known_by_its_bytes_not_its_label():
    from core.metadata.cover_art import normalize_cover_art, sniff_image_mime
    assert sniff_image_mime(_jpeg()) == "image/jpeg"
    assert sniff_image_mime(_png()) == "image/png"
    assert sniff_image_mime(_webp()) == "image/webp"
    assert sniff_image_mime(b"not an image") is None
    # a JPEG a CDN calls image/jpg (or octet-stream) is a JPEG
    data, mime = normalize_cover_art(_jpeg(), "image/jpg")
    assert mime == "image/jpeg" and data[:2] == b"\xff\xd8"
    assert normalize_cover_art(_png(), "application/octet-stream")[1] == "image/png"
    # WebP: no tag format's players agree on it, so it becomes a JPEG
    data, mime = normalize_cover_art(_webp((32, 20)), "image/webp")
    assert mime == "image/jpeg" and data[:2] == b"\xff\xd8"
    from PIL import Image
    assert Image.open(io.BytesIO(data)).size == (32, 20)
    # bytes nothing recognises are passed on as they came
    assert normalize_cover_art(b"mystery", "image/jpeg") == (b"mystery", "image/jpeg")


@pytest.mark.parametrize("data,label,want", [
    (_jpeg(), "image/jpg", "JPEG"), (_jpeg(), "image/jpeg", "JPEG"),
    (_png(), "image/png", "PNG"), (_webp(), "image/webp", "JPEG"),
])
def test_an_m4a_cover_is_marked_as_what_it_is(tmp_path, data, label, want):
    """"jpeg" in mime_type: image/jpg and image/webp both fell to PNG, and a
    JPEG marked PNG shows as no cover in Apple Music, Plex and Jellyfin."""
    from core.metadata import artwork
    from mutagen.mp4 import MP4Cover
    path = _audio(tmp_path, "m4a")
    audio = _open(path)
    assert artwork._write_embedded_art(audio, data, label, SYMBOLS) is True
    audio.save()
    cover = _open(path)["covr"][0]
    assert cover.imageformat == getattr(MP4Cover, "FORMAT_" + want)
    assert bytes(cover)[:2] == (b"\xff\xd8" if want == "JPEG" else b"\x89P")


@pytest.mark.parametrize("ext", ("mp3", "flac"))
def test_a_webp_cover_is_embedded_as_a_jpeg_with_its_real_size(tmp_path, ext):
    from core.metadata import artwork
    path = _audio(tmp_path, ext)
    audio = _open(path)
    assert artwork._write_embedded_art(audio, _webp((80, 60)), "image/webp", SYMBOLS) is True
    audio.save()
    audio = _open(path)
    if ext == "mp3":
        apic = audio.tags.getall("APIC")[0]
        assert apic.mime == "image/jpeg" and apic.data[:2] == b"\xff\xd8"
    else:
        pic = audio.pictures[0]
        assert pic.mime == "image/jpeg" and (pic.width, pic.height) == (80, 60)


def test_the_retag_writer_marks_covers_the_same_way(tmp_path):
    import core.tag_writer as tw
    from mutagen.mp4 import MP4Cover
    path = _audio(tmp_path, "m4a")
    audio = _open(path)
    assert tw._embed_cover_art_data(audio, _jpeg(), "image/jpg") is True
    assert audio["covr"][0].imageformat == MP4Cover.FORMAT_JPEG
    flac = _open(_audio(tmp_path, "flac", "b"))
    assert tw._embed_cover_art_data(flac, _webp((50, 30)), "image/webp") is True
    assert flac.pictures[0].mime == "image/jpeg" and (flac.pictures[0].width, flac.pictures[0].height) == (50, 30)


# ── 7. what tagging keeps, and the compilation flag ─────────────────────────

def _write_replaygain(audio):
    if isinstance(audio.tags, SYMBOLS.ID3):
        audio.tags.add(SYMBOLS.TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-6.50 dB"]))
        audio.tags.add(SYMBOLS.TXXX(encoding=3, desc="replaygain_track_peak", text=["0.988"]))
    elif isinstance(audio, SYMBOLS.MP4):
        audio["----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN"] = [SYMBOLS.MP4FreeForm(b"-6.50 dB")]
        audio["----:com.apple.iTunes:replaygain_track_peak"] = [SYMBOLS.MP4FreeForm(b"0.988")]
    else:
        audio["REPLAYGAIN_TRACK_GAIN"] = ["-6.50 dB"]
        audio["replaygain_track_peak"] = ["0.988"]
        if isinstance(audio, SYMBOLS.OggOpus):
            audio["R128_TRACK_GAIN"] = ["-256"]


def _replaygain(audio):
    if isinstance(audio.tags, SYMBOLS.ID3):
        return {f.desc.upper(): str(f.text[0]) for f in audio.tags.getall("TXXX")
                if f.desc.upper().startswith("REPLAYGAIN_")}
    if isinstance(audio, SYMBOLS.MP4):
        return {k.rsplit(":", 1)[-1].upper(): v[0].decode() for k, v in audio.items()
                if k.upper().startswith("----:COM.APPLE.ITUNES:REPLAYGAIN_")}
    return {k.upper(): v[0] for k, v in audio.items() if k.upper().startswith(("REPLAYGAIN_", "R128_"))}


@pytest.mark.parametrize("ext", FORMATS)
def test_replaygain_survives_tagging(tmp_path, ext):
    """it describes the audio, not the release: clearing the tags threw it
    away, and SoulSync's own ReplayGain pass is off unless switched on."""
    path = _audio(tmp_path, ext)
    audio = _open(path)
    _write_replaygain(audio)
    audio["TITLE" if not isinstance(audio.tags, SYMBOLS.ID3) and not isinstance(audio, SYMBOLS.MP4)
          else ("\xa9nam" if isinstance(audio, SYMBOLS.MP4) else "TIT2")] = (
        ["uploader junk"] if not isinstance(audio.tags, SYMBOLS.ID3)
        else SYMBOLS.TIT2(encoding=3, text=["uploader junk"]))
    audio.save()

    ok, _ = _enhance(path, dict(CORE))
    assert ok
    after = _open(path)
    kept = _replaygain(after)
    assert kept["REPLAYGAIN_TRACK_GAIN"] == "-6.50 dB" and kept["REPLAYGAIN_TRACK_PEAK"] == "0.988"
    if ext == "opus":
        assert kept["R128_TRACK_GAIN"] == "-256"
    # and only that: the uploader's other tags are still replaced
    assert _tag(after, "TIT2", "title", "\xa9nam") == ["Yellow"]


def _compilation(audio):
    if isinstance(audio.tags, SYMBOLS.ID3):
        return [str(t) for f in audio.tags.getall("TCMP") for t in f.text] == ["1"]
    if isinstance(audio, SYMBOLS.MP4):
        return bool(audio.get("cpil"))
    return (audio.get("compilation") or [None])[0] == "1"


@pytest.mark.parametrize("ext", FORMATS)
def test_a_various_artists_album_is_flagged_a_compilation(tmp_path, ext):
    """the flag every player groups a various-artists album by was never
    written: iTunes and the players that follow it scattered such an album
    across its contributors."""
    path = _audio(tmp_path, ext)
    ok, _ = _enhance(path, {**CORE, "artist": "Don Felder", "album_artist": "Various Artists",
                            "album": "Heavy Metal"})
    assert ok and _compilation(_open(path)) is True


@pytest.mark.parametrize("ext", FORMATS)
def test_an_artists_own_album_is_not_flagged(tmp_path, ext):
    # a single artist's best-of is "compilation" to Spotify, and is NOT a
    # compilation to a player: flagged, it would leave the artist's page
    path = _audio(tmp_path, ext)
    ok, _ = _enhance(path, {**CORE, "album": "Greatest Hits"})
    assert ok and _compilation(_open(path)) is False


def test_a_file_is_untouched_until_tagging_can_go_ahead(tmp_path):
    """the APE strip rewrote the file before the metadata was known to exist:
    a failed extraction logged "original tags retained" having already
    deleted some of them."""
    path = _audio(tmp_path, "mp3")
    before = Path(path).read_bytes()
    ok, stripped = _enhance(path, None)
    assert ok is False and stripped.call_count == 0
    assert Path(path).read_bytes() == before
    # and when it can, the strip still runs, before the file is opened
    order = []
    ok, stripped = _enhance(path, dict(CORE), strip=lambda p: order.append("strip"),
                            extract=lambda *a, **k: (order.append("extract"), dict(CORE))[1])
    assert ok and order == ["extract", "strip"]


# ── 8. an m4a's artist, with guests ─────────────────────────────────────────

def _freeform(audio, name):
    return [v.decode("utf-8") for v in audio.get("----:com.apple.iTunes:" + name) or []]


def test_an_m4a_does_not_repeat_the_guests_already_in_its_title(tmp_path):
    """feat_in_title puts the guest in the title and leaves the artist as the
    primary. MP3 and FLAC did; M4A wrote the whole list to the artist, so the
    guest was in both."""
    path = _audio(tmp_path, "m4a")
    meta = {**CORE, "title": "Starboy (feat. Daft Punk)", "artist": "The Weeknd",
            "_artists_list": ["The Weeknd", "Daft Punk"]}
    cfg = Cfg(metadata_enhancement__tags__write_multi_artist=True,
              metadata_enhancement__tags__feat_in_title=True)
    assert _enhance(path, meta, cfg=cfg)[0]
    audio = _open(path)
    assert audio["\xa9ART"] == ["The Weeknd"]
    assert _freeform(audio, "ARTISTS") == ["The Weeknd", "Daft Punk"]


def test_an_m4a_keeps_its_artist_list_without_feat_in_title(tmp_path):
    # #587: Navidrome and Jellyfin read the multi-value artist atom
    path = _audio(tmp_path, "m4a")
    meta = {**CORE, "artist": "The Weeknd, Daft Punk", "_artists_list": ["The Weeknd", "Daft Punk"],
            "album_artist": "The Weeknd, Daft Punk", "_album_artists_list": ["The Weeknd", "Daft Punk"]}
    assert _enhance(path, meta, cfg=Cfg(metadata_enhancement__tags__write_multi_artist=True))[0]
    audio = _open(path)
    assert audio["\xa9ART"] == ["The Weeknd", "Daft Punk"]
    assert _freeform(audio, "ARTISTS") == ["The Weeknd", "Daft Punk"]
    assert _freeform(audio, "ALBUMARTISTS") == ["The Weeknd", "Daft Punk"]
    # off: one artist string, no list
    path = _audio(tmp_path, "m4a", "single")
    assert _enhance(path, meta)[0]
    audio = _open(path)
    assert audio["\xa9ART"] == ["The Weeknd, Daft Punk"] and _freeform(audio, "ARTISTS") == []


# ── 9. track numbers ────────────────────────────────────────────────────────

def test_a_real_track_one_is_not_renumbered_from_its_file_name():
    """`track_number == 1` stood for "missing", so track 1 of an album, in a
    file called "21 - Guns.flac", became track 21."""
    from core.downloads.post_processing import context_track_number
    assert context_track_number({"track_number": 1}, "/dl/21 - Guns.flac") == 1
    assert context_track_number({"track_number": 1}, "/dl/21. Guns.flac") == 1
    assert context_track_number({"track_number": 7}, "/dl/03 - Song.flac") == 7
    # missing, or not a number worth having: the file name is asked
    assert context_track_number({}, "/dl/03 - Song.flac") == 3
    assert context_track_number({"track_number": None}, "/dl/03 - Song.flac") == 3
    assert context_track_number({"track_number": 0}, "/dl/03 - Song.flac") == 3
    # a file name with no number reads as 1, as it always has; no file, no number
    assert context_track_number({}, "/dl/Song.flac") == 1
    assert context_track_number({"track_number": "x"}, None) == 0
    assert context_track_number({"track_number": "4"}, "/dl/Song.flac") == 4


@pytest.mark.parametrize("ext", FORMATS)
def test_an_unknown_track_number_is_left_out_not_written_as_zero(tmp_path, ext):
    """the import marks an unknown position 0. it was written as "0/10"."""
    path = _audio(tmp_path, ext)
    assert _enhance(path, {**CORE, "track_number": 0})[0]
    audio = _open(path)
    assert _tag(audio, "TRCK", "tracknumber", "trkn") == []
    assert _tag(audio, "TIT2", "title", "\xa9nam") == ["Yellow"]
    # a known one is written as before
    path = _audio(tmp_path, ext, "known")
    assert _enhance(path, dict(CORE))[0]
    audio = _open(path)
    if ext == "m4a":
        assert audio["trkn"] == [(5, 10)]
    elif ext == "mp3":
        assert _tag(audio, "TRCK", "", "") == ["5/10"]
    else:
        assert audio["tracknumber"] == ["5"] and audio["tracktotal"] == ["10"]


def test_the_track_number_formatters_never_emit_zero():
    from core.metadata.track_number_format import (
        format_track_number_tag, format_track_number_tuple, format_vorbis_track_fields,
        track_number_known,
    )
    assert track_number_known(5) and track_number_known("3")
    assert not track_number_known(0) and not track_number_known(-1) and not track_number_known("x")
    assert track_number_known(None) is False
    # asked anyway, a formatter falls back to 1 as it does for a missing number
    assert format_track_number_tag(0, 14) == "1/14"
    assert format_track_number_tuple(0, 14) == (1, 14)
    assert format_vorbis_track_fields(0, 14) == ("1", "14")
    assert format_vorbis_track_fields("7", "0") == ("7", None)
    assert format_track_number_tag(None, 13) == "1/13" and format_track_number_tag(6, 0) == "6"


# ── 10. genres, featuring, and a reference artist ───────────────────────────

@pytest.mark.parametrize("given,want", [
    ("EDM", "EDM"), ("edm", "EDM"), ("IDM", "IDM"), ("uk garage", "UK Garage"), ("UK Garage", "UK Garage"),
    ("dnb", "DnB"), ("DnB", "DnB"), ("r&b", "R&B"), ("R&B", "R&B"), ("k-pop", "K-Pop"), ("lo-fi", "Lo-Fi"),
    ("hip-hop", "Hip-Hop"), ("Hip-Hop", "Hip-Hop"), ("rock", "Rock"), ("ROCK", "Rock"),
    ("indie rock", "Indie Rock"), ("80's", "80's"), ("rock 'n' roll", "Rock 'n' Roll"),
    ("  jazz  ", "Jazz"), ("nu-disco", "Nu-Disco"), ("ost", "OST"), ("Synthwave", "Synthwave"),
    ("post-punk", "Post-Punk"), ("dj mix", "DJ Mix"), ("mpb", "MPB"),
])
def test_a_genre_keeps_its_capitals(given, want):
    assert ms._genre_case(given) == want


def test_the_genres_written_to_a_file_keep_their_capitals(tmp_path):
    """through the real writer: the source's genre and the enrichment's,
    merged, on a real file"""
    path = _audio(tmp_path, "flac")
    audio = _open(path)
    pp = _blank_pp(mb_genres=["edm", "uk garage"], lastfm_tags=["DnB", "hip-hop"])
    ms._write_embedded_metadata(audio, {"genre": "R&B", "artist": "x", "album_artist": "x"}, pp,
                                Cfg(), SYMBOLS)
    # (one joined value: multi-value genres are a setting, off by default)
    assert audio["genre"] == ["R&B, EDM, UK Garage, DnB, Hip-Hop"]


@pytest.mark.parametrize("title,has", [
    ("Starboy (feat. Daft Punk)", True), ("Starboy [ft Daft Punk]", True), ("Starboy (Featuring Daft Punk)", True),
    ("Starboy ft. Daft Punk", True), ("Starboy feat. Daft Punk", True), ("Starboy featuring Daft Punk", True),
    ("Starboy (FEAT Daft Punk)", True),
    ("6 ft Under", False), ("A Feat of Strength", False), ("Featuring You", False), ("Left Feat", False),
    ("Ft Worth", False), ("", False),
])
def test_only_a_real_featuring_credit_counts_as_one(title, has):
    """"6 ft Under" matched, so the guest wasn't added to the title, while
    still being taken out of the artist: gone from both."""
    assert ms._title_has_featuring(title) is has


def test_a_bare_ft_counts_only_when_a_guest_follows_it():
    guests = ["Daft Punk"]
    assert ms._title_has_featuring("Starboy ft Daft Punk", guests) is True
    assert ms._title_has_featuring("Starboy feat daft punk", guests) is True
    assert ms._title_has_featuring("6 ft Under", guests) is False
    assert ms._title_has_featuring("6 ft Under", ["Under"]) is True      # the guest really is "Under"
    assert ms._title_has_featuring("Starboy ft Daft Punk") is False      # nobody to look for


def test_a_reference_artist_takes_the_artist_list_with_it():
    """a reference overrides the artist; the list written beside it (the
    Artists frame, an M4A's atom) kept the names it replaced."""
    meta = {"title": "t", "artist": "Old A, Old B", "_artists_list": ["Old A", "Old B"],
            "album": "a", "album_artist": "Old A", "_album_artists_list": ["Old A", "Old B"],
            "track_number": 2, "disc_number": 1}
    ms._apply_metadata_reference(meta, {"track_artist": "New Artist", "artist_name": "New Album Artist",
                                        "title": "New Title", "track_number": 4, "track_count": 9})
    assert meta["artist"] == "New Artist" and meta["_artists_list"] == ["New Artist"]
    assert meta["album_artist"] == "New Album Artist" and meta["_album_artists_list"] == ["New Album Artist"]
    assert (meta["title"], meta["track_number"], meta["total_tracks"]) == ("New Title", 4, 9)
    # a reference that names no artist leaves the lists alone
    meta = {"artist": "A, B", "_artists_list": ["A", "B"], "track_number": 1, "disc_number": 1}
    ms._apply_metadata_reference(meta, {"title": "T"})
    assert meta["_artists_list"] == ["A", "B"]


# ── 11. read_tag reads what write_tag wrote ─────────────────────────────────

@pytest.mark.parametrize("ext", FORMATS)
@pytest.mark.parametrize("tag,value", [
    ("DATE", "1999-03-31"), ("ARTISTSORT", "Creator, Tyler The"), ("ALBUMARTISTSORT", "Beatles, The"),
    ("LYRICS", "look at the stars"), ("ISRC", "GBAYE0000351"), ("LABEL", "Parlophone"),
    ("COPYRIGHT", "(C) 2000"), ("MUSICBRAINZ_ALBUMID", "a-1"),
])
def test_a_tag_written_is_a_tag_read(tmp_path, ext, tag, value):
    """DATE, the sort names and LYRICS go to their own frame or atom on MP3
    and M4A; read_tag looked for them somewhere else and said None."""
    from core.metadata.musicbrainz_tags import read_tag, write_tag
    path = _audio(tmp_path, ext)
    audio = _open(path)
    write_tag(audio, tag, value, SYMBOLS)
    audio.save()
    assert read_tag(_open(path), tag, SYMBOLS) == value


# ── what the file says about the recording is kept ──────────────────────────

def _pre(path, ext):
    audio = _open(path)
    if ext == "mp3":
        from mutagen.id3 import COMM, TCOM, TIPL, TKEY, TXXX
        for f in (TCOM(encoding=3, text=["Chris Martin"]), TKEY(encoding=3, text=["B"]),
                  TIPL(encoding=3, people=[["producer", "Ken Nelson"]]),
                  COMM(encoding=3, lang="eng", desc="", text=["ripped by xX"]),
                  TXXX(encoding=3, desc="MusicBrainz Album Id", text=["stale"]),
                  TXXX(encoding=3, desc="RIPPER", text=["xX"])):
            audio.tags.add(f)
    elif ext == "m4a":
        ff = lambda v: [SYMBOLS.MP4FreeForm(v.encode())]  # noqa: E731
        audio["\xa9wrt"] = ["Chris Martin"]
        audio["pgap"] = True
        audio["----:com.apple.iTunes:iTunSMPB"] = ff(" 00000000 00000840 000001C0")
        audio["----:com.apple.iTunes:PRODUCER"] = ff("Ken Nelson")
        audio["\xa9cmt"] = ["ripped by xX"]
        audio["----:com.apple.iTunes:MusicBrainz Album Id"] = ff("stale")
    else:
        audio["composer"] = ["Chris Martin"]
        audio["INITIALKEY"] = ["B"]
        audio["producer"] = ["Ken Nelson"]
        audio["comment"] = ["ripped by xX"]
        audio["musicbrainz_albumid"] = ["stale"]
        audio["ripper"] = ["xX"]
    audio.save()


@pytest.mark.parametrize("ext", FORMATS)
def test_the_recordings_own_facts_survive_and_the_uploaders_junk_does_not(tmp_path, ext):
    """the clear is for what the file says about the RELEASE (an uploader's
    ids and comments). who wrote the song, its key, its credits and an M4A's
    gapless padding are facts about the recording, and no source SoulSync
    asks gives them back."""
    path = _audio(tmp_path, ext)
    _pre(path, ext)
    ok, _ = _enhance(path, dict(CORE))
    assert ok
    audio = _open(path)
    assert _tag(audio, "TCOM", "composer", "\xa9wrt") == ["Chris Martin"]
    if ext == "mp3":
        assert _tag(audio, "TKEY", "", "") == ["B"]
        assert audio.tags.getall("TIPL")[0].people == [["producer", "Ken Nelson"]]
        assert not audio.tags.getall("COMM") and not audio.tags.getall("TXXX:RIPPER")
        assert not audio.tags.getall("TXXX:MusicBrainz Album Id")
    elif ext == "m4a":
        assert audio["pgap"] is True and "----:com.apple.iTunes:iTunSMPB" in audio
        assert _tag(audio, "", "", "----:com.apple.iTunes:PRODUCER") == ["Ken Nelson"]
        assert "\xa9cmt" not in audio and "----:com.apple.iTunes:MusicBrainz Album Id" not in audio
    else:
        assert audio["initialkey"] == ["B"] and audio["producer"] == ["Ken Nelson"]
        assert "comment" not in audio and "ripper" not in audio and "musicbrainz_albumid" not in audio


@pytest.mark.parametrize("ext", FORMATS)
def test_a_value_soulsync_has_replaces_the_kept_one(tmp_path, ext):
    from core.metadata import tagfields
    path = _audio(tmp_path, ext)
    audio = _open(path)
    tagfields.write(audio, SYMBOLS, "bpm", 173)
    audio.save()

    def embed(audio_file, metadata, context, runtime=None, on_resolved=None):
        on_resolved()
        tagfields.write(audio_file, SYMBOLS, "bpm", 87)

    ok, _ = _enhance(path, dict(CORE), embed=embed)
    assert ok
    assert tagfields.read(_open(path), SYMBOLS, "bpm") == ["87"]


# ── the one table ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("ext", FORMATS)
@pytest.mark.parametrize("name", sorted(__import__("core.metadata.tagfields", fromlist=["FIELDS"]).FIELDS))
def test_every_field_in_the_table_reads_back_what_was_written(tmp_path, ext, name):
    from core.metadata import tagfields
    path = _audio(tmp_path, ext)
    audio = _open(path)
    kind = tagfields.FIELDS[name].kind
    text = "2001-02-03" if name == "date" else "value one"     # an ID3 date frame holds only a date
    value = True if kind == "flag" else 120 if kind == "int" else text
    assert tagfields.write(audio, SYMBOLS, name, value) is True
    audio.save()
    back = tagfields.read(_open(path), SYMBOLS, name)
    assert back == (["True"] if kind == "flag" and ext == "m4a" else ["1"] if kind == "flag"
                    else ["120"] if kind == "int" else [text])
    again = _open(path)
    assert tagfields.remove(again, SYMBOLS, name) is True
    again.save()
    assert tagfields.read(_open(path), SYMBOLS, name) == []


@pytest.mark.parametrize("title,guests,added", [
    ("Umbrella", ["JAY-Z"], ["JAY-Z"]),
    ("Umbrella (feat. JAY-Z)", ["JAY-Z"], []),
    ("Princess of China (with Rihanna)", ["Rihanna"], []),
    ("Stay (Rihanna Remix)", ["Rihanna"], []),
    ("Back to the Future", ["Future"], ["Future"]),          # a word in a title is not a credit
    ("Song (with A)", ["A", "B"], ["B"]),                    # only the one not yet credited
])
def test_only_guests_the_title_does_not_credit_are_added(title, guests, added):
    assert ms._guests_missing_from_title(title, guests) == added
