import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { EnhancedView } from './enhanced-view';

const READY = { loading: false, error: '' };

const DATA = {
  albums: [
    {
      id: 1,
      title: 'SAW 85-92',
      record_type: 'album',
      year: 1992,
      label: 'Apollo',
      thumb_url: 'a.jpg',
      tracks: [{ duration: 300_000, file_path: 'a.flac' }, { file_path: 'b.flac' }],
    },
    { id: 2, title: 'Digeridoo', record_type: 'ep', tracks: [{ file_path: 'c.mp3' }] },
  ],
};

afterEach(() => {
  // NOT document.body.innerHTML = '': anything rendered through BodyPortal
  // lives there, and wiping the body out from under Testing Library's cleanup
  // makes it throw "The node to be removed is not a child of this node".
  cleanup();
  delete window.playTrackList;
});

describe('EnhancedView states', () => {
  it('shows a loading line while the request is in flight', () => {
    render(
      <EnhancedView
        isAdmin={false}
        onReload={vi.fn()}
        data={null}
        status={{ loading: true, error: '' }}
      />,
    );
    expect(document.querySelector('.enhanced-loading')?.textContent).toBe(
      'Loading library data...',
    );
  });

  it('shows the failure instead of an empty view', () => {
    render(
      <EnhancedView
        isAdmin={false}
        onReload={vi.fn()}
        data={null}
        status={{ loading: false, error: 'boom' }}
      />,
    );
    expect(document.querySelector('.enhanced-loading')?.textContent).toBe('Failed to load: boom');
  });
});

describe('the library summary on the artist card', () => {
  it('reads the stats as one sentence, skipping empty buckets', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    // no singles in the fixture, so that word never appears
    expect(document.querySelector('.lib-artist-stats')?.textContent).toBe(
      '1 album·1 EP·3 tracks·5m',
    );
  });

  it('badges each format with its count, commonest first, under a lossless bar', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    const badges = [...document.querySelectorAll('.enhanced-stats-formats .enhanced-format-badge')];
    expect(badges.map((n) => n.textContent)).toEqual(['FLAC 2', 'MP3 1']);
    expect(badges[0].className).toContain('flac');
    expect(document.querySelector('.lib-quality-headline')?.textContent).toBe('67% lossless');
    expect((document.querySelector('.lib-quality-seg') as HTMLElement).style.width).toBe('67%');
  });
});

describe('sections', () => {
  it('renders only the buckets that have albums', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    const titles = [...document.querySelectorAll('.enhanced-section-title')].map(
      (n) => n.textContent,
    );
    // Singles is omitted rather than shown as an empty header.
    expect(titles).toEqual(['Albums', 'EPs']);
  });

  it('renders a bucket outside album/ep/single instead of dropping it', () => {
    // Was pinned the other way: the view walked a fixed album/ep/single list,
    // so anything else was fetched, grouped and then never shown. Compilations
    // are the common case and there was no way to see them at all
    // (TheHomeGuy, Aug 2026).
    render(
      <EnhancedView
        onReload={vi.fn()}
        isAdmin={false}
        data={{ albums: [{ id: 9, title: 'Live At', record_type: 'live', tracks: [] }] }}
        status={READY}
      />,
    );
    expect(document.querySelector('.enhanced-section')).not.toBeNull();
    expect(document.body.textContent).toContain('Live');
  });

  it('gives compilations their own named section', () => {
    render(
      <EnhancedView
        onReload={vi.fn()}
        isAdmin={false}
        data={{
          albums: [{ id: 10, title: 'Greatest Hits', record_type: 'compilation', tracks: [] }],
        }}
        status={READY}
      />,
    );
    expect(document.body.textContent).toContain('Compilations');
  });

  it('counts releases and tracks in the section header', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    const counts = [...document.querySelectorAll('.enhanced-section-count')].map(
      (n) => n.textContent,
    );
    expect(counts[0]).toBe('1 release · 2 tracks');
  });
});

describe('album rows', () => {
  it('shows the title, meta line and format badge', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    const row = document.getElementById('enhanced-album-row-1') as HTMLElement;
    expect(row.querySelector('.enhanced-album-title')?.textContent).toBe('SAW 85-92');
    expect(row.querySelector('.enhanced-album-meta-line')?.textContent).toBe(
      '1992 · 2 tracks · 5:00 · Apollo',
    );
    // the section heading already says what type it is
    expect(row.querySelector('.enhanced-album-type-badge')).toBeNull();
    expect(row.querySelector('.enhanced-format-badge')?.textContent).toBe('FLAC');
    expect(row.querySelector('.lib-pill.warn')).toBeNull();
  });

  it('flags the gap when the source counts more tracks than are owned', () => {
    render(
      <EnhancedView
        isAdmin={false}
        onReload={vi.fn()}
        data={{ albums: [{ id: 4, title: 'Short', api_track_count: 12, tracks: [{ id: 1 }] }] }}
        status={READY}
      />,
    );
    expect(document.querySelector('.lib-pill.warn')?.textContent).toBe('11 missing');
  });

  it('falls back to the music note when the album has no art', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    const row = document.getElementById('enhanced-album-row-2') as HTMLElement;
    expect(row.querySelector('.enhanced-album-thumb')).toBeNull();
    expect(row.querySelector('.enhanced-album-thumb-fallback')).not.toBeNull();
  });

  it('swaps a BROKEN thumbnail for the fallback', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    const img = document.querySelector('.enhanced-album-thumb') as HTMLImageElement;
    fireEvent.error(img);
    const row = document.getElementById('enhanced-album-row-1') as HTMLElement;
    expect(row.querySelector('.enhanced-album-thumb')).toBeNull();
    expect(row.querySelector('.enhanced-album-thumb-fallback')).not.toBeNull();
  });

  it('titles an untitled album "Unknown"', () => {
    render(
      <EnhancedView
        onReload={vi.fn()}
        isAdmin={false}
        data={{ albums: [{ id: 3, title: '', tracks: [] }] }}
        status={READY}
      />,
    );
    expect(document.querySelector('.enhanced-album-title')?.textContent).toBe('Unknown');
  });

  it('plays every album row in disc and track order without expanding it', () => {
    window.playTrackList = vi.fn();
    render(
      <EnhancedView
        onReload={vi.fn()}
        isAdmin={false}
        data={{
          artist: { id: 42, name: 'Aphex Twin', thumb_url: 'artist.jpg' },
          albums: [
            {
              id: 1,
              title: 'SAW 85-92',
              thumb_url: 'album.jpg',
              tracks: [
                { id: 2, title: 'Tha', disc_number: 1, track_number: 2, file_path: 'tha.flac' },
                { id: 1, title: 'Xtal', disc_number: 1, track_number: 1, file_path: 'xtal.flac' },
              ],
            },
          ],
        }}
        status={READY}
      />,
    );

    const row = document.getElementById('enhanced-album-row-1') as HTMLElement;
    fireEvent.click(row.querySelector('.enhanced-album-play-btn')!);

    expect(window.playTrackList).toHaveBeenCalledWith(
      [
        expect.objectContaining({ title: 'Xtal', file_path: 'xtal.flac', artist: 'Aphex Twin' }),
        expect.objectContaining({ title: 'Tha', file_path: 'tha.flac', artist: 'Aphex Twin' }),
      ],
      'SAW 85-92',
    );
    expect(row.className).not.toContain('expanded');
  });
});

describe('expanding an album', () => {
  it('starts collapsed', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    expect(document.getElementById('enhanced-album-wrapper-1')?.className).not.toContain(
      'expanded',
    );
    expect(document.getElementById('enhanced-tracks-panel-1')?.className).not.toContain('visible');
  });

  it('marks the row, wrapper and panel on click, and unmarks on a second', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    const row = document.getElementById('enhanced-album-row-1') as HTMLElement;

    fireEvent.click(row);
    expect(row.className).toContain('expanded');
    expect(document.getElementById('enhanced-album-wrapper-1')?.className).toContain('expanded');
    expect(document.getElementById('enhanced-tracks-panel-1')?.className).toContain('visible');

    fireEvent.click(row);
    expect(row.className).not.toContain('expanded');
    expect(document.getElementById('enhanced-tracks-panel-1')?.className).not.toContain('visible');
  });

  it('does not render the panel body until the album is expanded', () => {
    // The vanilla's lazy render: a large library can have hundreds of albums,
    // and each panel is a full header plus track table.
    render(<EnhancedView isAdmin onReload={vi.fn()} data={DATA} status={READY} />);
    expect(document.querySelector('.enhanced-expanded-header')).toBeNull();

    fireEvent.click(document.getElementById('enhanced-album-row-1') as HTMLElement);
    expect(document.querySelectorAll('.enhanced-expanded-header')).toHaveLength(1);
  });

  it('expands each album independently', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    fireEvent.click(document.getElementById('enhanced-album-row-1') as HTMLElement);
    expect(document.getElementById('enhanced-tracks-panel-2')?.className).not.toContain('visible');
  });
});

describe('the album metadata row', () => {
  // the integration the component tests could not see: who gets the row at
  // all. gating it on the admin's "edit details" toggle silently took release
  // date, style, mood and explicit away from everyone else.
  const open = () => fireEvent.click(document.getElementById('enhanced-album-row-1')!);

  it('is read-only and always shown for a non-admin', () => {
    render(<EnhancedView isAdmin={false} onReload={vi.fn()} data={DATA} status={READY} />);
    open();
    expect(document.querySelector('.enhanced-album-meta-row')).not.toBeNull();
    expect(document.querySelector('.enhanced-album-meta-input')).toBeNull();
    const labels = [...document.querySelectorAll('.enhanced-album-meta-label')].map(
      (n) => n.textContent,
    );
    expect(labels).toEqual([
      'Title',
      'Year',
      'Release Date',
      'Genres',
      'Label',
      'Style',
      'Mood',
      'Type',
      'Explicit',
    ]);
  });

  it('is hidden for an admin until Edit details, then editable', () => {
    render(<EnhancedView isAdmin onReload={vi.fn()} data={DATA} status={READY} />);
    open();
    expect(document.querySelector('.enhanced-album-meta-row')).toBeNull();

    fireEvent.click(document.querySelector('.lib-edit-details') as HTMLElement);
    expect(document.querySelector('.enhanced-album-meta-row')).not.toBeNull();
    expect(document.querySelector('.enhanced-album-meta-input')).not.toBeNull();
  });
});

describe('the canonical tracklist on expand', () => {
  // The step the React port dropped: nothing fetched the source tracklist, so
  // no album ever grew a missing row and "I Have This" was unreachable.
  const ONE_OF_TWO = {
    albums: [
      {
        id: 7,
        title: 'Two Tracker',
        record_type: 'single',
        deezer_id: 'dz7',
        tracks: [{ id: 71, title: 'Side A', track_number: 1, file_path: 'a.flac' }],
      },
    ],
    artist: { id: 3, name: 'Someone' },
  };
  const TRACKLIST = {
    success: true,
    source: 'deezer',
    tracks: [
      { id: 't1', name: 'Side A', track_number: 1 },
      { id: 't2', name: 'Side B', track_number: 2 },
    ],
  };
  const jsonResponse = (body: unknown) =>
    Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  // The panel fires other requests on expand (the reorganize queue snapshot);
  // only the tracklist one is under test, so the stub answers the rest blank.
  const stubFetch = () => {
    const tracklist = vi.fn(() => jsonResponse(TRACKLIST));
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) =>
        String(input).includes('/tracks?') ? tracklist() : jsonResponse({ success: true }),
      ),
    );
    return tracklist;
  };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches the tracklist once the album opens and renders the gap as a missing row', async () => {
    const tracklist = stubFetch();
    render(<EnhancedView isAdmin onReload={vi.fn()} data={ONE_OF_TWO} status={READY} />);
    expect(tracklist).not.toHaveBeenCalled();

    fireEvent.click(document.getElementById('enhanced-album-row-7') as HTMLElement);
    const fetchSpy = fetch as unknown as ReturnType<typeof vi.fn>;
    const call = fetchSpy.mock.calls.find((c) => String(c[0]).includes('/tracks?'));
    const url = new URL(String(call?.[0]), 'http://x');
    expect(url.pathname).toBe('/api/album/dz7/tracks');
    expect(url.searchParams.get('source')).toBe('deezer');
    expect(url.searchParams.get('artist')).toBe('Someone');

    const missing = await waitFor(() => {
      const row = document.querySelector('.enhanced-missing-track-row');
      expect(row).not.toBeNull();
      return row as HTMLElement;
    });
    expect(missing.textContent).toContain('Side B');
    expect(missing.querySelector('.enhanced-missing-manage-btn')).not.toBeNull();
    // The owned track is matched, not doubled.
    expect(document.querySelectorAll('.enhanced-missing-track-row')).toHaveLength(1);
    expect(tracklist).toHaveBeenCalledTimes(1);
  });

  it('runs the diff again when a fresh record arrives', async () => {
    // An import hands back a refetched album with no diff on it; the row that
    // was just filled has to drop out, so the fresh record is diffed anew.
    const tracklist = stubFetch();
    const { rerender } = render(
      <EnhancedView isAdmin onReload={vi.fn()} data={ONE_OF_TWO} status={READY} />,
    );
    fireEvent.click(document.getElementById('enhanced-album-row-7') as HTMLElement);
    await waitFor(() =>
      expect(document.querySelector('.enhanced-missing-track-row')).not.toBeNull(),
    );

    const filled = {
      ...ONE_OF_TWO,
      albums: [
        {
          ...ONE_OF_TWO.albums[0],
          tracks: [
            ...ONE_OF_TWO.albums[0].tracks,
            { id: 72, title: 'Side B', track_number: 2, file_path: 'b.flac' },
          ],
        },
      ],
    };
    rerender(<EnhancedView isAdmin onReload={vi.fn()} data={filled} status={READY} />);
    await waitFor(() => expect(tracklist).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(document.querySelector('.enhanced-missing-track-row')).toBeNull());
  });

  it('does not fetch for an album with no source id', () => {
    const tracklist = stubFetch();
    render(<EnhancedView isAdmin onReload={vi.fn()} data={DATA} status={READY} />);
    fireEvent.click(document.getElementById('enhanced-album-row-1') as HTMLElement);
    expect(tracklist).not.toHaveBeenCalled();
  });
});
