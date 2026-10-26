import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ImportFileTab } from './import-file-tab';

const PARSED = {
  success: true,
  bookmarked: 2,
  playlists: [
    {
      id: 'newpipe_2',
      name: 'Gym',
      track_count: 1,
      tracks: [{ track_name: 'Sanctuary', artist_name: 'TAYA.', source_track_id: 'AAA' }],
    },
    { id: 'newpipe_1', name: 'Empty one', track_count: 0, tracks: [] },
    {
      id: 'newpipe_3',
      name: 'Chill',
      track_count: 1,
      tracks: [{ track_name: 'Neglect', artist_name: 'Charles Oliver', source_track_id: 'CCC' }],
    },
  ],
};

let parseBody: unknown = PARSED;
let mirrorAnswer: (name: string) => unknown = () => ({ success: true, playlist_id: 1 });
let mirrored: { source: string; source_playlist_id: string; name: string }[] = [];

beforeEach(() => {
  parseBody = PARSED;
  mirrorAnswer = () => ({ success: true, playlist_id: 1 });
  mirrored = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/import/newpipe') {
        expect(init?.body).toBeInstanceOf(FormData);
        return new Response(JSON.stringify(parseBody));
      }
      if (url === '/api/mirror-playlist') {
        const body = JSON.parse(init?.body as string);
        mirrored.push(body);
        return new Response(JSON.stringify(mirrorAnswer(body.name)));
      }
      return new Response('{}');
    }),
  );
  vi.stubGlobal('showToast', vi.fn());
});
afterEach(() => vi.unstubAllGlobals());

function dropBackup(name = 'NewPipeData-20261022.zip') {
  const onImported = vi.fn();
  render(<ImportFileTab onImported={onImported} />);
  const input = document.querySelector('#import-file-input') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(['PK'], name)] } });
  return onImported;
}

describe('NewPipe import', () => {
  it('a zip goes to the newpipe branch, every non-empty playlist pre-ticked', async () => {
    dropBackup();
    await waitFor(() => expect(screen.getByText('Gym')).toBeInTheDocument());
    expect(screen.getByText('NewPipe backup · 3 playlists')).toBeInTheDocument();
    expect((screen.getByLabelText('Import Gym') as HTMLInputElement).checked).toBe(true);
    const empty = screen.getByLabelText('Import Empty one') as HTMLInputElement;
    expect(empty.checked).toBe(false);
    expect(empty.disabled).toBe(true);
    expect(screen.getByText(/2 bookmarked YouTube playlists are only a link/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import 2 playlists' })).toBeEnabled();
  });

  it('mirrors only the ticked ones, as newpipe, then hands back to the page', async () => {
    const onImported = dropBackup('newpipe.db');
    await waitFor(() => expect(screen.getByText('Chill')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Import Chill'));
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 playlist' }));

    await waitFor(() => expect(onImported).toHaveBeenCalled());
    expect(mirrored.map((m) => [m.source, m.source_playlist_id, m.name])).toEqual([
      ['newpipe', 'newpipe_2', 'Gym'],
    ]);
    expect(window.showToast).toHaveBeenCalledWith('Imported 1 NewPipe playlist', 'success');
  });

  it('a failed mirror is named and the picker stays open', async () => {
    mirrorAnswer = (name) =>
      name === 'Chill' ? { success: false, error: 'nope' } : { success: true };
    const onImported = dropBackup();
    await waitFor(() => expect(screen.getByText('Chill')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Import 2 playlists' }));

    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith("Couldn't import: Chill", 'error'),
    );
    expect(window.showToast).toHaveBeenCalledWith('Imported 1 NewPipe playlist', 'warning');
    expect(onImported).not.toHaveBeenCalled();
    expect(screen.getByText('Gym')).toBeInTheDocument();
  });

  it("shows the server's reason when the file is not a backup, and clears back", async () => {
    parseBody = { success: false, error: 'this zip has no newpipe.db in it' };
    dropBackup();
    await waitFor(() =>
      expect(screen.getByText('this zip has no newpipe.db in it')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTitle('Clear and start over'));
    expect(screen.getByText('Drop your file here')).toBeInTheDocument();
  });

  it('a csv still takes the old path', async () => {
    render(<ImportFileTab onImported={vi.fn()} />);
    const input = document.querySelector('#import-file-input') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['Title,Artist\nA,B'], 'list.csv', { type: 'text/csv' })] },
    });
    await waitFor(() =>
      expect(document.querySelector('#import-file-preview-section')).not.toBeNull(),
    );
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});
