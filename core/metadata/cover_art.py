"""a cover, as what it really is, in a form every player can show.

both tag writers (the download tagger, core/metadata/artwork.py, and the
library re-tagger, core/tag_writer.py) trusted the mime type a CDN sent with
the image and wrote it into the file:

* an M4A cover is marked JPEG or PNG by a flag, chosen with
  ``"jpeg" in mime_type``. a JPEG served as ``image/jpg``, and every WebP,
  went in marked PNG, and Apple Music, Plex and Jellyfin show no cover for
  bytes that don't match their flag;
* a WebP was embedded raw in MP3 and FLAC. hardware players and a good deal
  of software can't draw it, and its size was read as 640x640 because the
  size reader knew only JPEG and PNG.

so: an image is known by its own first bytes, a label that disagrees is
corrected, and anything that isn't a JPEG or a PNG is re-encoded as a JPEG
(Pillow, already a dependency; without it the bytes pass through unchanged).
"""

from __future__ import annotations

import base64
import io
from typing import Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("metadata.cover_art")

JPEG, PNG, WEBP, GIF, BMP = "image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"
# what a tag may carry as it is
_EMBEDDABLE = (JPEG, PNG)
_JPEG_QUALITY = 92


def sniff_image_mime(data: bytes) -> Optional[str]:
    """the image type its bytes say it is, or None when they say nothing"""
    head = bytes(data[:16]) if data else b""
    if head[:3] == b"\xff\xd8\xff":
        return JPEG
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return GIF
    if head[:2] == b"BM":
        return BMP
    return None


def _to_jpeg(data: bytes) -> Optional[bytes]:
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - no Pillow: the bytes pass through
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.mode in ("RGBA", "LA", "P"):
                # a JPEG has no transparency: flatten onto white, not black
                rgba = img.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.split()[-1])
                img = flat
            else:
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
            return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 - unreadable: the bytes pass through
        logger.debug("cover could not be re-encoded as a JPEG: %s", exc)
        return None


def normalize_cover_art(image_data: bytes, mime_type: Optional[str]) -> Tuple[bytes, str]:
    """``(bytes, mime)`` fit to embed: the mime the bytes really are, and a
    JPEG in place of anything that isn't a JPEG or a PNG. bytes nothing
    recognises come back as they were, under the label they came with."""
    label = (mime_type or JPEG).strip().lower() or JPEG
    real = sniff_image_mime(image_data)
    if real is None:
        return image_data, (JPEG if label == "image/jpg" else label)
    if real in _EMBEDDABLE:
        return image_data, real
    converted = _to_jpeg(image_data)
    if converted:
        logger.info("Cover art was %s; embedded as a JPEG so every player can show it.", real)
        return converted, JPEG
    return image_data, real


def image_size(image_data: bytes) -> Tuple[Optional[int], Optional[int]]:
    """(width, height), or (None, None). JPEG and PNG by their headers; the
    rest through Pillow."""
    from core.metadata.common import get_image_dimensions
    try:
        width, height = get_image_dimensions(image_data)
        if width and height:
            return int(width), int(height)
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_data)) as img:
            return int(img.size[0]), int(img.size[1])
    except Exception:  # noqa: BLE001
        return None, None


def flac_picture(symbols, image_data: bytes, mime_type: str):
    """a front-cover Picture block with the image's real size"""
    picture = symbols.Picture()
    picture.data = image_data
    picture.type = 3
    picture.mime = mime_type
    width, height = image_size(image_data)
    picture.width = width or 640
    picture.height = height or 640
    picture.depth = 24
    return picture


def ogg_picture_comment(symbols, image_data: bytes, mime_type: str) -> str:
    """the METADATA_BLOCK_PICTURE comment an Ogg Vorbis or Opus file carries
    its cover in: a FLAC picture block, base64"""
    return base64.b64encode(flac_picture(symbols, image_data, mime_type).write()).decode("ascii")


def mp4_cover(symbols, image_data: bytes, mime_type: str):
    """an MP4Cover flagged as what the bytes are"""
    fmt = symbols.MP4Cover.FORMAT_PNG if mime_type == PNG else symbols.MP4Cover.FORMAT_JPEG
    return symbols.MP4Cover(image_data, imageformat=fmt)


__all__ = ["sniff_image_mime", "normalize_cover_art", "image_size", "flac_picture",
           "ogg_picture_comment", "mp4_cover", "JPEG", "PNG", "WEBP"]
