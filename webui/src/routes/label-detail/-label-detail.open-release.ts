/**
 * Opening a release from the label grid, ported from `_openReleaseModal`.
 *
 * This is not a straight "call the modal" — the catalog comes from MusicBrainz,
 * which gives good tracklists but NO usable images: Cover Art Archive is
 * unreachable from the browser and times out server-side. So the release is
 * first re-resolved against a reliable source (the same call Search makes), and
 * only that source's album-detail carries images the modal AND the wishlist
 * entry can actually load.
 *
 * The url the wishlist stores has to be ABSOLUTE. `/api/labels/cover` is
 * relative, and the wishlist image normaliser rewrites any '/...' url and
 * breaks it — which is why the fallback goes through /api/labels/cover-url,
 * whose job is to hand back the CDN url itself.
 */

import type { LabelRelease } from './-label-detail.types';

import { normalizeForMatch } from './-label-detail.helpers';

interface AlbumDetail {
  name?: string;
  album_type?: string;
  release_date?: string;
  images?: { url?: string }[];
  artists?: { id?: string; name?: string; image_url?: string; images?: { url?: string }[] }[];
  tracks?: Record<string, unknown>[];
}

/**
 * Which album on a reliable source IS this MusicBrainz release.
 *
 * Exact artist+title, then title alone, then whatever came back first — the
 * vanilla's ladder. The loose normalisation is right here (and wrong for the
 * ownership key): two catalogues punctuate the same album differently.
 */
export function pickResolvedAlbum(
  albums: { id?: string; name?: string; artist?: string; source?: string }[],
  release: LabelRelease,
): { id?: string; name?: string; artist?: string; source?: string } | undefined {
  const wantAlbum = normalizeForMatch(release.album);
  const wantArtist = normalizeForMatch(release.artist);
  return (
    albums.find(
      (a) => normalizeForMatch(a.name) === wantAlbum && normalizeForMatch(a.artist) === wantArtist,
    ) ||
    albums.find((a) => normalizeForMatch(a.name) === wantAlbum) ||
    albums[0]
  );
}

/**
 * Is this album image usable?
 *
 * A Cover Art Archive url is worse than none: it will not load in the browser,
 * so it would render a broken image in the modal and store a dead url on the
 * wishlist entry.
 */
export function isUsableAlbumImage(url: string | undefined | null): boolean {
  return Boolean(url) && String(url).indexOf('coverartarchive.org') === -1;
}

/** The album object the download modal and the wishlist entry are built from. */
export function buildAlbumObject(
  detail: AlbumDetail,
  release: LabelRelease,
  albumId: string,
  source: string,
  fallbackImage: string,
): Record<string, unknown> {
  const detailImage = detail.images?.[0]?.url ?? '';
  const detailImageOk = isUsableAlbumImage(detailImage);
  const image = detailImageOk ? detailImage : fallbackImage;
  return {
    name: detail.name || release.album,
    id: albumId,
    album_type: detail.album_type || release.primary_type || 'album',
    images: detailImageOk ? detail.images : image ? [{ url: image }] : [],
    image_url: image,
    // A bare year is not a release date; the vanilla widened it to Jan 1 so the
    // modal and the wishlist entry both had something parseable.
    release_date: detail.release_date || (release.year ? `${release.year}-01-01` : ''),
    total_tracks: (detail.tracks ?? []).length,
    artists: detail.artists || [{ name: release.artist }],
    source,
  };
}

/** The artist object, preferring the resolved source's identity over the catalog's. */
export function buildArtistObject(
  detail: AlbumDetail,
  release: LabelRelease,
  source: string,
): Record<string, unknown> {
  const first = detail.artists?.[0] ?? {};
  return {
    id: first.id || release.artist_id || '',
    name: first.name || release.artist,
    image_url: first.image_url || first.images?.[0]?.url || '',
    source,
  };
}

/** The modal's synthetic id and heading, which the wishlist keys off. */
export function releaseModalIdentity(
  release: LabelRelease,
  albumId: string,
  albumName: string,
): { id: string; heading: string } {
  return { id: `lbl_album_${albumId}`, heading: `[${release.artist}] ${albumName}` };
}

/**
 * The whole click-to-download flow.
 *
 * Four steps, and every one of them is allowed to fail softly except the
 * tracklist:
 *   1. re-resolve the release on a reliable source (best effort — MusicBrainz
 *      is the fallback, and it still yields tracks),
 *   2. fetch that album's detail for tracks + images,
 *   3. if the images are unusable, ask /api/labels/cover-url for an ABSOLUTE
 *      CDN url the wishlist can store,
 *   4. hand the assembled album/artist/tracks to the shared download modal.
 *
 * With no modal available the vanilla degraded to a plain search handoff rather
 * than doing nothing, and so does this.
 */
export async function openLabelRelease(release: LabelRelease): Promise<void> {
  if (typeof window.openDownloadMissingModalForArtistAlbum !== 'function') {
    window._handoffLibrarySearchToEnhancedSearch?.(`${release.artist} ${release.album}`);
    return;
  }

  window.showLoadingOverlay?.('Loading album...');
  try {
    let source = '';
    let albumId = '';
    try {
      const found = await window.enhancedSearchFetch?.(`${release.artist} ${release.album}`, {});
      const picked = pickResolvedAlbum(found?.albums ?? [], release);
      if (picked?.id) {
        albumId = String(picked.id);
        source = picked.source || found?.metadata_source || '';
      }
    } catch {
      // Fall through to MusicBrainz below.
    }

    const useSource = source || 'musicbrainz';
    const useId = albumId || release.release_group_id || '';
    const params = new URLSearchParams({
      source: useSource,
      name: release.album ?? '',
      artist: release.artist ?? '',
    });
    const detail = (await fetch(`/api/spotify/album/${encodeURIComponent(useId)}?${params}`).then(
      (r) => r.json(),
    )) as AlbumDetail;

    const tracks = detail?.tracks ?? [];
    if (!tracks.length) {
      window.showToast?.('No tracks found for this release', 'error');
      return;
    }

    let fallbackImage = '';
    if (!isUsableAlbumImage(detail?.images?.[0]?.url)) {
      try {
        const query = new URLSearchParams({
          artist: release.artist ?? '',
          album: release.album ?? '',
        });
        const resolved = (await fetch(`/api/labels/cover-url?${query}`).then((r) => r.json())) as {
          url?: string;
        };
        fallbackImage = resolved?.url ?? '';
      } catch {
        fallbackImage = '';
      }
    }

    const album = buildAlbumObject(detail, release, useId, useSource, fallbackImage);
    const artist = buildArtistObject(detail, release, useSource);
    const identity = releaseModalIdentity(release, useId, String(album.name));
    // Every track carries the album + source, because the modal reads them per
    // track when it builds the download jobs.
    const enriched = tracks.map((track) => ({ ...track, source: useSource, album }));

    window.openDownloadMissingModalForArtistAlbum(
      identity.id,
      identity.heading,
      enriched,
      album,
      artist,
      false,
      'artist_album',
    );
  } catch {
    window.showToast?.('Could not open this release', 'error');
  } finally {
    window.hideLoadingOverlay?.();
  }
}
