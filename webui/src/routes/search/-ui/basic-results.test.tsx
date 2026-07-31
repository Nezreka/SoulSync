import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { BasicAlbum, BasicResult, BasicTrack } from '../-basic.types';
import type { BasicResultActions } from './basic-results';

import { BasicResults } from './basic-results';

afterEach(cleanup);

function track(over: Partial<BasicTrack> = {}): BasicTrack {
  return {
    result_type: 'track',
    username: 'peer',
    filename: 'a.flac',
    size: 10 * 1024 * 1024,
    bitrate: 320,
    duration: 200_000,
    quality: 'flac',
    free_upload_slots: 1,
    upload_speed: 1_000_000,
    queue_length: 0,
    sample_rate: 44_100,
    bit_depth: 16,
    artist: 'Aphex Twin',
    title: 'Xtal',
    album: 'SAW',
    track_number: 1,
    quality_score: 0.9,
    ...over,
  };
}

function album(over: Partial<BasicAlbum> = {}): BasicAlbum {
  return {
    result_type: 'album',
    username: 'peer',
    album_path: '/music/saw',
    album_title: 'Selected Ambient Works',
    artist: 'Aphex Twin',
    track_count: 2,
    total_size: 80 * 1024 * 1024,
    tracks: [track(), track({ title: 'Tha', track_number: 2, filename: 'b.flac' })],
    dominant_quality: 'flac',
    year: '1992',
    free_upload_slots: 1,
    upload_speed: 1_000_000,
    queue_length: 0,
    quality_score: 0.8,
    ...over,
  };
}

function noopActions(): BasicResultActions {
  return {
    onDownloadTrack: vi.fn(),
    onStreamTrack: vi.fn(),
    onMatchedTrack: vi.fn(),
    onDownloadAlbum: vi.fn(),
    onMatchedAlbum: vi.fn(),
    onDownloadAlbumTrack: vi.fn(),
    onStreamAlbumTrack: vi.fn(),
    onMatchedAlbumTrack: vi.fn(),
  };
}

function renderResults(results: BasicResult[] = [track()], actions = noopActions()) {
  const view = render(
    <BasicResults results={results} actions={actions} placeholder="Nothing here." />,
  );
  return { ...view, actions };
}

describe('empty state', () => {
  it('renders the placeholder it is given, not a hardcoded one', () => {
    // Two different sentences in the vanilla — "Enter a search term to get
    // started." before any search, "No search results found." after a failed
    // one. Accusing a fresh page of a failed search is the bug this prevents.
    const { container } = render(
      <BasicResults results={[]} actions={noopActions()} placeholder="Enter a search term." />,
    );
    expect(container.querySelector('.search-results-placeholder p')?.textContent).toBe(
      'Enter a search term.',
    );
  });

  it('keeps the results container id even when empty', () => {
    // helper.js's tour targets #search-results-area; losing it on the empty
    // state would break the tour step for exactly the users being shown around.
    const { container } = render(
      <BasicResults results={[]} actions={noopActions()} placeholder="x" />,
    );
    expect(container.querySelector('#search-results-area')).not.toBeNull();
  });
});

describe('track cards', () => {
  it('renders the title, artist, size, quality and bitrate', () => {
    const { container } = renderResults([track()]);
    const card = container.querySelector('.track-result-card') as HTMLElement;
    expect(within(card).getByText('Xtal')).toBeTruthy();
    expect(within(card).getByText('by Aphex Twin')).toBeTruthy();
    expect(card.querySelector('.track-details')?.textContent).toContain('10.0 MB');
    expect(card.querySelector('.track-details')?.textContent).toContain('flac');
    expect(card.querySelector('.track-details')?.textContent).toContain('320kbps');
  });

  it('falls back for a result with no title or artist', () => {
    const { container } = renderResults([track({ title: null, artist: null })]);
    expect(container.querySelector('.track-title')?.textContent).toBe('Unknown Title');
    expect(container.querySelector('.track-artist')?.textContent).toBe('by Unknown Artist');
  });

  it('omits the bitrate rather than printing a zero', () => {
    const { container } = renderResults([track({ bitrate: null })]);
    expect(container.querySelector('.track-details')?.textContent).not.toContain('kbps');
  });

  it('wires the three actions to the rendered index', () => {
    const actions = noopActions();
    const { container } = renderResults(
      [track({ title: 'first' }), track({ title: 'second' })],
      actions,
    );
    const second = container.querySelectorAll('.track-result-card')[1];

    fireEvent.click(second.querySelector('.track-download-btn') as HTMLElement);
    fireEvent.click(second.querySelector('.track-stream-btn') as HTMLElement);
    fireEvent.click(second.querySelector('.track-matched-btn') as HTMLElement);

    expect(actions.onDownloadTrack).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'second' }),
      1,
    );
    expect(actions.onStreamTrack).toHaveBeenCalledWith(expect.anything(), 1);
    expect(actions.onMatchedTrack).toHaveBeenCalledWith(expect.anything(), 1);
  });

  it('renders the uploader as chat.js"s delegated message button', () => {
    // chat.js binds ONE capture-phase listener on document for
    // [data-chat-msg-user]; the attribute is the whole contract.
    const { container } = renderResults([track({ username: 'somepeer' })]);
    const button = container.querySelector('.track-uploader .chat-user-link') as HTMLElement;
    expect(button.getAttribute('data-chat-msg-user')).toBe('somepeer');
    expect(button.textContent).toBe('somepeer');
    expect(button.getAttribute('title')).toBe('Message this user on Soulseek');
  });

  it('shows Unknown for a missing uploader but sends no name to chat', () => {
    const { container } = renderResults([track({ username: '' })]);
    const button = container.querySelector('.chat-user-link') as HTMLElement;
    expect(button.textContent).toBe('Unknown');
    expect(button.getAttribute('data-chat-msg-user')).toBe('');
  });
});

describe('album cards', () => {
  it('renders the header with the track count, size and dominant quality', () => {
    // The vanilla read `result.quality` here, which an album never carries, so
    // every album — FLAC included — was labelled "Mixed".
    const { container } = renderResults([album()]);
    expect(container.querySelector('.album-title')?.textContent).toBe('Selected Ambient Works');
    const details = container.querySelector('.album-details')?.textContent ?? '';
    expect(details).toContain('2 tracks');
    expect(details).toContain('80.0 MB');
    expect(details).toContain('flac');
    expect(details).not.toContain('Mixed');
  });

  it('starts collapsed and expands on a header click', () => {
    const { container } = renderResults([album()]);
    const list = container.querySelector('.album-track-list') as HTMLElement;
    const indicator = container.querySelector('.album-expand-indicator') as HTMLElement;

    expect(list.style.display).toBe('none');
    expect(indicator.textContent).toBe('▶');

    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);

    expect(list.style.display).toBe('block');
    expect(indicator.textContent).toBe('▼');
    expect(container.querySelector('.album-result-card')?.className).toContain('expanded');
  });

  it('collapses again on a second click', () => {
    const { container } = renderResults([album()]);
    const header = container.querySelector('.album-card-header') as HTMLElement;
    fireEvent.click(header);
    fireEvent.click(header);
    expect((container.querySelector('.album-track-list') as HTMLElement).style.display).toBe(
      'none',
    );
  });

  it('expands each album independently', () => {
    const { container } = renderResults([album({ album_title: 'A' }), album({ album_title: 'B' })]);
    fireEvent.click(container.querySelectorAll('.album-card-header')[1] as HTMLElement);
    const lists = container.querySelectorAll('.album-track-list');
    expect((lists[0] as HTMLElement).style.display).toBe('none');
    expect((lists[1] as HTMLElement).style.display).toBe('block');
  });

  it('does not toggle when an action button inside the header is clicked', () => {
    // The download buttons live INSIDE the clickable header; without
    // stopPropagation every download would also expand the folder.
    const actions = noopActions();
    const { container } = renderResults([album()], actions);
    fireEvent.click(container.querySelector('.album-download-btn') as HTMLElement);

    expect(actions.onDownloadAlbum).toHaveBeenCalled();
    expect((container.querySelector('.album-track-list') as HTMLElement).style.display).toBe(
      'none',
    );
  });

  it('collapses everything when the result list changes', () => {
    // Index 2 in a new result set is a different album, so a retained
    // expansion opens a folder the user never clicked.
    const { container, rerender } = renderResults([album()]);
    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);
    expect((container.querySelector('.album-track-list') as HTMLElement).style.display).toBe(
      'block',
    );

    rerender(
      <BasicResults
        results={[album({ album_title: 'Other' })]}
        actions={noopActions()}
        placeholder="x"
      />,
    );

    expect((container.querySelector('.album-track-list') as HTMLElement).style.display).toBe(
      'none',
    );
  });

  it('wires the album actions', () => {
    const actions = noopActions();
    const { container } = renderResults([track(), album()], actions);
    fireEvent.click(container.querySelector('.album-matched-btn') as HTMLElement);
    // Index 1 — the album's position in the RENDERED list, which is what
    // window.currentSearchResults publishes.
    expect(actions.onMatchedAlbum).toHaveBeenCalledWith(expect.anything(), 1);
  });
});

describe('album track rows', () => {
  it('renders each track with its number, artist, size and quality', () => {
    const { container } = renderResults([album()]);
    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);

    const rows = container.querySelectorAll('.track-item');
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector('.track-item-title')?.textContent).toBe('Xtal');
    expect(rows[0].querySelector('.track-item-details')?.textContent).toContain('1. ');
    expect(rows[0].querySelector('.track-item-details')?.textContent).toContain('Aphex Twin');
  });

  it('names an untitled track by its position', () => {
    const { container } = renderResults([album({ tracks: [track({ title: null })] })]);
    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);
    expect(container.querySelector('.track-item-title')?.textContent).toBe('Track 1');
  });

  it("falls back to the album's artist for a track that has none", () => {
    const { container } = renderResults([
      album({ artist: 'Album Artist', tracks: [track({ artist: null })] }),
    ]);
    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);
    expect(container.querySelector('.track-item-details')?.textContent).toContain('Album Artist');
  });

  it('passes both indices to the track actions', () => {
    const actions = noopActions();
    const { container } = renderResults([album()], actions);
    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);

    const second = container.querySelectorAll('.track-item')[1];
    fireEvent.click(second.querySelector('.track-download-btn') as HTMLElement);
    fireEvent.click(second.querySelector('.track-stream-btn') as HTMLElement);
    fireEvent.click(second.querySelector('.track-matched-btn') as HTMLElement);

    expect(actions.onDownloadAlbumTrack).toHaveBeenCalledWith(expect.anything(), 0, 1);
    expect(actions.onStreamAlbumTrack).toHaveBeenCalledWith(expect.anything(), 0, 1);
    expect(actions.onMatchedAlbumTrack).toHaveBeenCalledWith(expect.anything(), 0, 1);
  });
});

describe('disc separators', () => {
  it('renders none for a single-disc album', () => {
    const { container } = renderResults([album()]);
    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);
    expect(container.querySelectorAll('.disc-separator')).toHaveLength(0);
  });

  it('labels every disc when the track numbers reset', () => {
    const tracks = [1, 2, 1, 2].map((n, i) =>
      track({ track_number: n, filename: `t${i}.flac`, title: `T${i}` }),
    );
    const { container } = renderResults([album({ tracks })]);
    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);

    const separators = [...container.querySelectorAll('.disc-separator')].map((n) => n.textContent);
    expect(separators).toEqual(['Disc 1', 'Disc 2']);
  });

  it('numbers a third disc correctly', () => {
    const tracks = [1, 2, 1, 1].map((n, i) => track({ track_number: n, filename: `t${i}.flac` }));
    const { container } = renderResults([album({ tracks })]);
    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);

    const separators = [...container.querySelectorAll('.disc-separator')].map((n) => n.textContent);
    expect(separators).toEqual(['Disc 1', 'Disc 2', 'Disc 3']);
  });

  it('carries the inline styling that is its only appearance', () => {
    // `.disc-separator` has no stylesheet rule; drop the inline styles and the
    // separators render as plain unstyled text.
    const tracks = [1, 1].map((n, i) => track({ track_number: n, filename: `t${i}.flac` }));
    const { container } = renderResults([album({ tracks })]);
    fireEvent.click(container.querySelector('.album-card-header') as HTMLElement);

    const first = container.querySelector('.disc-separator') as HTMLElement;
    expect(first.style.fontWeight).toBe('600');
    expect(first.style.borderBottom).toBeTruthy();
  });
});
