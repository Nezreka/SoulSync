import { cleanup, fireEvent, render } from '@testing-library/react';
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
});

describe('EnhancedView states', () => {
  it('shows a loading line while the request is in flight', () => {
    render(<EnhancedView isAdmin={false} data={null} status={{ loading: true, error: '' }} />);
    expect(document.querySelector('.enhanced-loading')?.textContent).toBe(
      'Loading library data...',
    );
  });

  it('shows the failure instead of an empty view', () => {
    render(<EnhancedView isAdmin={false} data={null} status={{ loading: false, error: 'boom' }} />);
    expect(document.querySelector('.enhanced-loading')?.textContent).toBe('Failed to load: boom');
  });
});

describe('the stats bar', () => {
  it('lists the five stats in order', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
    const labels = [...document.querySelectorAll('.enhanced-stat-label')].map((n) => n.textContent);
    expect(labels).toEqual(['Albums', 'EPs', 'Singles', 'Tracks', 'Duration']);
  });

  it('badges each format with its count, commonest first', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
    const badges = [...document.querySelectorAll('.enhanced-stats-formats .enhanced-format-badge')];
    expect(badges.map((n) => n.textContent)).toEqual(['FLAC (2)', 'MP3 (1)']);
    expect(badges[0].className).toContain('flac');
  });
});

describe('sections', () => {
  it('renders only the buckets that have albums', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
    const titles = [...document.querySelectorAll('.enhanced-section-title')].map(
      (n) => n.textContent,
    );
    // Singles is omitted rather than shown as an empty header.
    expect(titles).toEqual(['Albums', 'EPs']);
  });

  it('never renders a bucket outside album/ep/single', () => {
    // groupAlbumsByType creates a bucket for any record_type; the view ignores
    // it, exactly as the vanilla did.
    render(
      <EnhancedView
        isAdmin={false}
        data={{ albums: [{ id: 9, title: 'Live At', record_type: 'live', tracks: [] }] }}
        status={READY}
      />,
    );
    expect(document.querySelector('.enhanced-section')).toBeNull();
  });

  it('counts releases and tracks in the section header', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
    const counts = [...document.querySelectorAll('.enhanced-section-count')].map(
      (n) => n.textContent,
    );
    expect(counts[0]).toBe('1 release · 2 tracks');
  });
});

describe('album rows', () => {
  it('shows the title, meta line, type badge and format badge', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
    const row = document.getElementById('enhanced-album-row-1') as HTMLElement;
    expect(row.querySelector('.enhanced-album-title')?.textContent).toBe('SAW 85-92');
    expect(row.querySelector('.enhanced-album-meta-line')?.textContent).toBe(
      '1992 · 2 tracks · 5:00 · Apollo',
    );
    expect(row.querySelector('.enhanced-album-type-badge')?.textContent).toBe('album');
    expect(row.querySelector('.enhanced-format-badge')?.textContent).toBe('FLAC');
  });

  it('falls back to the music note when the album has no art', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
    const row = document.getElementById('enhanced-album-row-2') as HTMLElement;
    expect(row.querySelector('.enhanced-album-thumb')).toBeNull();
    expect(row.querySelector('.enhanced-album-thumb-fallback')).not.toBeNull();
  });

  it('swaps a BROKEN thumbnail for the fallback', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
    const img = document.querySelector('.enhanced-album-thumb') as HTMLImageElement;
    fireEvent.error(img);
    const row = document.getElementById('enhanced-album-row-1') as HTMLElement;
    expect(row.querySelector('.enhanced-album-thumb')).toBeNull();
    expect(row.querySelector('.enhanced-album-thumb-fallback')).not.toBeNull();
  });

  it('titles an untitled album "Unknown"', () => {
    render(
      <EnhancedView
        isAdmin={false}
        data={{ albums: [{ id: 3, title: '', tracks: [] }] }}
        status={READY}
      />,
    );
    expect(document.querySelector('.enhanced-album-title')?.textContent).toBe('Unknown');
  });
});

describe('expanding an album', () => {
  it('starts collapsed', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
    expect(document.getElementById('enhanced-album-wrapper-1')?.className).not.toContain(
      'expanded',
    );
    expect(document.getElementById('enhanced-tracks-panel-1')?.className).not.toContain('visible');
  });

  it('marks the row, wrapper and panel on click, and unmarks on a second', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
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
    render(<EnhancedView isAdmin data={DATA} status={READY} />);
    expect(document.querySelector('.enhanced-expanded-header')).toBeNull();

    fireEvent.click(document.getElementById('enhanced-album-row-1') as HTMLElement);
    expect(document.querySelectorAll('.enhanced-expanded-header')).toHaveLength(1);
  });

  it('expands each album independently', () => {
    render(<EnhancedView isAdmin={false} data={DATA} status={READY} />);
    fireEvent.click(document.getElementById('enhanced-album-row-1') as HTMLElement);
    expect(document.getElementById('enhanced-tracks-panel-2')?.className).not.toContain('visible');
  });
});
