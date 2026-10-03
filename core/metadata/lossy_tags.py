"""carry a tagged lossless file's tags onto its lossy copy (#1422).

ffmpeg's ``-map_metadata 0`` copies a flac's vorbis comments under their raw
names, so an mp3 copy ended up with TXXX:MUSICBRAINZ_ALBUMID, lyrics in
TXXX:USLT and the flac's own quality tag, and ``-vn`` dropped the cover. the
source is already tagged by soulsync's import writer at this point, so the copy
is rebuilt from it with the same frames that writer uses for a native mp3/m4a.

vorbis source -> any copy: translated. same tag family (id3 -> mp3, mp4 ->
m4a): copied as-is. anything else keeps ffmpeg's text tags. every case gets the
cover and the new QUALITY.
"""

import base64

from utils.logging_config import get_logger

logger = get_logger("metadata.lossy_tags")

# folded into TRCK / trkn, or rewritten for the copy
_TRACK_TOTAL_KEYS = ("TRACKTOTAL", "TOTALTRACKS")
_DROP = {"QUALITY", "ENCODER", "METADATA_BLOCK_PICTURE", "COVERART", "COVERARTMIME"}
_LYRICS_KEYS = ("LYRICS", "UNSYNCEDLYRICS")

_ID3_TEXT = {
    "TITLE": "TIT2", "ARTIST": "TPE1", "ALBUMARTIST": "TPE2", "ALBUM": "TALB",
    "GENRE": "TCON", "COMPOSER": "TCOM", "COPYRIGHT": "TCOP", "BPM": "TBPM",
}
_MP4_TEXT = {
    "TITLE": "\xa9nam", "ARTIST": "\xa9ART", "ALBUMARTIST": "aART", "ALBUM": "\xa9alb",
    "GENRE": "\xa9gen", "COMPOSER": "\xa9wrt", "COPYRIGHT": "cprt",
}


def _vorbis_fields(tags):
    fields = {}
    for key, value in tags or []:
        fields.setdefault(str(key).upper(), []).append(value)
    return fields


def _int(value):
    try:
        return int(str(value).split("/", 1)[0].strip())
    except (TypeError, ValueError):
        return None


def _front_cover(audio, symbols):
    """(data, mime) of the source's front cover (or first picture), else None."""
    pics = []
    if isinstance(audio, symbols.FLAC):
        pics = [(p.type, p.data, p.mime) for p in audio.pictures]
    elif isinstance(audio.tags, symbols.ID3):
        pics = [(f.type, f.data, f.mime) for f in audio.tags.getall("APIC")]
    elif isinstance(audio, symbols.MP4):
        for c in audio.get("covr") or []:
            mime = "image/png" if c.imageformat == symbols.MP4Cover.FORMAT_PNG else "image/jpeg"
            pics.append((3, bytes(c), mime))
    if not pics:
        return None
    _type, data, mime = next((p for p in pics if p[0] == 3), pics[0])
    return data, mime or "image/jpeg"


def _write_cover(dest, cover, symbols):
    data, mime = cover
    if isinstance(dest.tags, symbols.ID3):
        dest.tags.delall("APIC")
        dest.tags.add(symbols.APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    elif isinstance(dest, symbols.MP4):
        fmt = symbols.MP4Cover.FORMAT_PNG if "png" in mime else symbols.MP4Cover.FORMAT_JPEG
        dest["covr"] = [symbols.MP4Cover(data, imageformat=fmt)]
    else:
        # opus/ogg carry pictures as a base64 flac picture block
        pic = symbols.Picture()
        pic.type, pic.mime, pic.data = 3, mime, data
        dest["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]


def _write_quality(dest, label, symbols):
    if isinstance(dest.tags, symbols.ID3):
        dest.tags.delall("TXXX:QUALITY")
        dest.tags.add(symbols.TXXX(encoding=3, desc="QUALITY", text=[label]))
    elif isinstance(dest, symbols.MP4):
        dest["----:com.apple.iTunes:QUALITY"] = [symbols.MP4FreeForm(label.encode("utf-8"))]
    else:
        dest["QUALITY"] = [label]


def _vorbis_to_id3(fields, dest, symbols):
    from mutagen import id3
    from core.metadata.musicbrainz_tags import write_tag
    from core.metadata.source import VORBIS_TAG_MAP
    from core.metadata.track_number_format import format_track_number_tag
    canonical = {v: k for k, v in VORBIS_TAG_MAP.items()}
    total = next((fields[k][0] for k in _TRACK_TOTAL_KEYS if fields.get(k)), None)
    for key, values in fields.items():
        if key in _DROP or key in _TRACK_TOTAL_KEYS:
            continue
        if key in _ID3_TEXT:
            dest.tags.add(getattr(id3, _ID3_TEXT[key])(encoding=3, text=values))
        elif key == "TRACKNUMBER":
            dest.tags.add(id3.TRCK(encoding=3, text=[format_track_number_tag(_int(values[0]), _int(total))]))
        elif key == "DISCNUMBER":
            dest.tags.add(id3.TPOS(encoding=3, text=[str(_int(values[0]) or 1)]))
        elif key in _LYRICS_KEYS:
            dest.tags.delall("USLT")
            dest.tags.add(id3.USLT(encoding=3, lang="eng", desc="", text=values[0]))
        elif key == "ALBUMARTISTS":
            dest.tags.add(id3.TXXX(encoding=3, desc="Album Artists", text=values))
        else:
            write_tag(dest, canonical.get(key, key), values, symbols)


def _vorbis_to_mp4(fields, dest, symbols):
    from core.metadata.musicbrainz_tags import write_tag
    from core.metadata.source import VORBIS_TAG_MAP
    from core.metadata.track_number_format import format_track_number_tuple
    canonical = {v: k for k, v in VORBIS_TAG_MAP.items()}
    total = next((fields[k][0] for k in _TRACK_TOTAL_KEYS if fields.get(k)), None)
    for key, values in fields.items():
        if key in _DROP or key in _TRACK_TOTAL_KEYS:
            continue
        if key in _MP4_TEXT:
            dest[_MP4_TEXT[key]] = values
        elif key == "TRACKNUMBER":
            dest["trkn"] = [format_track_number_tuple(_int(values[0]), _int(total))]
        elif key == "DISCNUMBER":
            dest["disk"] = [(_int(values[0]) or 1, 0)]
        elif key in _LYRICS_KEYS:
            dest["\xa9lyr"] = [values[0]]
        elif key == "BPM":
            if _int(values[0]):
                dest["tmpo"] = [_int(values[0])]
        else:
            write_tag(dest, canonical.get(key, key), values, symbols)


def carry_tags_to_lossy_copy(source_path, dest_path, quality_label):
    """Rewrite ``dest_path``'s tags from ``source_path``. Never raises; False when
    the copy could not be retagged (it keeps whatever ffmpeg wrote)."""
    from core.metadata.common import get_mutagen_symbols, is_vorbis_like
    symbols = get_mutagen_symbols()
    if symbols is None:
        return False
    try:
        src = symbols.File(source_path)
        dest = symbols.File(dest_path)
        if src is None or dest is None:
            return False
        if dest.tags is None:
            dest.add_tags()
        src_vorbis = is_vorbis_like(src, symbols) and src.tags is not None
        dest_id3 = isinstance(dest.tags, symbols.ID3)
        dest_mp4 = isinstance(dest, symbols.MP4)
        if src_vorbis:
            fields = _vorbis_fields(src.tags)
            dest.tags.clear()
            if dest_id3:
                _vorbis_to_id3(fields, dest, symbols)
            elif dest_mp4:
                _vorbis_to_mp4(fields, dest, symbols)
            else:
                for key, values in fields.items():
                    if key not in _DROP:
                        dest[key] = values
        elif dest_id3 and isinstance(src.tags, symbols.ID3):
            dest.tags.clear()
            for frame in src.tags.values():
                dest.tags.add(frame)
        elif dest_mp4 and isinstance(src, symbols.MP4) and src.tags is not None:
            dest.tags.clear()
            for key, value in src.tags.items():
                dest[key] = value
        cover = _front_cover(src, symbols)
        if cover:
            _write_cover(dest, cover, symbols)
        _write_quality(dest, quality_label, symbols)
        if dest_id3:
            dest.save(v1=0, v2_version=4)
        else:
            dest.save()
        return True
    except Exception as e:  # noqa: BLE001 - a lossy copy with ffmpeg's tags beats no copy
        logger.error("[Lossy Copy] could not carry tags onto %s: %s", dest_path, e)
        return False
