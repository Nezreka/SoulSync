"""Synthesize an artist-detail response for an artist that isn't in the library.

Extracted from ``web_server.py`` so the logic is importable at test time.
The route handler in ``web_server.py`` is now a thin wrapper that builds the
per-source clients (which live as module globals there), calls this function,
and wraps the return value in ``jsonify``.

Used by ``/api/artist-detail/<id>`` when the URL is called with a ``source``
query parameter and the library DB lookup misses. Enriches the response with
whatever metadata we can pull on demand:

  * Image URL (via ``core.metadata.artist_image.get_artist_image_url``)
  * Source-specific artist info — genres + follower count from the named
    source's ``get_artist`` / ``get_artist_info`` helper
  * Last.fm bio + listeners + playcount + URL (by artist name)
  * Discography from the named source, with variant dedup disabled so every
    release surfaces

All per-source clients are passed in explicitly. Callers that can't or don't
want to provide a given client pass ``None`` — the corresponding enrichment
branch becomes a no-op.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from core.artist_source_lookup import SOURCE_ID_FIELD
from core.metadata import artist_image as metadata_artist_image
from core.metadata import discography as metadata_discography
from core.metadata.lookup import MetadataLookupOptions

logger = logging.getLogger("artist_source_detail")


def build_source_only_artist_detail(
    artist_id: str,
    artist_name: str,
    source: str,
    *,
    spotify_client: Optional[Any] = None,
    deezer_client: Optional[Any] = None,
    itunes_client: Optional[Any] = None,
    discogs_client: Optional[Any] = None,
    lastfm_api_key: Optional[str] = None,
) -> Tuple[Dict[str, Any], int]:
    """Build the artist-detail payload for a source-only artist.

    Returns ``(payload_dict, http_status)``. Callers wrap the dict in
    ``jsonify`` or equivalent. Status is 200 on success, 404 when the
    source's discography lookup returned no releases.
    """
    resolved_name = (artist_name or artist_id or "").strip()

    # 1. Image URL via the same helper /api/artist/<id>/image uses.
    image_url: Optional[str] = None
    try:
        image_url = metadata_artist_image.get_artist_image_url(artist_id, source_override=source)
    except Exception as e:
        logger.debug(f"Artist image lookup failed for {source}:{artist_id}: {e}")

    # 2. Source-side artist info (image, genres, followers depending on source).
    source_genres: list = []
    source_followers: Optional[int] = None
    try:
        if source == "spotify" and spotify_client is not None:
            sp_artist = spotify_client.get_artist(artist_id, allow_fallback=False)
            if sp_artist:
                source_genres = sp_artist.get("genres") or []
                source_followers = (sp_artist.get("followers") or {}).get("total")
                if not image_url and sp_artist.get("images"):
                    image_url = sp_artist["images"][0].get("url")
        elif source == "deezer" and deezer_client is not None:
            dz_artist = deezer_client.get_artist_info(artist_id)
            if dz_artist:
                source_genres = dz_artist.get("genres") or []
                source_followers = (dz_artist.get("followers") or {}).get("total")
        elif source == "itunes" and itunes_client is not None:
            it_artist = itunes_client.get_artist(artist_id)
            if it_artist:
                source_genres = it_artist.get("genres") or []
        elif source == "discogs" and discogs_client is not None:
            dc_artist = discogs_client.get_artist(artist_id)
            if dc_artist:
                source_genres = dc_artist.get("genres") or []
    except Exception as e:
        logger.debug(f"Source-side artist info lookup failed for {source}:{artist_id}: {e}")

    # 3. Last.fm enrichment by artist name.
    lastfm_bio: Optional[str] = None
    lastfm_listeners: Optional[int] = None
    lastfm_playcount: Optional[int] = None
    lastfm_url: Optional[str] = None
    if resolved_name and lastfm_api_key:
        try:
            from core.lastfm_client import LastFMClient
            lastfm = LastFMClient(api_key=lastfm_api_key)
            lf_info = lastfm.get_artist_info(resolved_name)
            if lf_info:
                bio_obj = lf_info.get("bio") or {}
                lastfm_bio = bio_obj.get("content") or bio_obj.get("summary")
                stats_obj = lf_info.get("stats") or {}
                if stats_obj.get("listeners"):
                    try:
                        lastfm_listeners = int(stats_obj["listeners"])
                    except (ValueError, TypeError):
                        pass
                if stats_obj.get("playcount"):
                    try:
                        lastfm_playcount = int(stats_obj["playcount"])
                    except (ValueError, TypeError):
                        pass
                lastfm_url = lf_info.get("url")
        except Exception as e:
            logger.debug(f"Last.fm enrichment failed for {resolved_name}: {e}")

    # 4. Discography from the specified source. Skip variant dedup so the
    #    page shows every release the source returns — matches the inline
    #    Artists-page behaviour that this view was modelled after.
    discography_result = metadata_discography.get_artist_detail_discography(
        artist_id,
        artist_name=resolved_name or artist_id,
        options=MetadataLookupOptions(
            source_override=source,
            allow_fallback=True,
            skip_cache=False,
            max_pages=0,
            # Match the Download Discography endpoint cap (200).
            # Spotify already paginates all; Deezer / iTunes / Discogs /
            # Hydrabase clamp at the outer limit. 200 covers prolific
            # catalogues without exceeding iTunes/Discogs internal caps.
            limit=200,
            artist_source_ids={source: artist_id},
            dedup_variants=False,
        ),
    )

    if not discography_result.get("success"):
        return {
            "success": False,
            "error": discography_result.get("error", "Could not load discography"),
            "source": source,
        }, 404

    artist_info: Dict[str, Any] = {
        "id": artist_id,
        "name": resolved_name or artist_id,
        "image_url": image_url,
        "server_source": None,  # not in library
        "genres": source_genres,
    }

    # Stamp the source-specific ID so the correct service badge renders on the
    # hero (e.g. source=deezer -> deezer_id; source=spotify -> spotify_artist_id).
    source_id_field = SOURCE_ID_FIELD.get(source)
    if source_id_field:
        artist_info[source_id_field] = artist_id

    if source_followers is not None:
        artist_info["followers"] = source_followers
    if lastfm_bio:
        artist_info["lastfm_bio"] = lastfm_bio
    if lastfm_listeners is not None:
        artist_info["lastfm_listeners"] = lastfm_listeners
    if lastfm_playcount is not None:
        artist_info["lastfm_playcount"] = lastfm_playcount
    if lastfm_url:
        artist_info["lastfm_url"] = lastfm_url

    logger.info(
        f"Source-only artist-detail: {artist_info['name']} from {source} — "
        f"albums={len(discography_result.get('albums', []))}, "
        f"eps={len(discography_result.get('eps', []))}, "
        f"singles={len(discography_result.get('singles', []))}, "
        f"genres={len(source_genres)}, lastfm_bio={'yes' if lastfm_bio else 'no'}"
    )

    return {
        "success": True,
        "artist": artist_info,
        "discography": discography_result,
        "enrichment_coverage": {},
    }, 200
