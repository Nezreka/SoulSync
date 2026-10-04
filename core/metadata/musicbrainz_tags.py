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


def release_by_artist(release, artist_names, artist_mbid=None):
    """the release is credited to the artist we expect. an id match wins; else a
    credited name or the artist's current name folds equal to one we expect.
    equal, not similar: "Mammoth Mammoth" is a different band from "Mammoth"
    (#1426). no expected name or no credited name -> can't judge, True."""
    from core.text.fold import fold_title
    credits = [c for c in (release or {}).get("artist-credit") or [] if isinstance(c, dict)]
    if isinstance(artist_names, str):
        artist_names = [artist_names]
    expected = {fold_title(n, drop_brackets=False) for n in artist_names or [] if n}
    expected.discard("")
    if not expected or not credits:
        return True
    if artist_mbid and any((c.get("artist") or {}).get("id") == artist_mbid for c in credits):
        return True
    joined = "".join((c.get("name") or (c.get("artist") or {}).get("name") or "") + (c.get("joinphrase") or "")
                     for c in credits)
    names = {joined} | {c.get("name") or "" for c in credits} | {(c.get("artist") or {}).get("name") or "" for c in credits}
    folded = {fold_title(n, drop_brackets=False) for n in names if n} - {""}
    return not folded or bool(expected & folded)


def _latin(folded):
    """every letter is a-z (fold_title already took the accents off)."""
    return all(ch.isascii() for ch in folded if ch.isalpha())


def track_title_agrees(title, track, threshold=0.7):
    """a release track at the file's position is the same song, not just the
    same slot on another album (#1426). a word-prefix counts, so "Song -
    Remastered 2011" agrees with "Song". a side with no title -> can't judge, True."""
    from core.text.fold import fold_title, folded_similarity
    mine = fold_title(title or "")
    if not mine:
        return True
    track = track or {}
    theirs = {fold_title(n or "") for n in (track.get("title"), (track.get("recording") or {}).get("title"))} - {""}
    if not theirs:
        return True
    # "Hikari Saiko" vs "光、再考" is the same song in two scripts. a romanized
    # library can't be checked against native titles, so that's no-judge too
    theirs = {name for name in theirs if _latin(name) == _latin(mine)}
    if not theirs:
        return True
    for name in theirs:
        short, long_ = sorted((mine, name), key=len)
        if folded_similarity(mine, name) >= threshold or long_.startswith(short + " "):
            return True
    return False


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
    # the release's disambiguation ("baby punk version"), picard's album comment.
    # it's what keeps two same-named releases apart on disk (#1299).
    if (release.get("disambiguation") or "").strip():
        tags["MUSICBRAINZ_ALBUMCOMMENT"] = release["disambiguation"].strip()
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
