import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SpotifyTab } from './account-tabs';
import { PlaylistSearch, noMatchText, playlistNameMatches } from './playlist-search';
import { filterServerList } from './server-playlist-list';

describe('playlistNameMatches', () => {
  it('is a case-blind substring over any of the names', () => {
    expect(playlistNameMatches(['Road Trip'], 'road')).toBe(true);
    expect(playlistNameMatches(['Road Trip'], 'TRIP')).toBe(true);
    expect(playlistNameMatches(['Road Trip', 'Custom'], 'cust')).toBe(true);
    expect(playlistNameMatches(['Road Trip'], 'gym')).toBe(false);
  });

  it('an empty or blank query matches everything, a missing name nothing', () => {
    expect(playlistNameMatches([undefined], '')).toBe(true);
    expect(playlistNameMatches(['x'], '   ')).toBe(true);
    expect(playlistNameMatches([undefined, null], 'x')).toBe(false);
  });
});

function Harness() {
  const [value, setValue] = useState('road');
  return <PlaylistSearch value={value} onChange={setValue} />;
}

describe('PlaylistSearch', () => {
  it('Escape and the clear button both empty it', () => {
    render(<Harness />);
    const box = screen.getByLabelText('Search playlists') as HTMLInputElement;
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(box.value).toBe('');
    fireEvent.change(box, { target: { value: 'gym' } });
    fireEvent.click(screen.getByLabelText('Clear search'));
    expect(box.value).toBe('');
    expect(screen.queryByLabelText('Clear search')).toBeNull();
  });
});

describe('filterServerList', () => {
  const view = {
    synced: [{ id: '1', name: 'Road Trip' }],
    unsynced: [{ id: '2', name: 'Gym' }],
    serverAdmin: [{ owner: 'a', profile: null, playlists: [{ id: '3', name: 'Road Work' }] }],
    others: [{ owner: 'b', profile: null, playlists: [{ id: '4', name: 'Sleep' }] }],
    serverType: 'navidrome',
  };

  it('narrows every section and drops owner groups the search empties', () => {
    const out = filterServerList(view, 'road');
    expect(out.synced.map((p) => p.id)).toEqual(['1']);
    expect(out.unsynced).toEqual([]);
    expect(out.serverAdmin.map((g) => g.playlists.map((p) => p.id))).toEqual([['3']]);
    expect(out.others).toEqual([]);
    expect(out.serverType).toBe('navidrome');
  });

  it('a blank query hands the list back untouched', () => {
    expect(filterServerList(view, ' ')).toBe(view);
  });
});

describe('SpotifyTab search', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify([
              { id: 'p1', name: 'Road Trip', track_count: 4 },
              { id: 'p2', name: 'Gym', track_count: 9 },
            ]),
          ),
      ),
    );
    vi.stubGlobal('showToast', vi.fn());
    vi.stubGlobal('showLoadingOverlay', vi.fn());
    vi.stubGlobal('hideLoadingOverlay', vi.fn());
  });
  afterEach(() => vi.unstubAllGlobals());

  it('filters the cards by name and says so when nothing is left', async () => {
    render(<SpotifyTab />);
    await waitFor(() => expect(screen.getByText('Road Trip')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Search playlists'), { target: { value: 'gym' } });
    expect(screen.queryByText('Road Trip')).toBeNull();
    expect(screen.getByText('Gym')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Search playlists'), { target: { value: 'zzz' } });
    expect(screen.getByText(noMatchText('zzz'))).toBeInTheDocument();
  });
});
