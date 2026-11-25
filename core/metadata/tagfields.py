"""where a field lives in each kind of audio file, written down once.

a tag has three addresses: an ID3 frame (MP3, AIFF), a Vorbis comment (FLAC,
Ogg Vorbis, Opus) and an MP4 atom (M4A). the download tagger, the library
re-tagger, the MusicBrainz writer and the enrichment writer each carried
their own copy of that mapping as a three-way ``if``, and the copies
drifted: Ogg files got no cover, an M4A's artist was written differently
from an MP3's, DATE was written to one frame and read from another.

this is the one copy. ``FIELDS`` is the table; ``write`` / ``read`` /
``remove`` are the only code that knows how a frame, a comment or an atom is
put together. a name that isn't in the table is a freeform tag of that name
(``TXXX:<NAME>``, ``<NAME>``, ``----:com.apple.iTunes:<NAME>``), which is
where every custom id has always gone.

addresses, as written in the table:
  ID3     ``TPE1``; ``TXXX:Artists`` (a TXXX frame with that description);
          ``UFID:<owner>``; ``USLT`` (lyrics)
  Vorbis  the comment key
  MP4     ``\\xa9nam`` (an atom); ``----:ARTISTS`` (a freeform atom under
          com.apple.iTunes)
"""

from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Optional

MP4_FREEFORM = "----:com.apple.iTunes:"


class Field(NamedTuple):
    id3: str
    vorbis: str
    mp4: str
    kind: str = "text"          # text | int (an MP4 integer atom) | flag (an MP4 boolean atom)


FIELDS: Dict[str, Field] = {
    # what the release is
    "title": Field("TIT2", "title", "\xa9nam"),
    "artist": Field("TPE1", "artist", "\xa9ART"),
    "album_artist": Field("TPE2", "albumartist", "aART"),
    "album": Field("TALB", "album", "\xa9alb"),
    "date": Field("TDRC", "date", "\xa9day"),
    "genre": Field("TCON", "genre", "\xa9gen"),
    "artists": Field("TXXX:Artists", "artists", "----:ARTISTS"),
    "album_artists": Field("TXXX:Album Artists", "albumartists", "----:ALBUMARTISTS"),
    "artistsort": Field("TSOP", "artistsort", "soar"),
    "albumartistsort": Field("TSO2", "albumartistsort", "soaa"),
    "compilation": Field("TCMP", "compilation", "cpil", "flag"),
    "copyright": Field("TCOP", "copyright", "cprt"),
    "label": Field("TPUB", "label", "----:LABEL"),
    "isrc": Field("TSRC", "isrc", "----:ISRC"),
    "lyrics": Field("USLT", "lyrics", "\xa9lyr"),
    "quality": Field("TXXX:QUALITY", "quality", "----:QUALITY"),
    # what the recording is: facts about the performance and the stream that
    # no metadata source SoulSync asks will supply (core/metadata/tag_keep.py)
    "bpm": Field("TBPM", "bpm", "tmpo", "int"),
    "key": Field("TKEY", "initialkey", "----:initialkey"),
    "composer": Field("TCOM", "composer", "\xa9wrt"),
    "lyricist": Field("TEXT", "lyricist", "----:LYRICIST"),
    "conductor": Field("TPE3", "conductor", "----:CONDUCTOR"),
    "remixer": Field("TPE4", "remixer", "----:REMIXER"),
    "grouping": Field("TIT1", "grouping", "\xa9grp"),
    "work": Field("TXXX:WORK", "work", "\xa9wrk"),
    "movement": Field("MVNM", "movementname", "\xa9mvn"),
    "language": Field("TLAN", "language", "----:LANGUAGE"),
    "encoder": Field("TSSE", "encoder", "\xa9too"),
}


def freeform(name: str) -> Field:
    """a custom tag of this name, in every container"""
    return Field("TXXX:" + name, name, "----:" + name)


def field_for(name: str) -> Field:
    return FIELDS.get(name) or FIELDS.get(str(name).lower()) or freeform(name)


def container(audio: Any, symbols: Any) -> Optional[str]:
    """'id3', 'vorbis' or 'mp4': which address applies. None for a file with
    no tag container this code writes."""
    from core.metadata.common import is_vorbis_like
    if audio is None:
        return None
    if isinstance(getattr(audio, "tags", None), symbols.ID3):
        return "id3"
    if isinstance(audio, symbols.MP4):
        return "mp4"
    if is_vorbis_like(audio, symbols):
        return "vorbis"
    return None


def _values(value: Any) -> List[Any]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [v for v in items if v is not None and v != ""]


def _mp4_key(address: str) -> str:
    return MP4_FREEFORM + address[5:] if address.startswith("----:") else address


def _id3_frame(symbols: Any, frame_id: str):
    from mutagen import id3
    return getattr(symbols, frame_id, None) or getattr(id3, frame_id)


def write_at(audio: Any, symbols: Any, field: Field, value: Any) -> bool:
    """write ``value`` (one or several) at this field's address, replacing
    what is there. nothing is written for an empty value. False when the file
    has no container for it."""
    values = _values(value)
    kind = container(audio, symbols)
    if not values or kind is None:
        return False
    if kind == "id3":
        address = field.id3
        if address.startswith("TXXX:"):
            audio.tags.add(symbols.TXXX(encoding=3, desc=address[5:], text=[str(v) for v in values]))
        elif address.startswith("UFID:"):
            audio.tags.add(symbols.UFID(owner=address[5:], data=str(values[0]).encode("ascii")))
        elif address == "USLT":
            from mutagen.id3 import USLT
            # a USLT is keyed by language and description: one added beside an
            # old 'XXX' frame stacked a second set of lyrics (review 5 R5-A-3)
            audio.tags.delall("USLT")
            audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=str(values[0])))
        else:
            text = ["1"] if field.kind == "flag" else [str(v) for v in values]
            audio.tags.add(_id3_frame(symbols, address)(encoding=3, text=text))
    elif kind == "mp4":
        key = _mp4_key(field.mp4)
        if field.kind == "flag":
            audio[key] = True
        elif field.kind == "int":
            audio[key] = [int(round(float(values[0])))]
        elif key.startswith(MP4_FREEFORM):
            audio[key] = [symbols.MP4FreeForm(str(v).encode("utf-8")) for v in values]
        else:
            audio[key] = [str(v) for v in values]
    else:
        audio[field.vorbis] = ["1"] if field.kind == "flag" else [str(v) for v in values]
    return True


def read_at(audio: Any, symbols: Any, field: Field) -> List[str]:
    """every value at this field's address, as text. [] when there is none."""
    kind = container(audio, symbols)
    if kind is None:
        return []
    if kind == "id3":
        address = field.id3
        if address.startswith("TXXX:"):
            frames = [f for f in audio.tags.getall("TXXX") if f.desc == address[5:]]
            return [str(t) for f in frames for t in (f.text or [])]
        if address.startswith("UFID:"):
            frames = [f for f in audio.tags.getall("UFID") if f.owner == address[5:]]
            return [f.data.decode("utf-8", "replace") if isinstance(f.data, bytes) else str(f.data) for f in frames]
        if address == "USLT":
            return [str(f.text) for f in audio.tags.getall("USLT")][:1]
        return [str(t) for f in audio.tags.getall(address) for t in (f.text or [])]
    raw = audio.get(_mp4_key(field.mp4)) if kind == "mp4" else audio.get(field.vorbis)
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    return [v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v) for v in raw]


def remove_at(audio: Any, symbols: Any, field: Field) -> bool:
    kind = container(audio, symbols)
    if kind is None:
        return False
    if kind == "id3":
        address = field.id3
        if address.startswith("TXXX:") or address.startswith("UFID:"):
            key = address
            if key in audio.tags:
                audio.tags.delall(key)
                return True
            return False
        had = bool(audio.tags.getall(address))
        audio.tags.delall(address)
        return had
    key = _mp4_key(field.mp4) if kind == "mp4" else field.vorbis
    if key in audio:
        del audio[key]
        return True
    return False


def write(audio: Any, symbols: Any, name: str, value: Any) -> bool:
    return write_at(audio, symbols, field_for(name), value)


def read(audio: Any, symbols: Any, name: str) -> List[str]:
    return read_at(audio, symbols, field_for(name))


def remove(audio: Any, symbols: Any, name: str) -> bool:
    return remove_at(audio, symbols, field_for(name))


def write_track_position(audio: Any, symbols: Any, number: Any, total: Any, *, known: bool = True) -> None:
    """the track number and the album's track count.

    ID3 and MP4 carry both in one tag ("5/10", (5, 10)); Vorbis has no such
    convention, so the number goes in TRACKNUMBER bare and the total in its
    own field ("1/1" there displayed literally as the track number). a
    number that isn't ``known`` is left out: the import marks a position it
    couldn't work out, and a zero or an invented 1 is worse than nothing."""
    from core.metadata.track_number_format import (
        format_track_number_tag,
        format_track_number_tuple,
        format_vorbis_track_fields,
    )
    kind = container(audio, symbols)
    if kind == "id3":
        if known:
            audio.tags.add(symbols.TRCK(encoding=3, text=[format_track_number_tag(number, total)]))
    elif kind == "vorbis":
        vorbis_number, vorbis_total = format_vorbis_track_fields(number, total)
        if known:
            audio["tracknumber"] = [vorbis_number]
        if vorbis_total is not None:
            audio["tracktotal"] = [vorbis_total]
            audio["totaltracks"] = [vorbis_total]      # legacy readers
    elif kind == "mp4":
        if known:
            audio["trkn"] = [format_track_number_tuple(number, total)]


def write_disc_position(audio: Any, symbols: Any, number: int, total: Any) -> None:
    """the disc number, always written (a track with none floats ungrouped
    above the disc sections in Jellyfin and Plex), and the disc count."""
    from core.metadata.track_number_format import format_track_number_tag, format_track_number_tuple
    kind = container(audio, symbols)
    if kind == "id3":
        audio.tags.add(symbols.TPOS(encoding=3, text=[format_track_number_tag(number, total)]))
    elif kind == "vorbis":
        audio["discnumber"] = [str(number)]
        if total:
            audio["disctotal"] = audio["totaldiscs"] = [str(total)]
    elif kind == "mp4":
        audio["disk"] = [format_track_number_tuple(number, total)]


__all__ = ["Field", "FIELDS", "MP4_FREEFORM", "freeform", "field_for", "container",
           "write", "read", "remove", "write_at", "read_at", "remove_at",
           "write_track_position", "write_disc_position"]
