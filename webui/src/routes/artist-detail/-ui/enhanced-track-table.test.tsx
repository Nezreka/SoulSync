import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createShellBridge } from '@/test/shell-bridge';

import type { EnhancedAlbum } from '../-artist-detail.enhanced';

import { EnhancedTrackTable } from './enhanced-track-table';

const ALBUM: EnhancedAlbum = {
  id: 7,
  tracks: [
    {
      id: 1,
      track_number: 1,
      disc_number: 1,
      title: 'Xtal',
      duration: 300_000,
      bitrate: 1000,
      bpm: 120,
      file_path: '/music/Aphex/SAW/01 Xtal.flac',
      spotify_track_id: 'sp1',
    },
    {
      id: 2,
      track_number: 2,
      disc_number: 1,
      title: 'Tha',
      duration: 570_000,
      bitrate: 192,
      file_path: '/music/Aphex/SAW/02 Tha.mp3',
    },
  ],
  missing_tracks: [
    { title: 'Ageispolis', track_number: 3, disc_number: 1, source: 'spotify', track_id: 's3' },
  ],
};

function renderTable(album: EnhancedAlbum = ALBUM, isAdmin = true, selected = new Set<string>()) {
  const onSelectedChange = vi.fn();
  const onTrackEdited = vi.fn();
  const view = render(
    <EnhancedTrackTable
      album={album}
      isAdmin={isAdmin}
      artist={ARTIST}
      selected={selected}
      onSelectedChange={onSelectedChange}
      onTrackEdited={onTrackEdited}
    />,
  );
  return { onSelectedChange, onTrackEdited, ...view };
}

const ARTIST = { id: 42, name: 'Aphex Twin', thumb_url: 'artist.jpg' };

const rows = () => [...document.querySelectorAll('tbody tr')] as HTMLElement[];

const ACTIONS = [
  'showTagPreview',
  'analyzeTrackReplayGain',
  'showTrackSourceInfo',
  'openReidentifyModal',
  'showTrackRedownloadModal',
  'deleteLibraryTrack',
  'openMissingTrackManageModal',
  'showReportIssueModal',
  'openManualMatchModal',
  '_showMobileTrackActions',
  'addToQueue',
  'playNext',
] as const;

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  for (const action of ACTIONS) window[action] = vi.fn() as never;
});

afterEach(() => {
  for (const action of ACTIONS) delete window[action];
  delete window.SoulSyncWebShellBridge;
  // NOT document.body.innerHTML = '': anything rendered through BodyPortal
  // lives there, and wiping the body out from under Testing Library's cleanup
  // makes it throw "The node to be removed is not a child of this node".
  cleanup();
});

describe('the empty state', () => {
  it('says so instead of rendering a headers-only table', () => {
    renderTable({ id: 7, tracks: [] });
    expect(document.querySelector('.enhanced-no-tracks')?.textContent).toBe(
      'No tracks in database',
    );
    expect(document.querySelector('table')).toBeNull();
  });
});

describe('columns', () => {
  it('gives an admin a select-all box, write-tag and delete columns', () => {
    renderTable();
    const headers = [...document.querySelectorAll('thead th')].map((th) => th.className);
    expect(headers[0]).toBe('');
    expect(headers).toContain('col-writetag');
    expect(headers).toContain('col-delete');
    expect(headers).not.toContain('col-report');
  });

  it('gives everyone else a report column and no select-all', () => {
    renderTable(ALBUM, false);
    expect(document.querySelector('thead .enhanced-track-checkbox')).toBeNull();
    const headers = [...document.querySelectorAll('thead th')].map((th) => th.className);
    expect(headers).toContain('col-report');
    expect(headers).not.toContain('col-writetag');
  });

  it('marks the sortable columns and leaves the rest inert', () => {
    renderTable();
    const sortable = [...document.querySelectorAll('thead th[data-sort-field]')].map((th) =>
      th.getAttribute('data-sort-field'),
    );
    expect(sortable).toEqual([
      'track_number',
      'disc_number',
      'title',
      'duration',
      'format',
      'bitrate',
      'bpm',
    ]);
  });
});

describe('the sort arrow', () => {
  it('appears on the clicked column and flips on a second click', () => {
    renderTable();
    const th = document.querySelector('[data-sort-field="title"]') as HTMLElement;

    fireEvent.click(th);
    expect(th.textContent).toBe('Title ▲');
    fireEvent.click(th);
    expect(th.textContent).toBe('Title ▼');
  });

  it('moves to the newly clicked column', () => {
    renderTable();
    fireEvent.click(document.querySelector('[data-sort-field="title"]') as HTMLElement);
    fireEvent.click(document.querySelector('[data-sort-field="bpm"]') as HTMLElement);
    expect(document.querySelector('[data-sort-field="title"]')?.textContent).toBe('Title');
    expect(document.querySelector('[data-sort-field="bpm"]')?.textContent).toBe('BPM ▲');
  });

  it('does NOT reorder the rows — verbatim vanilla', () => {
    // sortEnhancedTracks sorted album.tracks and the table then rendered from
    // _getEnhancedAlbumTrackRows, which re-sorts by disc/track/title. So the
    // arrow moves and the rows do not.
    renderTable();
    const before = rows().map((r) => r.getAttribute('data-track-id'));
    fireEvent.click(document.querySelector('[data-sort-field="title"]') as HTMLElement);
    expect(rows().map((r) => r.getAttribute('data-track-id'))).toEqual(before);
  });
});

describe('an owned row', () => {
  it('shows duration, format, bitrate, bpm and the file BASENAME', () => {
    renderTable();
    const row = rows()[0];
    expect(row.querySelector('.col-duration')?.textContent).toBe('5:00');
    expect(row.querySelector('.enhanced-format-badge')?.textContent).toBe('FLAC');
    expect(row.querySelector('.enhanced-bitrate')?.textContent).toBe('1000 kbps');
    expect(row.querySelector('.col-bpm')?.textContent).toBe('120');
    // The full path is the tooltip; the cell shows the file name.
    expect(row.querySelector('.col-path')?.textContent).toBe('01 Xtal.flac');
    expect(row.querySelector('.col-path')?.getAttribute('title')).toBe(
      '/music/Aphex/SAW/01 Xtal.flac',
    );
  });

  it('classes the bitrate by band', () => {
    renderTable();
    expect(rows()[0].querySelector('.enhanced-bitrate')?.className).toContain('high');
    expect(rows()[1].querySelector('.enhanced-bitrate')?.className).toContain('medium');
  });

  it('chips every match service, matched or not', () => {
    renderTable();
    const chips = [...rows()[0].querySelectorAll('.enhanced-track-match-chip')];
    expect(chips).toHaveLength(8);
    expect(chips[0].className).toContain('matched');
    expect(chips[0].getAttribute('title')).toBe('spotify: sp1');
    expect(chips[1].className).toContain('not-found');
    // An unmatched chip must not claim an id it does not have.
    expect(chips[1].getAttribute('title')).toBe('musicbrainz: no match');
  });

  it('offers play, queue and the admin action buttons', () => {
    renderTable();
    const row = rows()[0];
    expect(row.querySelector('.enhanced-play-btn')?.textContent).toBe('▶');
    expect(row.querySelector('.enhanced-queue-btn')).not.toBeNull();
    expect(row.querySelector('.enhanced-write-tag-btn')).not.toBeNull();
    expect(row.querySelector('.enhanced-delete-btn')).not.toBeNull();
  });

  it('marks the editable cells for an admin only', () => {
    renderTable();
    expect(rows()[0].querySelector('.col-title')?.className).toContain('editable');

    document.body.innerHTML = '';
    renderTable(ALBUM, false);
    expect(rows()[0].querySelector('.col-title')?.className).not.toContain('editable');
    expect(rows()[0].querySelector('.enhanced-track-report-btn')).not.toBeNull();
  });

  it('DISABLES play for a track with no file', () => {
    renderTable({ id: 7, tracks: [{ id: 1, track_number: 1, title: 'No file' }] });
    const play = document.querySelector('.enhanced-play-btn') as HTMLButtonElement;
    expect(play.disabled).toBe(true);
    expect(play.title).toBe('No file available');
    // Nothing to queue either.
    expect(document.querySelector('.enhanced-queue-btn')).toBeNull();
  });
});

describe('a missing row', () => {
  const missingRow = () => rows()[2];

  it('is badged, dashed and un-selectable', () => {
    renderTable();
    const row = missingRow();
    expect(row.className).toContain('enhanced-missing-track-row');
    expect(row.querySelector('.enhanced-missing-track-badge')?.textContent).toBe('Missing');
    expect(row.querySelector('.enhanced-play-btn')?.textContent).toBe('—');
    expect(row.querySelector('.col-format')?.textContent).toBe('-');
    expect(row.querySelector('.col-path')?.textContent).toBe('Missing from library');
    // An empty cell, not a disabled box: there is no file to bulk-act on.
    expect(row.querySelector('.enhanced-track-checkbox')).toBeNull();
  });

  it('keeps the track NUMBER editable but not the disc or title (#1051)', () => {
    // The number is the slot the row claims; disc and title describe a real
    // file's tags, which a missing row does not have.
    renderTable();
    const row = missingRow();
    expect(row.querySelector('.col-num')?.className).toContain('editable');
    expect(row.querySelector('.col-disc')?.className).not.toContain('editable');
    expect(row.querySelector('.col-title')?.className).not.toContain('editable');
  });

  it('offers Manage instead of the owned-track actions', () => {
    renderTable();
    const row = missingRow();
    expect(row.querySelector('.enhanced-missing-manage-btn')?.textContent).toBe('Manage');
    expect(row.querySelector('.enhanced-delete-btn')).toBeNull();
    expect(row.querySelector('.enhanced-write-tag-btn')).toBeNull();
  });

  it('offers Manage to a non-admin too, in place of Report', () => {
    renderTable(ALBUM, false);
    expect(missingRow().querySelector('.enhanced-missing-manage-btn')).not.toBeNull();
    expect(missingRow().querySelector('.enhanced-track-report-btn')).toBeNull();
  });
});

describe('selection', () => {
  it('ticks one row', () => {
    const { onSelectedChange } = renderTable();
    fireEvent.click(rows()[0].querySelector('.enhanced-track-checkbox') as HTMLElement);
    expect(onSelectedChange).toHaveBeenCalledWith(new Set(['1']));
  });

  it('unticks a row that was already selected', () => {
    const { onSelectedChange } = renderTable(ALBUM, true, new Set(['1']));
    fireEvent.click(rows()[0].querySelector('.enhanced-track-checkbox') as HTMLElement);
    expect(onSelectedChange).toHaveBeenCalledWith(new Set());
  });

  it('select-all covers the OWNED rows only', () => {
    const { onSelectedChange } = renderTable();
    fireEvent.click(document.querySelector('thead .enhanced-track-checkbox') as HTMLElement);
    // The missing row has no id in the set.
    expect(onSelectedChange).toHaveBeenCalledWith(new Set(['1', '2']));
  });

  it('select-all clears when everything owned is already ticked', () => {
    const { onSelectedChange } = renderTable(ALBUM, true, new Set(['1', '2']));
    const all = document.querySelector('thead .enhanced-track-checkbox') as HTMLInputElement;
    expect(all.checked).toBe(true);
    fireEvent.click(all);
    expect(onSelectedChange).toHaveBeenCalledWith(new Set());
  });

  it('marks the selected row', () => {
    renderTable(ALBUM, true, new Set(['1']));
    expect(rows()[0].className).toContain('selected');
    expect(rows()[1].className).not.toContain('selected');
  });
});

describe('row actions', () => {
  const click = (selector: string, row = 0) =>
    fireEvent.click(rows()[row].querySelector(selector) as HTMLElement);

  it('plays through the library player with the album and artist names', () => {
    renderTable();
    click('.enhanced-play-btn');
    expect(window.SoulSyncWebShellBridge?.playLibraryTrack).toHaveBeenCalledWith(
      expect.objectContaining({ id: 1 }),
      '',
      'Aphex Twin',
    );
  });

  it('queues with a library payload, art falling back to the ARTIST thumbnail', () => {
    renderTable();
    click('.enhanced-queue-btn');
    expect(window.addToQueue).toHaveBeenCalledWith(
      expect.objectContaining({
        is_library: true,
        file_path: '/music/Aphex/SAW/01 Xtal.flac',
        album: 'Unknown Album',
        artist: 'Aphex Twin',
        image_url: 'artist.jpg',
        artist_id: 42,
        album_id: 7,
      }),
    );
  });

  it('play-next uses playNext, not the plain enqueue', () => {
    renderTable();
    click('.enhanced-playnext-btn');
    expect(window.playNext).toHaveBeenCalled();
    expect(window.addToQueue).not.toHaveBeenCalled();
  });

  it('falls back to a plain enqueue when the player has no play-next', () => {
    delete window.playNext;
    renderTable();
    click('.enhanced-playnext-btn');
    expect(window.addToQueue).toHaveBeenCalled();
  });

  it('wires each admin action to its own handler', () => {
    renderTable();
    click('.enhanced-write-tag-btn');
    expect(window.showTagPreview).toHaveBeenCalledWith(1);

    click('.enhanced-redownload-btn');
    expect(window.showTrackRedownloadModal).toHaveBeenCalledWith(
      expect.objectContaining({ id: 1 }),
      ALBUM,
    );

    click('.enhanced-delete-btn');
    expect(window.deleteLibraryTrack).toHaveBeenCalledWith(1, 7);
  });

  it('hands the button element to the two actions that render onto it', () => {
    renderTable();
    const rg = rows()[0].querySelector('.enhanced-rg-btn') as HTMLElement;
    fireEvent.click(rg);
    expect(window.analyzeTrackReplayGain).toHaveBeenCalledWith(1, rg);

    const info = rows()[0].querySelector('.enhanced-source-info-btn') as HTMLElement;
    fireEvent.click(info);
    expect(window.showTrackSourceInfo).toHaveBeenCalledWith(
      expect.objectContaining({ id: 1 }),
      info,
    );
  });

  it('re-identifies with the album art and title for context', () => {
    renderTable({ ...ALBUM, title: 'SAW 85-92', thumb_url: 'cover.jpg' });
    click('.enhanced-reidentify-btn');
    expect(window.openReidentifyModal).toHaveBeenCalledWith(
      1,
      'Xtal',
      'Aphex Twin',
      'SAW 85-92',
      'cover.jpg',
    );
  });

  it('manages a missing track from either role', () => {
    renderTable();
    click('.enhanced-missing-manage-btn', 2);
    expect(window.openMissingTrackManageModal).toHaveBeenCalledWith(
      expect.objectContaining({ _missingExpected: true }),
      ALBUM,
    );

    document.body.innerHTML = '';
    renderTable(ALBUM, false);
    click('.enhanced-missing-manage-btn', 2);
    expect(window.openMissingTrackManageModal).toHaveBeenCalledTimes(2);
  });

  it('reports a track issue with its ALBUM name too', () => {
    renderTable(ALBUM, false);
    click('.enhanced-track-report-btn');
    expect(window.showReportIssueModal).toHaveBeenCalledWith('track', 1, 'Xtal', 'Aphex Twin', '');
  });

  it('opens the mobile action popover', () => {
    renderTable();
    click('.enhanced-mobile-actions-btn');
    expect(window._showMobileTrackActions).toHaveBeenCalled();
  });

  it('does not let an action bubble into the album toggle', () => {
    const onPanelClick = vi.fn();
    render(
      <div onClick={onPanelClick}>
        <EnhancedTrackTable
          album={ALBUM}
          isAdmin
          artist={ARTIST}
          selected={new Set()}
          onSelectedChange={vi.fn()}
          onTrackEdited={vi.fn()}
        />
      </div>,
    );
    fireEvent.click(document.querySelector('.enhanced-delete-btn') as HTMLElement);
    expect(onPanelClick).not.toHaveBeenCalled();
  });
});

describe('re-matching a track', () => {
  it('opens the matcher for the clicked service, with the BARE track title', () => {
    // The album has a title here on purpose: every service except Bandcamp
    // searched better on the title alone.
    renderTable({ ...ALBUM, title: 'SAW 85-92' });
    fireEvent.click(rows()[0].querySelectorAll('.enhanced-track-match-chip')[1]);
    expect(window.openManualMatchModal).toHaveBeenCalledWith('track', 1, 'musicbrainz', 'Xtal', 42);
  });

  it('does not leave a leading space when the album is untitled', () => {
    renderTable();
    const chips = rows()[0].querySelectorAll('.enhanced-track-match-chip');
    fireEvent.click(chips[chips.length - 1]);
    expect(window.openManualMatchModal).toHaveBeenCalledWith('track', 1, 'bandcamp', 'Xtal', 42);
  });

  it('sends the ALBUM name alongside the title for Bandcamp only', () => {
    // Bandcamp searches release pages, where a bare track title is ambiguous
    // across compilations, remixes and covers.
    renderTable({ ...ALBUM, title: 'SAW 85-92' });
    const chips = rows()[0].querySelectorAll('.enhanced-track-match-chip');
    fireEvent.click(chips[chips.length - 1]);
    expect(window.openManualMatchModal).toHaveBeenCalledWith(
      'track',
      1,
      'bandcamp',
      'SAW 85-92 Xtal',
      42,
    );
  });

  it('is inert for a non-admin', () => {
    renderTable(ALBUM, false);
    fireEvent.click(rows()[0].querySelectorAll('.enhanced-track-match-chip')[1]);
    expect(window.openManualMatchModal).not.toHaveBeenCalled();
  });
});
