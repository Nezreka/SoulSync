"""Release-scoped MusicBrainz metadata shared by import and album completion."""


def selected_release_id(album):
    """Only a concrete release is authoritative; a release-group is not an edition."""
    if not isinstance(album, dict):
        return ""
    explicit = album.get("musicbrainz_release_id")
    if explicit:
        return str(explicit)
    url = (album.get("external_urls") or {}).get("musicbrainz", "")
    if "/release/" in url:
        return url.split("/release/", 1)[1].split("?", 1)[0].strip("/")
    return ""


def track_matches_title(title, track):
    """An explicit edition must not assign a different song by position alone."""
    import re
    import unicodedata
    def normalized(value):
        return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())
    expected = normalized(title)
    return bool(expected) and expected in {
        normalized(track.get("title")), normalized((track.get("recording") or {}).get("title"))}


def credit_tags(credits, album=False):
    entries = [c for c in credits or [] if isinstance(c, dict)]
    names = [c.get("name") or c.get("artist", {}).get("name") for c in entries]
    ids = [c.get("artist", {}).get("id") for c in entries]
    sorts = "".join((c.get("artist", {}).get("sort-name") or c.get("name") or
                     c.get("artist", {}).get("name", "")) + c.get("joinphrase", "")
                    for c in entries).strip()
    tags = {}
    if sorts:
        tags["ALBUMARTISTSORT" if album else "ARTISTSORT"] = sorts
    if any(ids):
        tags["MUSICBRAINZ_ALBUMARTISTID" if album else "MUSICBRAINZ_ARTIST_ID"] = [i for i in ids if i]
    if not album and any(names):
        tags["ARTISTS"] = [n for n in names if n]
    return tags


def release_tags(release):
    tags = credit_tags(release.get("artist-credit"), album=True)
    rg = release.get("release-group") or {}
    if release.get("id"):
        tags["MUSICBRAINZ_RELEASE_ID"] = release["id"]
    if rg.get("id"):
        tags["MUSICBRAINZ_RELEASEGROUPID"] = rg["id"]
    types = [rg.get("primary-type")] + (rg.get("secondary-types") or [])
    if any(types):
        tags["RELEASETYPE"] = [t.lower() for t in types if t]
    if rg.get("first-release-date"):
        tags["ORIGINALDATE"] = rg["first-release-date"]
        tags["ORIGINALYEAR"] = rg["first-release-date"][:4]
    for field, tag in (("date", "DATE"), ("status", "RELEASESTATUS"),
                       ("country", "RELEASECOUNTRY"), ("barcode", "BARCODE"), ("asin", "ASIN")):
        if release.get(field):
            tags[tag] = release[field].lower() if field == "status" else release[field]
    labels = release.get("label-info") or []
    for tag, values in (("LABEL", [(x.get("label") or {}).get("name") for x in labels]),
                        ("CATALOGNUMBER", [x.get("catalog-number") for x in labels])):
        if any(values):
            tags[tag] = list(dict.fromkeys(v for v in values if v))
    media = release.get("media") or []
    if media:
        tags["TOTALDISCS"] = str(len(media))
        if media[0].get("format"):
            tags["MEDIA"] = media[0]["format"]
    if (release.get("text-representation") or {}).get("script"):
        tags["SCRIPT"] = release["text-representation"]["script"]
    return tags


def write_tag(audio, tag, value, symbols):
    """Write Picard's native frames/atoms and preserve multi-value fields."""
    from core.metadata.common import is_vorbis_like
    from core.metadata.source import ID3_TAG_MAP, MP4_TAG_MAP, VORBIS_TAG_MAP
    values = [str(v) for v in (value if isinstance(value, (list, tuple)) else [value]) if v is not None]
    if not values:
        return
    native = {"DATE": "TDRC", "ARTISTSORT": "TSOP", "ALBUMARTISTSORT": "TSO2", "LABEL": "TPUB", "ISRC": "TSRC"}
    if isinstance(audio.tags, symbols.ID3):
        frame, desc = ID3_TAG_MAP.get(tag, ("TXXX", tag))
        frame = native.get(tag, frame)
        if frame == "UFID":
            audio.tags.add(symbols.UFID(owner=desc, data=values[0].encode("ascii")))
        elif frame == "TXXX":
            audio.tags.add(symbols.TXXX(encoding=3, desc=desc, text=values))
        else:
            from mutagen import id3
            factory = getattr(symbols, frame, None) or getattr(id3, frame)
            audio.tags.add(factory(encoding=3, text=values))
    elif isinstance(audio, symbols.MP4):
        atom = {"DATE": "\xa9day", "ARTISTSORT": "soar", "ALBUMARTISTSORT": "soaa"}.get(tag)
        if atom:
            audio[atom] = values
        else:
            key = "----:com.apple.iTunes:" + MP4_TAG_MAP.get(tag, tag)
            audio[key] = [symbols.MP4FreeForm(v.encode("utf-8")) for v in values]
    elif is_vorbis_like(audio, symbols):
        key = VORBIS_TAG_MAP.get(tag, tag)
        if key != tag and tag in audio:
            del audio[tag]  # Remove SoulSync's legacy alias before writing Picard's key.
        audio[key] = values
