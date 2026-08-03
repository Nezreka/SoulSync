import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { EnhancedAlbum } from '../-artist-detail.enhanced';

import { getAlbumTrackRows } from '../-artist-detail.enhanced-album';
import { ExpandedAlbumHeader } from './expanded-album-header';

const ALBUM: EnhancedAlbum = {
  id: 7,
  title: 'SAW 85-92',
  year: 1992,
  label: 'Apollo',
  record_type: 'album',
  thumb_url: 'cover.jpg',
  genres: ['ambient', 'idm'],
  spotify_album_id: 'sp1',
  spotify_match_status: 'matched',
  tracks: [{ id: 1, duration: 300_000, track_number: 1 }],
};

function renderHeader(album: EnhancedAlbum = ALBUM, isAdmin = true) {
  return render(
    <ExpandedAlbumHeader
      album={album}
      rows={getAlbumTrackRows(album)}
      artistId={42}
      artistName="Aphex Twin"
      isAdmin={isAdmin}
      onArtApplied={vi.fn()}
      onAlbumDeleted={vi.fn()}
      onAlbumPatched={vi.fn()}
    />,
  );
}

const ACTIONS = [
  'openAlbumArtPicker',
  'openManualMatchModal',
  'runEnrichment',
  'deleteLibraryAlbum',
  'showReportIssueModal',
] as const;

beforeEach(() => {
  for (const action of ACTIONS) window[action] = vi.fn() as never;
});

afterEach(() => {
  for (const action of ACTIONS) delete window[action];
  // NOT document.body.innerHTML = '': anything rendered through BodyPortal
  // lives there, and wiping the body out from under Testing Library's cleanup
  // makes it throw "The node to be removed is not a child of this node".
  cleanup();
});

describe('the header body', () => {
  it('shows the title, detail line, genres and id badges', () => {
    renderHeader();
    expect(document.querySelector('.enhanced-expanded-title')?.textContent).toBe('SAW 85-92');
    expect(document.querySelector('.enhanced-expanded-meta')?.textContent).toBe(
      '1992 · 1 track · 5:00 · Apollo · ALBUM',
    );
    expect([...document.querySelectorAll('.enhanced-genre-tag')].map((n) => n.textContent)).toEqual(
      ['ambient', 'idm'],
    );
    const badge = document.querySelector('.enhanced-id-badge') as HTMLAnchorElement;
    expect(badge.getAttribute('href')).toBe('https://open.spotify.com/album/sp1');
    expect(badge.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('omits the genre and id rows entirely when there are none', () => {
    renderHeader({ id: 7, title: 'X', tracks: [] });
    expect(document.querySelector('.enhanced-expanded-genres')).toBeNull();
    expect(document.querySelector('.enhanced-expanded-ids')).toBeNull();
  });

  it('HIDES a broken cover rather than removing it', () => {
    // Removing the img would collapse the wrap and lose the click target that
    // opens the art picker.
    renderHeader();
    const img = document.querySelector('.enhanced-expanded-art') as HTMLImageElement;
    fireEvent.error(img);
    expect(img.style.visibility).toBe('hidden');
    expect(document.querySelector('.enhanced-expanded-art')).not.toBeNull();
  });

  it('opens the art picker from the cover', async () => {
    // No longer a window bridge: the cover click mounts the local ArtPicker
    // (openAlbumArtPicker's port), which fetches this album's options.
    const fetchSpy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ candidates: [] })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    try {
      renderHeader();
      fireEvent.click(document.querySelector('.enhanced-expanded-art-wrap') as HTMLElement);
      expect(await screen.findByText('Choose cover art')).toBeTruthy();
      expect(String(fetchSpy.mock.calls[0][0])).toContain('/art-options');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('match chips', () => {
  it('renders one per service with its status', () => {
    renderHeader();
    const chips = [...document.querySelectorAll('.enhanced-match-chip')];
    expect(chips).toHaveLength(9);
    expect(chips[0].textContent).toBe('Spotify: matched');
    expect(chips[1].textContent).toBe('MB: —');
  });

  it('opens the manual matcher for its own service', () => {
    // The chip now mounts the LOCAL match modal (auto-search stubs fetch),
    // seeded with the album title as the default query.
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_i: RequestInfo | URL, _init?: RequestInit) =>
          new Response(JSON.stringify({ success: true, results: [] })),
      ),
    );
    try {
      renderHeader();
      fireEvent.click(document.querySelectorAll('.enhanced-match-chip')[1]);
      expect(screen.getByText('Match album on MusicBrainz')).toBeTruthy();
      expect(
        (document.querySelector('.enhanced-match-search-input') as HTMLInputElement).value,
      ).toBe('SAW 85-92');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('does not let an ID BADGE click bubble into the row toggle', () => {
    // Following the link would otherwise also collapse the album underneath it.
    const onRowClick = vi.fn();
    render(
      <div onClick={onRowClick}>
        <ExpandedAlbumHeader
          album={ALBUM}
          rows={[]}
          artistId={42}
          artistName="Aphex Twin"
          isAdmin
          onArtApplied={vi.fn()}
          onAlbumDeleted={vi.fn()}
          onAlbumPatched={vi.fn()}
        />
      </div>,
    );
    fireEvent.click(document.querySelector('.enhanced-id-badge') as HTMLElement);
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('does not let the chip click bubble into the row toggle', () => {
    const onRowClick = vi.fn();
    render(
      <div onClick={onRowClick}>
        <ExpandedAlbumHeader
          album={ALBUM}
          rows={[]}
          artistId={42}
          artistName="Aphex Twin"
          isAdmin
          onArtApplied={vi.fn()}
          onAlbumDeleted={vi.fn()}
          onAlbumPatched={vi.fn()}
        />
      </div>,
    );
    fireEvent.click(document.querySelector('.enhanced-match-chip') as HTMLElement);
    expect(onRowClick).not.toHaveBeenCalled();
  });
});

describe('admin actions', () => {
  it('are hidden for a non-admin, but Report Issue stays', () => {
    renderHeader(ALBUM, false);
    expect(document.querySelector('.enhanced-enrich-wrap')).toBeNull();
    expect(document.querySelector('.enhanced-delete-album-btn')).toBeNull();
    expect(document.querySelector('.enhanced-report-issue-btn')).not.toBeNull();
  });

  it('reports an issue with the album and its artist', () => {
    renderHeader(ALBUM, false);
    fireEvent.click(document.querySelector('.enhanced-report-issue-btn') as HTMLElement);
    expect(window.showReportIssueModal).toHaveBeenCalledWith('album', 7, 'SAW 85-92', 'Aphex Twin');
  });

  it('opens the enrich menu on click and closes it after picking a source', () => {
    renderHeader();
    const menu = document.querySelector('.enhanced-enrich-menu') as HTMLElement;
    expect(menu.className).not.toContain('visible');

    fireEvent.click(document.querySelector('.enhanced-enrich-btn') as HTMLElement);
    expect(menu.className).toContain('visible');

    // Picking a source fires the local enrichment request (no window bridge).
    const fetchSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: true, results: {} })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    try {
      fireEvent.click(document.querySelectorAll('.enhanced-enrich-menu-item')[0]);
      expect(String(fetchSpy.mock.calls[0]?.[0])).toBe('/api/library/enrich');
      expect(JSON.parse(String(fetchSpy.mock.calls[0]?.[1]?.body))).toMatchObject({
        entity_type: 'album',
        entity_id: 7,
        service: 'spotify',
        name: 'SAW 85-92',
        artist_name: 'Aphex Twin',
        artist_id: 42,
      });
      expect(menu.className).not.toContain('visible');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('wires each album action to its own handler', () => {
    renderHeader();
    // Write-tags is local now (writeAlbumTags's port): the fixture's tracks
    // have no file_path, so it refuses instead of opening the batch modal.
    window.showToast = vi.fn() as never;
    fireEvent.click(document.querySelector('.enhanced-write-tags-album-btn') as HTMLElement);
    expect(window.showToast).toHaveBeenCalledWith('No tracks with files in this album', 'error');
    delete window.showToast;

    // Reorganize is local now (showReorganizeModal's port): the button
    // mounts the modal, which loads this album's metadata sources.
    const fetchSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ sources: [] })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    fireEvent.click(document.querySelector('.enhanced-reorganize-album-btn') as HTMLElement);
    expect(document.getElementById('reorganize-modal-title')?.textContent).toBe(
      'Reorganize: SAW 85-92',
    );
    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe('/api/library/album/7/reorganize/sources');
    vi.unstubAllGlobals();

    // Delete now opens the LOCAL two-option dialog (deleteLibraryAlbum's port).
    fireEvent.click(document.querySelector('.enhanced-delete-album-btn') as HTMLElement);
    expect(screen.getByText('Delete Album', { selector: 'h3' })).toBeTruthy();
  });

  it('opens the batch tag modal when the album has on-disk files', async () => {
    const fetchSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: true, tracks: [], server_type: null })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    try {
      renderHeader({
        id: 7,
        title: 'SAW 85-92',
        tracks: [{ id: 1, file_path: '/music/a.flac' }, { id: 2 }],
      });
      fireEvent.click(document.querySelector('.enhanced-write-tags-album-btn') as HTMLElement);
      expect(document.getElementById('batch-tag-preview-title')?.textContent).toBe(
        'Write Tags — SAW 85-92',
      );
      // Only the track that actually has a file goes into the batch (5449).
      await waitFor(() =>
        expect(
          fetchSpy.mock.calls.some(([u]) => String(u) === '/api/library/tracks/tag-preview-batch'),
        ).toBe(true),
      );
      const call = fetchSpy.mock.calls.find(
        ([u]) => String(u) === '/api/library/tracks/tag-preview-batch',
      );
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ track_ids: [1] });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('runs album ReplayGain locally, disabling the button while it works', async () => {
    const fetchSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: false, error: 'no files' })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    window.showToast = vi.fn() as never;
    try {
      renderHeader();
      const rg = document.querySelector('.enhanced-rg-album-btn') as HTMLElement;
      fireEvent.click(rg);
      expect(rg.textContent).toBe('♫ Analyzing…');
      expect(String(fetchSpy.mock.calls[0]?.[0])).toBe('/api/library/album/7/analyze-replaygain');
      // A refused job re-enables the button via onDone.
      await waitFor(() =>
        expect(window.showToast).toHaveBeenCalledWith('ReplayGain: no files', 'error'),
      );
      await waitFor(() => expect(rg.textContent).toBe('♫ ReplayGain'));
    } finally {
      vi.unstubAllGlobals();
      delete window.showToast;
    }
  });

  it('redownloads the album through its CANONICAL source (#911)', async () => {
    // Local now: the flow fetches the tagged edition via /api/album/<id>/tracks
    // and hands off to the shared Download Missing modal.
    const fetchSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            success: true,
            album: { id: 'sp1', name: 'SAW 85-92' },
            tracks: [{ id: 't1', name: 'Xtal' }],
          }),
        ),
    );
    vi.stubGlobal('fetch', fetchSpy);
    window.openDownloadMissingModalForArtistAlbum = vi.fn() as never;
    window.registerArtistDownload = vi.fn() as never;
    try {
      renderHeader();
      const redownload = document.querySelector('.enhanced-redownload-album-btn') as HTMLElement;
      fireEvent.click(redownload);
      expect(redownload.textContent).toBe('Loading...');
      await waitFor(() => expect(window.openDownloadMissingModalForArtistAlbum).toHaveBeenCalled());
      const url = String(fetchSpy.mock.calls[0]?.[0]);
      expect(url).toContain('/api/album/sp1/tracks');
      expect(url).toContain('source=spotify');
      const args = (window.openDownloadMissingModalForArtistAlbum as ReturnType<typeof vi.fn>).mock
        .calls[0];
      expect(args?.[0]).toBe('library_redownload_sp1');
      expect(args?.[1]).toBe('[Aphex Twin] SAW 85-92');
      expect(window.registerArtistDownload).toHaveBeenCalled();
      await waitFor(() => expect(redownload.textContent).toBe('↻ Redownload'));
    } finally {
      vi.unstubAllGlobals();
      delete window.openDownloadMissingModalForArtistAlbum;
      delete window.registerArtistDownload;
    }
  });

  it('survives a handler that is not loaded rather than throwing', () => {
    for (const action of ACTIONS) delete window[action];
    renderHeader();
    fireEvent.click(document.querySelector('.enhanced-delete-album-btn') as HTMLElement);
  });
});
