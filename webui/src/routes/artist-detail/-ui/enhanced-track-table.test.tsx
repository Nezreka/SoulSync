import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  const onTrackDeleted = vi.fn();
  const view = render(
    <EnhancedTrackTable
      album={album}
      isAdmin={isAdmin}
      artist={ARTIST}
      selected={selected}
      onSelectedChange={onSelectedChange}
      onTrackEdited={onTrackEdited}
      onTrackDeleted={onTrackDeleted}
      onAlbumPatched={vi.fn()}
      onReload={vi.fn()}
    />,
  );
  return { onSelectedChange, onTrackEdited, onTrackDeleted, ...view };
}

const ARTIST = { id: 42, name: 'Aphex Twin', thumb_url: 'artist.jpg' };

const rows = () => [...document.querySelectorAll('tbody tr')] as HTMLElement[];

const titles = () =>
  rows().map((r) => r.querySelector('.col-title')?.textContent?.replace('Missing', '') ?? '');

const ACTIONS = [
  'showTrackSourceInfo',
  'deleteLibraryTrack',
  'showReportIssueModal',
  'openManualMatchModal',
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

  it('actually reorders the rows — the vanilla never did', () => {
    renderTable();
    // Default order is track number: Xtal (1), Tha (2), then the missing row.
    expect(titles()).toEqual(['Xtal', 'Tha', 'Ageispolis']);

    fireEvent.click(document.querySelector('[data-sort-field="title"]') as HTMLElement);
    expect(titles()).toEqual(['Tha', 'Xtal', 'Ageispolis']);
  });
});

describe('sorting by column', () => {
  it('sorts by title, and flips on a second click', () => {
    renderTable();
    const th = document.querySelector('[data-sort-field="title"]') as HTMLElement;

    fireEvent.click(th);
    expect(titles()).toEqual(['Tha', 'Xtal', 'Ageispolis']);
    fireEvent.click(th);
    expect(titles()).toEqual(['Xtal', 'Tha', 'Ageispolis']);
  });

  it('sorts numerically, not as strings', () => {
    // '1000' vs '192' as text would put 1000 first ascending; as numbers it is
    // second.
    renderTable();
    fireEvent.click(document.querySelector('[data-sort-field="bitrate"]') as HTMLElement);
    expect(titles()).toEqual(['Tha', 'Xtal', 'Ageispolis']);
  });

  it('sorts by duration', () => {
    renderTable();
    fireEvent.click(document.querySelector('[data-sort-field="duration"]') as HTMLElement);
    // Xtal is 5:00, Tha is 9:30.
    expect(titles()).toEqual(['Xtal', 'Tha', 'Ageispolis']);
  });

  it('sorts by the derived FORMAT, not by the raw path', () => {
    renderTable();
    fireEvent.click(document.querySelector('[data-sort-field="format"]') as HTMLElement);
    // FLAC before MP3 — from extractFormat, not from '01 Xtal.flac'.
    expect(titles()).toEqual(['Xtal', 'Tha', 'Ageispolis']);
  });

  it('SINKS a track with no value for that column, in both directions', () => {
    // Only Xtal has a BPM; an unset one must not float to the top ascending.
    renderTable();
    const th = document.querySelector('[data-sort-field="bpm"]') as HTMLElement;

    fireEvent.click(th);
    expect(titles()[0]).toBe('Xtal');
    fireEvent.click(th);
    expect(titles()[0]).toBe('Xtal');
  });

  it('always sinks MISSING rows, whichever way the sort runs', () => {
    // A row you do not own has no bitrate, no format and no file — sorting it
    // among real tracks would be sorting on absence.
    renderTable();
    const th = document.querySelector('[data-sort-field="title"]') as HTMLElement;

    fireEvent.click(th);
    expect(titles().at(-1)).toBe('Ageispolis');
    fireEvent.click(th);
    expect(titles().at(-1)).toBe('Ageispolis');
  });

  it('leaves the default order alone until a header is clicked', () => {
    renderTable();
    expect(titles()).toEqual(['Xtal', 'Tha', 'Ageispolis']);
  });

  it('re-sorts when the column changes', () => {
    renderTable();
    fireEvent.click(document.querySelector('[data-sort-field="title"]') as HTMLElement);
    expect(titles()).toEqual(['Tha', 'Xtal', 'Ageispolis']);

    fireEvent.click(document.querySelector('[data-sort-field="track_number"]') as HTMLElement);
    expect(titles()).toEqual(['Xtal', 'Tha', 'Ageispolis']);
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

  it('shows an opus average with a VBR tilde', () => {
    renderTable({
      id: 7,
      tracks: [
        {
          id: 3,
          track_number: 3,
          title: 'Autumnal Embrace',
          bitrate: 160,
          file_path: '/music/Skyforest/Autumnal Embrace.opus',
        },
      ],
    });
    const cell = rows()[0].querySelector('.enhanced-bitrate');
    expect(cell?.textContent).toBe('~160 kbps');
    expect(cell?.getAttribute('title')).toBe('Average bitrate (VBR)');
  });

  it('shows an aac average with a VBR tilde', () => {
    renderTable({
      id: 7,
      tracks: [
        {
          id: 4,
          track_number: 4,
          title: 'Voice of the Sea',
          bitrate: 256,
          file_path: '/music/Skyforest/Voice of the Sea.m4a',
        },
      ],
    });
    const cell = rows()[0].querySelector('.enhanced-bitrate');
    expect(cell?.textContent).toBe('~256 kbps');
    expect(cell?.getAttribute('title')).toBe('Average bitrate (VBR)');
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
    // Write-tags is no longer a window bridge: ✎ opens the local tag preview
    // modal (showTagPreview's port), which fetches the diff on mount.
    const fetchSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: true, diff: [], has_changes: false })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    renderTable();
    click('.enhanced-write-tag-btn');
    expect(screen.getByText('Write Tags to File')).toBeTruthy();
    expect(String(fetchSpy.mock.calls[0]?.[0])).toBe('/api/library/track/1/tag-preview');
    vi.unstubAllGlobals();

    // Redownload is local too: ↻ mounts the 3-step modal, which searches
    // metadata sources the moment it opens.
    const redlSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: true, metadata_results: {} })),
    );
    vi.stubGlobal('fetch', redlSpy);
    click('.enhanced-redownload-btn');
    expect(screen.getByText('Redownload Track')).toBeTruthy();
    expect(String(redlSpy.mock.calls[0]?.[0])).toBe(
      '/api/library/track/1/redownload/search-metadata',
    );
    vi.unstubAllGlobals();

    // Delete is no longer a window bridge: it opens the local two-option
    // dialog (deleteLibraryTrack's port). The full flow has its own test.
    click('.enhanced-delete-btn');
    expect(screen.getByText('Delete Track')).toBeTruthy();
  });

  it('deleting via the dialog fires the request and drops the row from state', async () => {
    const fetchSpy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: true, file_deleted: true })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    window.showToast = vi.fn() as never;
    try {
      const { onTrackDeleted } = renderTable();
      click('.enhanced-delete-btn');
      screen.getByText('Delete File Too').click();
      await waitFor(() => expect(onTrackDeleted).toHaveBeenCalledWith(1));
      expect(String(fetchSpy.mock.calls[0][0])).toContain('/api/library/track/1');
      expect(String(fetchSpy.mock.calls[0][0])).toContain('delete_file=true');
      expect(window.showToast).toHaveBeenCalledWith(
        'Track deleted from library and disk',
        'success',
      );
    } finally {
      vi.unstubAllGlobals();
      delete window.showToast;
    }
  });

  it('runs ReplayGain locally on the row and anchors source info to its button', async () => {
    // Both were window bridges taking the button element; both are local now.
    const fetchSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ success: true, track_gain: '-1.20 dB', lufs: -9.5 })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    window.showToast = vi.fn() as never;
    try {
      renderTable();
      const rg = rows()[0].querySelector('.enhanced-rg-btn') as HTMLElement;
      fireEvent.click(rg);
      expect(rg.textContent).toBe('…');
      await waitFor(() =>
        expect(window.showToast).toHaveBeenCalledWith(
          'ReplayGain written: -1.20 dB (-9.5 LUFS)',
          'success',
        ),
      );
      expect(String(fetchSpy.mock.calls[0]?.[0])).toBe('/api/library/track/1/analyze-replaygain');
      await waitFor(() => expect(rg.textContent).toBe('RG'));

      const info = rows()[0].querySelector('.enhanced-source-info-btn') as HTMLElement;
      fireEvent.click(info);
      expect(document.querySelector('#source-info-popover')).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
      delete window.showToast;
    }
  });

  it('re-identifies with the album art and title for context', async () => {
    // Local now (#889 port): ⇄ mounts the modal seeded with the row's filing,
    // which loads the source tabs the moment it opens.
    const fetchSpy = vi.fn(
      async (_i: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ sources: [] })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    try {
      renderTable({ ...ALBUM, title: 'SAW 85-92', thumb_url: 'cover.jpg' });
      click('.enhanced-reidentify-btn');
      expect(document.getElementById('reid-hero-title')?.textContent).toBe('Xtal');
      expect(document.getElementById('reid-hero-sub')?.textContent).toBe(
        'Aphex Twin · currently in “SAW 85-92”',
      );
      await waitFor(() =>
        expect(String(fetchSpy.mock.calls[0]?.[0])).toBe('/api/reidentify/sources'),
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('manages a missing track from either role', () => {
    // No longer a window bridge: Manage opens the local two-option chooser
    // seeded with the missing slot's context.
    renderTable();
    click('.enhanced-missing-manage-btn', 2);
    expect(screen.getByText('Manage Missing Track')).toBeTruthy();
    expect(screen.getByText('Add to Library')).toBeTruthy();
    expect(screen.getByText('I Have This')).toBeTruthy();
    cleanup();

    renderTable(ALBUM, false);
    click('.enhanced-missing-manage-btn', 2);
    expect(screen.getByText('Manage Missing Track')).toBeTruthy();
  });

  it('reports a track issue with its ALBUM name too', () => {
    renderTable(ALBUM, false);
    click('.enhanced-track-report-btn');
    expect(window.showReportIssueModal).toHaveBeenCalledWith('track', 1, 'Xtal', 'Aphex Twin', '');
  });

  it('opens the mobile action popover', () => {
    // Local now: the sheet lists the row's own actions.
    renderTable();
    click('.enhanced-mobile-actions-btn');
    const popover = document.querySelector('.enhanced-mobile-actions-popover');
    expect(popover).toBeTruthy();
    expect(popover?.querySelector('.popover-title')?.textContent).toBe('Xtal');
    expect(popover?.querySelector('.popover-delete')?.textContent).toContain('Delete Track');
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
          onTrackDeleted={vi.fn()}
          onAlbumPatched={vi.fn()}
          onReload={vi.fn()}
        />
      </div>,
    );
    fireEvent.click(document.querySelector('.enhanced-delete-btn') as HTMLElement);
    expect(onPanelClick).not.toHaveBeenCalled();
  });
});

describe('re-matching a track', () => {
  /** The chip now mounts the LOCAL match modal; the default query lands in
      its search input (trackMatchQuery drives it, pinned per service). The
      modal auto-searches on open, so fetch gets stubbed. */
  function openChip(album = ALBUM, chipIndex: number | 'last' = 1) {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_i: RequestInfo | URL, _init?: RequestInit) =>
          new Response(JSON.stringify({ success: true, results: [] })),
      ),
    );
    renderTable(album);
    const chips = rows()[0].querySelectorAll('.enhanced-track-match-chip');
    fireEvent.click(chips[chipIndex === 'last' ? chips.length - 1 : chipIndex]);
    return document.querySelector('.enhanced-match-search-input') as HTMLInputElement;
  }

  afterEach(() => vi.unstubAllGlobals());

  it('opens the matcher for the clicked service, with the BARE track title', () => {
    // The album has a title here on purpose: every service except Bandcamp
    // searched better on the title alone.
    const input = openChip({ ...ALBUM, title: 'SAW 85-92' }, 1);
    expect(screen.getByText('Match track on MusicBrainz')).toBeTruthy();
    expect(input.value).toBe('Xtal');
  });

  it('does not leave a leading space when the album is untitled', () => {
    const input = openChip(ALBUM, 'last');
    expect(input.value).toBe('Xtal');
  });

  it('sends the ALBUM name alongside the title for Bandcamp only', () => {
    // Bandcamp searches release pages, where a bare track title is ambiguous
    // across compilations, remixes and covers.
    const input = openChip({ ...ALBUM, title: 'SAW 85-92' }, 'last');
    expect(screen.getByText('Match track on Bandcamp')).toBeTruthy();
    expect(input.value).toBe('SAW 85-92 Xtal');
  });

  it('is inert for a non-admin', () => {
    renderTable(ALBUM, false);
    fireEvent.click(rows()[0].querySelectorAll('.enhanced-track-match-chip')[1]);
    expect(document.querySelector('.enhanced-match-search-input')).toBeNull();
  });
});
