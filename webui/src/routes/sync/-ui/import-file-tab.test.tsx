/**
 * The import-file tab: drop → parse → tune → preview → mirror, driven through
 * the real component with a captured fetch and real File/FileReader.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ImportFileTab } from './import-file-tab';

interface Call {
  url: string;
  method: string;
  body: unknown;
}
let calls: Call[] = [];

function stubFetch(): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return new Response(JSON.stringify({ success: true }));
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as { showToast?: unknown }).showToast;
});

/** Drive a real File through the hidden input the dropzone owns. */
function dropFile(name: string, content: string): void {
  const input = document.querySelector('#import-file-input') as HTMLInputElement;
  const file = new File([content], name, { type: 'text/plain' });
  fireEvent.change(input, { target: { files: [file] } });
}

function previewRows(): string[][] {
  const tbody = document.querySelector('#import-file-preview-tbody')!;
  return Array.from(tbody.querySelectorAll('tr')).map((tr) =>
    Array.from(tr.querySelectorAll('td')).map((td) => td.textContent ?? ''),
  );
}

describe('ImportFileTab — upload zone', () => {
  it('rejects unsupported extensions with the vanilla toast and stays on the zone (44-46)', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('cover.jpg', 'binary');
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        'Unsupported file type. Use CSV, TSV, TXT, M3U, or M3U8.',
        'error',
      ),
    );
    expect(screen.getByText('Drop your file here')).toBeInTheDocument();
  });

  it('drag-over toggles the drag-over class (19-20)', () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    const zone = document.querySelector('#import-file-dropzone')!;
    expect(zone.className).not.toContain('drag-over');
    fireEvent.dragOver(zone);
    expect(zone.className).toContain('drag-over');
    fireEvent.dragLeave(zone);
    expect(zone.className).not.toContain('drag-over');
  });
});

describe('ImportFileTab — CSV', () => {
  it('auto-maps headers, previews rows, and remapping a column re-derives the table', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('mine.csv', 'Title,Artist,Album,Length\nSong A,Band A,Rec A,3:30\nSong B,Band B,,210');

    await waitFor(() => expect(screen.getByText('mine.csv')).toBeInTheDocument());
    expect(screen.getByText('2 tracks parsed')).toBeInTheDocument();
    expect(previewRows()).toEqual([
      ['1', 'Song A', 'Band A', 'Rec A'],
      ['2', 'Song B', 'Band B', ''],
    ]);
    // Auto-map claimed Title/Artist/Album/Length; the name pre-filled from the
    // filename without its extension (328-333).
    expect(screen.getByPlaceholderText('Enter playlist name...')).toHaveValue('mine');

    // The Album column's select → Skip drops that column from the preview.
    const mapping = document.querySelector('#import-file-mapping-selects')!;
    const albumWrap = Array.from(mapping.querySelectorAll('.import-file-col-map')).find((w) =>
      w.textContent?.startsWith('Album'),
    )!;
    expect(albumWrap.className.split(' ')).toContain('mapped-album');
    fireEvent.change(within(albumWrap as HTMLElement).getByRole('combobox'), {
      target: { value: 'skip' },
    });
    expect(previewRows()).toEqual([
      ['1', 'Song A', 'Band A', ''],
      ['2', 'Song B', 'Band B', ''],
    ]);
  });

  it('a role claimed by another column moves exclusively (391-395)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('two.csv', 'Title,Artist\nS,A');
    await waitFor(() => expect(screen.getByText('two.csv')).toBeInTheDocument());

    const mapping = document.querySelector('#import-file-mapping-selects')!;
    const wraps = Array.from(mapping.querySelectorAll('.import-file-col-map'));
    // Point the Artist column at track_name — Title must lose the role.
    fireEvent.change(within(wraps[1] as HTMLElement).getByRole('combobox'), {
      target: { value: 'track_name' },
    });
    expect(previewRows()).toEqual([['1', 'A', '', '']]);
    expect(wraps[0].className.split(' ')).not.toContain('mapped-track');
  });

  it('a row whose only content sits in an unmapped column renders invalid (309, 344-346)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    // 'Notes' auto-maps to nothing, so row 2 yields empty name fields — but it
    // survives the parser's all-empty drop, so it reaches the preview.
    dropFile('holes.csv', 'Title,Artist,Notes\nGood,Band,ok\n,,orphan\n');
    await waitFor(() => expect(screen.getByText('holes.csv')).toBeInTheDocument());
    expect(screen.getByText('1 track parsed')).toBeInTheDocument();
    const rows = document.querySelectorAll('#import-file-preview-tbody tr');
    expect(rows).toHaveLength(2);
    expect(rows[0].className).toBe('');
    expect(rows[1].className).toContain('invalid-row');
  });

  it('the parser drops fully-empty rows before they can preview (importFileParseCsv 77)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('blanks.csv', 'Title,Artist\nGood,Band\n,\n');
    await waitFor(() => expect(screen.getByText('blanks.csv')).toBeInTheDocument());
    expect(previewRows()).toEqual([['1', 'Good', 'Band', '']]);
  });
});

describe('ImportFileTab — name pre-fill', () => {
  it('clear resets the name so the next file pre-fills again (412-421)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('first.csv', 'Title,Artist\nA,B');
    await waitFor(() => expect(screen.getByText('first.csv')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('Clear and start over'));
    dropFile('second.csv', 'Title,Artist\nC,D');
    await waitFor(() => expect(screen.getByText('second.csv')).toBeInTheDocument());
    expect(screen.getByPlaceholderText('Enter playlist name...')).toHaveValue('second');
  });
});

describe('ImportFileTab — a second file replaces the first', () => {
  it('a CSV followed by an M3U drops the stale headers and mapping bar (203-208)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('first.csv', 'Title,Artist\nA,B');
    await waitFor(() => expect(screen.getByText('first.csv')).toBeInTheDocument());
    expect(document.querySelector('#import-file-column-mapping')).not.toBeNull();

    fireEvent.click(screen.getByTitle('Clear and start over'));
    dropFile('second.m3u', '#EXTM3U\n#EXTINF:1,Band - Song\n/x.mp3\n');
    await waitFor(() => expect(screen.getByText('second.m3u')).toBeInTheDocument());
    expect(document.querySelector('#import-file-column-mapping')).toBeNull();
    expect(previewRows()).toEqual([['1', 'Song', 'Band', '']]);
  });

  it('a TSV parses through the delimiter detector, and quoted fields survive', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('tabs.tsv', 'Title\tArtist\tAlbum\n"Song, With Comma"\tBand A\tRec A');
    await waitFor(() => expect(screen.getByText('tabs.tsv')).toBeInTheDocument());
    expect(previewRows()).toEqual([['1', 'Song, With Comma', 'Band A', 'Rec A']]);
  });

  it('a dropped file goes through the same path as the browse input (21-26)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    const zone = document.querySelector('#import-file-dropzone')!;
    const file = new File(['Title,Artist\nDropped,Band'], 'dropped.csv', { type: 'text/csv' });
    fireEvent.drop(zone, { dataTransfer: { files: [file] } });
    await waitFor(() => expect(screen.getByText('dropped.csv')).toBeInTheDocument());
    expect(previewRows()).toEqual([['1', 'Dropped', 'Band', '']]);
  });
});

describe('ImportFileTab — plain text', () => {
  it('order + separator selects re-derive the tracks (242-268)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('list.txt', 'Band A - Song A\nBand B - Song B\nLonelyLine');

    await waitFor(() => expect(screen.getByText('list.txt')).toBeInTheDocument());
    // Default artist-title with ' - '.
    expect(previewRows()).toEqual([
      ['1', 'Song A', 'Band A', ''],
      ['2', 'Song B', 'Band B', ''],
      ['3', 'LonelyLine', '', ''],
    ]);
    // The CSV mapping bar must not be present for a text file (320-321).
    expect(document.querySelector('#import-file-column-mapping')).toBeNull();

    fireEvent.change(screen.getByDisplayValue('Artist - Title'), {
      target: { value: 'title-artist' },
    });
    expect(previewRows()).toEqual([
      ['1', 'Band A', 'Song A', ''],
      ['2', 'Band B', 'Song B', ''],
      ['3', 'LonelyLine', '', ''],
    ]);

    // A separator that matches nothing collapses every line to a track name.
    const separator = document.querySelector('#import-file-text-separator')!;
    fireEvent.change(separator, { target: { value: '|' } });
    expect(previewRows()).toEqual([
      ['1', 'Band A - Song A', '', ''],
      ['2', 'Band B - Song B', '', ''],
      ['3', 'LonelyLine', '', ''],
    ]);
  });
});

describe('ImportFileTab — M3U', () => {
  it('#EXTINF artist/title/duration parse, and #PLAYLIST: wins the name pre-fill (328-333)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile(
      'road.m3u',
      '#EXTM3U\n#PLAYLIST:Road Trip\n#EXTINF:210,Band A - Song A\n/music/a.mp3\n#EXTINF:180,Band B - Song B\n/music/b.mp3\n',
    );
    await waitFor(() => expect(screen.getByText('road.m3u')).toBeInTheDocument());
    expect(previewRows()).toEqual([
      ['1', 'Song A', 'Band A', ''],
      ['2', 'Song B', 'Band B', ''],
    ]);
    expect(screen.getByPlaceholderText('Enter playlist name...')).toHaveValue('Road Trip');
    // M3U is self-describing: neither tuning bar renders (203-208, 320-321).
    expect(document.querySelector('#import-file-text-format')).toBeNull();
    expect(document.querySelector('#import-file-column-mapping')).toBeNull();
  });
});

describe('ImportFileTab — submit', () => {
  it('mirrors as source file with the vanilla payload, toasts, clears, and calls back', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    const onImported = vi.fn();
    vi.spyOn(Date, 'now').mockReturnValue(1234567890);

    render(<ImportFileTab onImported={onImported} />);
    // 'Notes' maps to nothing, so row 2 survives the parser but has no names —
    // it must be filtered out at submit (432), not just hidden.
    dropFile('mine.csv', 'Title,Artist,Album,Length\nSong A,Band A,Rec A,3:30\n,,orphan,9:99\n');
    await waitFor(() => expect(screen.getByText('mine.csv')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText('Enter playlist name...'), {
      target: { value: '  My Import  ' },
    });
    fireEvent.click(screen.getByText('Import as Mirrored Playlist'));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ url: '/api/mirror-playlist', method: 'POST' });
    expect(calls[0].body).toMatchObject({
      source: 'file',
      source_playlist_id: 'file_1234567890',
      name: 'My Import',
      description: 'Imported from mine.csv',
      owner: 'local',
    });
    // Only the valid row ships (432), with every field wired through — the
    // duration heuristic included (3:30 → 210000ms).
    const shipped = (calls[0].body as { tracks: Record<string, unknown>[] }).tracks;
    expect(shipped).toHaveLength(1);
    expect(shipped[0]).toMatchObject({
      track_name: 'Song A',
      artist_name: 'Band A',
      album_name: 'Rec A',
      duration_ms: 210000,
    });
    expect(toast).toHaveBeenCalledWith('Imported "My Import" with 1 tracks', 'success');
    expect(onImported).toHaveBeenCalledOnce();
    // Cleared back to the upload zone with an empty name (412-421).
    expect(screen.getByText('Drop your file here')).toBeInTheDocument();
  });

  it('the import button is disabled until a name is typed (36-38, 336-337)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('noname.m3u', '#EXTM3U\n#EXTINF:1,A - B\n/x.mp3\n');
    await waitFor(() => expect(screen.getByText('noname.m3u')).toBeInTheDocument());
    // The filename pre-fill enables it; blanking the field disables it again.
    expect(screen.getByText('Import as Mirrored Playlist')).toBeEnabled();
    fireEvent.change(screen.getByPlaceholderText('Enter playlist name...'), {
      target: { value: '   ' },
    });
    expect(screen.getByText('Import as Mirrored Playlist')).toBeDisabled();
  });

  it('a file with no valid tracks refuses to import (433-436)', async () => {
    stubFetch();
    const toast = vi.fn();
    window.showToast = toast as typeof window.showToast;
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('empty.csv', 'Title,Artist\n,\n,\n');
    await waitFor(() => expect(screen.getByText('empty.csv')).toBeInTheDocument());
    expect(screen.getByText('0 tracks parsed')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Import as Mirrored Playlist'));
    expect(toast).toHaveBeenCalledWith('No valid tracks to import.', 'error');
    expect(calls).toEqual([]);
  });

  it('the ✕ clear button returns to the upload zone (412-421)', async () => {
    stubFetch();
    render(<ImportFileTab onImported={() => undefined} />);
    dropFile('mine.csv', 'Title,Artist\nS,A');
    await waitFor(() => expect(screen.getByText('mine.csv')).toBeInTheDocument());
    fireEvent.click(screen.getByTitle('Clear and start over'));
    expect(screen.getByText('Drop your file here')).toBeInTheDocument();
    expect(document.querySelector('#import-file-preview-section')).toBeNull();
  });
});
