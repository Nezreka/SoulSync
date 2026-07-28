import { cleanup, fireEvent, render } from '@testing-library/react';
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
    />,
  );
}

const ACTIONS = [
  'openAlbumArtPicker',
  'openManualMatchModal',
  'runEnrichment',
  'writeAlbumTags',
  'analyzeAlbumReplayGain',
  'showReorganizeModal',
  'redownloadLibraryAlbum',
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

  it('opens the art picker from the cover', () => {
    renderHeader();
    fireEvent.click(document.querySelector('.enhanced-expanded-art-wrap') as HTMLElement);
    expect(window.openAlbumArtPicker).toHaveBeenCalledWith(ALBUM);
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
    renderHeader();
    fireEvent.click(document.querySelectorAll('.enhanced-match-chip')[1]);
    expect(window.openManualMatchModal).toHaveBeenCalledWith(
      'album',
      7,
      'musicbrainz',
      'SAW 85-92',
      42,
    );
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

    fireEvent.click(document.querySelectorAll('.enhanced-enrich-menu-item')[0]);
    expect(window.runEnrichment).toHaveBeenCalledWith(
      'album',
      7,
      'spotify',
      'SAW 85-92',
      'Aphex Twin',
      42,
    );
    expect(menu.className).not.toContain('visible');
  });

  it('wires each album action to its own handler', () => {
    renderHeader();
    fireEvent.click(document.querySelector('.enhanced-write-tags-album-btn') as HTMLElement);
    expect(window.writeAlbumTags).toHaveBeenCalledWith(7);

    fireEvent.click(document.querySelector('.enhanced-reorganize-album-btn') as HTMLElement);
    expect(window.showReorganizeModal).toHaveBeenCalledWith(7);

    fireEvent.click(document.querySelector('.enhanced-delete-album-btn') as HTMLElement);
    expect(window.deleteLibraryAlbum).toHaveBeenCalledWith(7);
  });

  it('hands the BUTTON itself to the two actions that render progress on it', () => {
    renderHeader();
    const rg = document.querySelector('.enhanced-rg-album-btn') as HTMLElement;
    fireEvent.click(rg);
    expect(window.analyzeAlbumReplayGain).toHaveBeenCalledWith(7, rg);

    const redownload = document.querySelector('.enhanced-redownload-album-btn') as HTMLElement;
    fireEvent.click(redownload);
    expect(window.redownloadLibraryAlbum).toHaveBeenCalledWith(ALBUM, 'Aphex Twin', redownload);
  });

  it('survives a handler that is not loaded rather than throwing', () => {
    for (const action of ACTIONS) delete window[action];
    renderHeader();
    fireEvent.click(document.querySelector('.enhanced-delete-album-btn') as HTMLElement);
  });
});
