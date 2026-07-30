import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { SearchAlbum, SearchTrack } from './-search.types';

import {
  albumDetailParams,
  albumVirtualId,
  buildAlbumObject,
  buildArtistObject,
  buildSingleTrackAlbum,
  enrichAlbumTracks,
  enrichSingleTrack,
  openSearchAlbum,
  openSearchTrack,
  playOwnedTrack,
  streamSearchTrack,
  trackVirtualId,
  unsupportedStreamFormat,
} from './-search.actions';

const album = (over: Partial<SearchAlbum> = {}): SearchAlbum => ({
  id: 'alb1',
  name: 'Drukqs',
  artist: 'Aphex Twin',
  source: 'spotify',
  ...over,
});

const track = (over: Partial<SearchTrack> = {}): SearchTrack => ({
  id: 'trk1',
  name: 'Xtal',
  artist: 'Aphex Twin',
  album: 'SAW 85-92',
  duration_ms: 293_000,
  ...over,
});

const detail = {
  id: 'alb1',
  name: 'Drukqs',
  album_type: 'album',
  release_date: '2001-10-22',
  total_tracks: 2,
  images: [{ url: 'https://cdn/cover.jpg' }],
  artists: [{ id: 'art1', name: 'Aphex Twin', image_url: 'https://cdn/artist.jpg' }],
  tracks: [{ id: 'x', name: 'Avril 14th' }],
};

let modalCalls: unknown[][];
let bubbleCalls: unknown[][];
let toasts: unknown[][];

beforeEach(() => {
  modalCalls = [];
  bubbleCalls = [];
  toasts = [];
  window.openDownloadMissingModalForArtistAlbum = vi.fn((...args: unknown[]) => {
    modalCalls.push(args);
  }) as never;
  window.registerSearchDownload = vi.fn((...args: unknown[]) => {
    bubbleCalls.push(args);
  }) as never;
  window.showToast = vi.fn((...args: unknown[]) => {
    toasts.push(args);
  }) as never;
  window.showLoadingOverlay = vi.fn();
  window.hideLoadingOverlay = vi.fn();
  window.reopenActiveDownloadModal = vi.fn(() => false);
});

afterEach(() => {
  delete window.openDownloadMissingModalForArtistAlbum;
  delete window.registerSearchDownload;
  delete window.showToast;
  delete window.showLoadingOverlay;
  delete window.hideLoadingOverlay;
  delete window.reopenActiveDownloadModal;
  delete window.isAudioFormatSupported;
  delete window.getFileExtension;
  delete window.SoulSyncWebShellBridge;
});

describe('virtual playlist ids', () => {
  it('keeps albums and tracks in separate namespaces', () => {
    // Same id from two sections must not collide into one modal.
    expect(albumVirtualId(album({ id: '7' }))).toBe('enhanced_search_album_7');
    expect(trackVirtualId(track({ id: '7' }))).toBe('enhanced_search_track_7');
  });
});

describe('albumDetailParams', () => {
  it('always sends the name and artist the server matches on', () => {
    const params = albumDetailParams(album(), 'spotify');
    expect(params.get('name')).toBe('Drukqs');
    expect(params.get('artist')).toBe('Aphex Twin');
  });

  it('omits source for spotify and sends it otherwise', () => {
    expect(albumDetailParams(album(), 'spotify').has('source')).toBe(false);
    expect(albumDetailParams(album(), 'deezer').get('source')).toBe('deezer');
  });

  it('carries the hydrabase plugin so the server routes to the right client', () => {
    const params = albumDetailParams(
      album({ external_urls: { hydrabase_plugin: 'someplugin' } }),
      'hydrabase',
    );
    expect(params.get('plugin')).toBe('someplugin');
  });

  it('sends a bandcamp release url, which is the only way to fetch it', () => {
    // Bandcamp has no id-lookup API; without the url the server re-searches by
    // name and can land on a different release.
    const withUrl = album({ external_urls: { bandcamp: 'https://x.bandcamp.com/album/y' } });
    expect(albumDetailParams(withUrl, 'bandcamp').get('bandcamp_url')).toBe(
      'https://x.bandcamp.com/album/y',
    );
    // Only for bandcamp: another source's fetch has no use for it.
    expect(albumDetailParams(withUrl, 'deezer').has('bandcamp_url')).toBe(false);
  });
});

describe('modal payloads', () => {
  it('gives every track the whole album object', () => {
    // The modal reads it per track to build download jobs, and the wishlist
    // needs it to store a retryable entry.
    const tracks = enrichAlbumTracks(detail, album());
    expect(tracks[0].album).toEqual({
      name: 'Drukqs',
      id: 'alb1',
      album_type: 'album',
      images: [{ url: 'https://cdn/cover.jpg' }],
      release_date: '2001-10-22',
      total_tracks: 2,
    });
  });

  it('threads the metadata source onto each track', () => {
    // extract_source_metadata needs it downstream: without it, Deezer collab
    // tracks import tagged with only the primary artist.
    expect(enrichAlbumTracks(detail, album({ source: 'deezer' }))[0].source).toBe('deezer');
    // A track's own source wins over the album's.
    const withOwn = { ...detail, tracks: [{ id: 'x', source: 'tidal' }] };
    expect(enrichAlbumTracks(withOwn, album({ source: 'deezer' }))[0].source).toBe('tidal');
  });

  it('falls back to null rather than an empty source string', () => {
    expect(enrichAlbumTracks(detail, album({ source: undefined }))[0].source).toBeNull();
  });

  it('builds the album object from the DETAIL, not the search row', () => {
    // The search row has no track count and often a coarser title.
    const built = buildAlbumObject(detail, album({ name: 'drukqs (stale)' }));
    expect(built.name).toBe('Drukqs');
    expect(built.total_tracks).toBe(2);
  });

  it('names the artist from the detail, falling back to the row', () => {
    expect(buildArtistObject(detail, album(), 'spotify').id).toBe('art1');
    expect(buildArtistObject({ ...detail, artists: [] }, album(), 'spotify').name).toBe(
      'Aphex Twin',
    );
  });

  it('reads an artist id out of a compound album id when there is nothing else', () => {
    // Some sources mint `<artistId>_<rest>`. Nonsense elsewhere, which is why it
    // is the last resort.
    const built = buildArtistObject({ ...detail, artists: [] }, album({ id: 'art9_alb3' }), 'itunes');
    expect(built.id).toBe('art9');
    expect(built.source).toBe('itunes');
  });

  it('prefers the real artist list over the joined display string', () => {
    // "A, B" made collab downloads land tagged with one combined artist.
    const enriched = enrichSingleTrack(track({ artists: ['Aphex Twin', 'Squarepusher'] }));
    expect(enriched.artists).toEqual(['Aphex Twin', 'Squarepusher']);
    expect(enrichSingleTrack(track()).artists).toEqual(['Aphex Twin']);
  });

  it('wraps a single track as a one-track single', () => {
    const built = buildSingleTrackAlbum(track({ image_url: 'https://cdn/t.jpg' }));
    expect(built).toMatchObject({
      name: 'SAW 85-92',
      album_type: 'single',
      total_tracks: 1,
      images: [{ url: 'https://cdn/t.jpg' }],
      artists: [{ name: 'Aphex Twin' }],
    });
  });

  it('sends no images at all when the track has no art', () => {
    expect(buildSingleTrackAlbum(track({ image_url: undefined })).images).toEqual([]);
  });
});

describe('unsupportedStreamFormat', () => {
  it('skips the check for sources whose filenames are opaque ids', () => {
    // A youtube "filename" is an encoded id; reading an extension off it rejects
    // tracks that play fine.
    window.isAudioFormatSupported = vi.fn(() => false);
    for (const username of ['youtube', 'tidal', 'qobuz', 'hifi']) {
      expect(unsupportedStreamFormat({ username, filename: 'abc123' })).toBeNull();
    }
    expect(window.isAudioFormatSupported).not.toHaveBeenCalled();
  });

  it('names the format when the browser cannot play it', () => {
    window.isAudioFormatSupported = vi.fn(() => false);
    window.getFileExtension = vi.fn(() => 'wma');
    expect(unsupportedStreamFormat({ username: 'someuser', filename: 'x.wma' })).toBe('WMA');
  });

  it('passes a supported format', () => {
    window.isAudioFormatSupported = vi.fn(() => true);
    expect(unsupportedStreamFormat({ username: 'someuser', filename: 'x.flac' })).toBeNull();
  });

  it('lets a result with no filename through, for the player to judge', () => {
    window.isAudioFormatSupported = vi.fn(() => false);
    expect(unsupportedStreamFormat({ username: 'someuser' })).toBeNull();
  });
});

describe('openSearchAlbum', () => {
  it('opens the modal and registers the download bubble', async () => {
    server.use(http.get('/api/spotify/album/alb1', () => HttpResponse.json(detail)));
    await openSearchAlbum(album(), 'spotify');

    expect(modalCalls).toHaveLength(1);
    const [id, heading, tracks, albumObject, artistObject, overlay] = modalCalls[0];
    expect(id).toBe('enhanced_search_album_alb1');
    expect(heading).toBe('[Aphex Twin] Drukqs');
    expect(tracks).toHaveLength(1);
    expect((albumObject as { name: string }).name).toBe('Drukqs');
    expect((artistObject as { id: string }).id).toBe('art1');
    // False: this flow already raised its own overlay.
    expect(overlay).toBe(false);

    expect(bubbleCalls[0][1]).toBe('album');
    expect(bubbleCalls[0][3]).toBe('Aphex Twin');
  });

  it('reopens an existing modal WITHOUT fetching album detail', async () => {
    // The point is not saving a request: on a re-click while the source is down,
    // fetching first turns the modal the user already had into an error toast.
    let fetched = false;
    server.use(
      http.get('/api/spotify/album/alb1', () => {
        fetched = true;
        return HttpResponse.json(detail);
      }),
    );
    window.reopenActiveDownloadModal = vi.fn(() => true);

    await openSearchAlbum(album(), 'spotify');
    expect(fetched).toBe(false);
    expect(modalCalls).toHaveLength(0);
    expect(window.showLoadingOverlay).not.toHaveBeenCalled();
  });

  it('explains an empty tracklist instead of opening an empty modal', async () => {
    server.use(
      http.get('/api/spotify/album/alb1', () => HttpResponse.json({ ...detail, tracks: [] })),
    );
    await openSearchAlbum(album(), 'spotify');

    expect(modalCalls).toHaveLength(0);
    expect(String(toasts[0][0])).toContain('No tracks available for "Drukqs"');
    expect(toasts[0][1]).toBe('warning');
  });

  it('says when Spotify is not authenticated rather than "500"', async () => {
    server.use(http.get('/api/spotify/album/alb1', () => new HttpResponse(null, { status: 401 })));
    await openSearchAlbum(album(), 'spotify');
    expect(String(toasts[0][0])).toContain('Spotify not authenticated');
  });

  it('always takes the overlay back down', async () => {
    server.use(http.get('/api/spotify/album/alb1', () => new HttpResponse(null, { status: 500 })));
    await openSearchAlbum(album(), 'spotify');
    expect(window.hideLoadingOverlay).toHaveBeenCalled();
    expect(toasts).toHaveLength(1);
  });
});

describe('openSearchTrack', () => {
  it('opens a one-track modal and registers the bubble', async () => {
    await openSearchTrack(track());

    const [id, heading, tracks, albumObject, artistObject] = modalCalls[0];
    expect(id).toBe('enhanced_search_track_trk1');
    expect(heading).toBe('Aphex Twin - Xtal');
    expect(tracks).toHaveLength(1);
    expect((albumObject as { album_type: string }).album_type).toBe('single');
    expect(artistObject).toEqual({ id: null, name: 'Aphex Twin' });
    expect(bubbleCalls[0][1]).toBe('track');
  });

  it('reopens an existing modal instead of building a second one', async () => {
    window.reopenActiveDownloadModal = vi.fn(() => true);
    await openSearchTrack(track());
    expect(modalCalls).toHaveLength(0);
  });
});

describe('playOwnedTrack', () => {
  it('plays the LIBRARY row, not the search result', () => {
    // playLibraryTrack needs the library's own id, title and album thumb; the
    // search result knows none of them, which is why the whole row travels.
    const playLibraryTrack = vi.fn();
    window.SoulSyncWebShellBridge = { playLibraryTrack } as never;

    playOwnedTrack({
      track_id: 42,
      title: 'Xtal',
      file_path: '/music/xtal.flac',
      album_thumb_url: 'https://cdn/t.jpg',
      album_title: 'SAW 85-92',
      artist_name: 'Aphex Twin',
    });

    expect(playLibraryTrack).toHaveBeenCalledWith(
      { id: 42, title: 'Xtal', file_path: '/music/xtal.flac', _stats_image: 'https://cdn/t.jpg' },
      'SAW 85-92',
      'Aphex Twin',
    );
  });

  it('refuses to play a row with no file', () => {
    // "Owned" can mean a Plex-only entry with nothing on disk. Calling the
    // player with an empty path opens it on a track that cannot load.
    const playLibraryTrack = vi.fn();
    window.SoulSyncWebShellBridge = { playLibraryTrack } as never;

    playOwnedTrack({ track_id: 42, title: 'Xtal' });
    expect(playLibraryTrack).not.toHaveBeenCalled();
  });

  it('sends empty strings rather than undefined for the missing bits', () => {
    // The player writes these straight into the now-playing UI.
    const playLibraryTrack = vi.fn();
    window.SoulSyncWebShellBridge = { playLibraryTrack } as never;

    playOwnedTrack({ file_path: '/music/x.flac' });
    expect(playLibraryTrack).toHaveBeenCalledWith(
      { id: '', title: '', file_path: '/music/x.flac', _stats_image: null },
      '',
      '',
    );
  });
});

describe('streamSearchTrack', () => {
  it('sends the track metadata and starts the player', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('/api/enhanced-search/stream-track', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, result: { username: 'someuser' } });
      }),
    );
    const startStream = vi.fn();
    window.SoulSyncWebShellBridge = { startStream } as never;
    window.isAudioFormatSupported = vi.fn(() => true);

    await streamSearchTrack(track());

    expect(body).toEqual({
      track_name: 'Xtal',
      artist_name: 'Aphex Twin',
      album_name: 'SAW 85-92',
      duration_ms: 293_000,
    });
    expect(startStream).toHaveBeenCalledWith({ username: 'someuser' });
  });

  it('drops the overlay BEFORE the player opens', async () => {
    // startStream opens the media player; behind a loading overlay it is hidden.
    const order: string[] = [];
    server.use(
      http.post('/api/enhanced-search/stream-track', () =>
        HttpResponse.json({ success: true, result: { username: 'youtube', filename: 'abc' } }),
      ),
    );
    window.hideLoadingOverlay = vi.fn(() => order.push('hide')) as never;
    window.SoulSyncWebShellBridge = { startStream: vi.fn(() => order.push('stream')) } as never;

    await streamSearchTrack(track());
    expect(order.indexOf('hide')).toBeLessThan(order.indexOf('stream'));
  });

  it('refuses a format the browser cannot decode, and does not play it', async () => {
    server.use(
      http.post('/api/enhanced-search/stream-track', () =>
        HttpResponse.json({ success: true, result: { username: 'someuser', filename: 'x.wma' } }),
      ),
    );
    const startStream = vi.fn();
    window.SoulSyncWebShellBridge = { startStream } as never;
    window.isAudioFormatSupported = vi.fn(() => false);
    window.getFileExtension = vi.fn(() => 'wma');

    await streamSearchTrack(track());
    expect(startStream).not.toHaveBeenCalled();
    expect(String(toasts[0][0])).toContain('WMA format is not supported');
  });

  it('surfaces the server’s own error message', async () => {
    server.use(
      http.post('/api/enhanced-search/stream-track', () =>
        HttpResponse.json({ error: 'slskd is offline' }, { status: 503 }),
      ),
    );
    await streamSearchTrack(track());
    expect(String(toasts[0][0])).toContain('slskd is offline');
  });

  it('says nothing was found when the search succeeds but matches nothing', async () => {
    server.use(
      http.post('/api/enhanced-search/stream-track', () => HttpResponse.json({ success: false })),
    );
    await streamSearchTrack(track());
    expect(String(toasts[0][0])).toContain('No suitable track found');
  });
});
