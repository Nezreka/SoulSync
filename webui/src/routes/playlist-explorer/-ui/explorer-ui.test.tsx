import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ExplorerArtist, MirroredPlaylist } from '../-explorer.types';

import { ExplorerActionBar, ExplorerProgress, ExplorerZoomControls } from './explorer-chrome';
import { ExplorerConnections } from './explorer-connections';
import { ExplorerPicker } from './explorer-picker';
import { ExplorerTree } from './explorer-tree';

/**
 * The explorer's presentational layer. These assert the artefacts the vanilla
 * produced — class names, ids, data attributes, gating — because the
 * interaction controller and the connection layer both find their targets by
 * querying for exactly those.
 */

afterEach(cleanup);

const NO_CONNECTIONS = { width: 0, height: 0, paths: [] };

function pickerProps(overrides: Partial<Parameters<typeof ExplorerPicker>[0]> = {}) {
  return {
    playlists: [] as MirroredPlaylist[],
    activeSource: null,
    onSelectSource: vi.fn(),
    selectedPlaylistId: null,
    onSelectPlaylist: vi.fn(),
    onStartDiscovery: vi.fn(),
    discoverStates: {},
    liveDiscovery: {},
    mode: 'albums' as const,
    onSetMode: vi.fn(),
    building: false,
    hasBuilt: false,
    onBuild: vi.fn(),
    ...overrides,
  };
}

describe('ExplorerPicker', () => {
  it('tells the user to sync something when there are no mirrored playlists', () => {
    render(<ExplorerPicker {...pickerProps()} />);
    expect(screen.getByText('No mirrored playlists found. Sync a playlist first.')).toBeTruthy();
  });

  it('hides the tab strip at one source and shows it at two', () => {
    const { container, rerender } = render(
      <ExplorerPicker {...pickerProps({ playlists: [{ id: 1, name: 'A', source: 'spotify' }] })} />,
    );
    expect((container.querySelector('#explorer-picker-tabs') as HTMLElement).style.display).toBe(
      'none',
    );

    rerender(
      <ExplorerPicker
        {...pickerProps({
          playlists: [
            { id: 1, name: 'A', source: 'spotify' },
            { id: 2, name: 'B', source: 'tidal' },
          ],
        })}
      />,
    );
    const tabs = container.querySelectorAll('.explorer-picker-tab');
    expect([...tabs].map((tab) => tab.textContent)).toEqual(['Spotify 1', 'Tidal 1']);
  });

  it('shows only the active source, and switches on a tab click', () => {
    const onSelectSource = vi.fn();
    const { container } = render(
      <ExplorerPicker
        {...pickerProps({
          activeSource: 'tidal',
          onSelectSource,
          playlists: [
            {
              id: 1,
              name: 'Spotify one',
              source: 'spotify',
              total_count: 10,
              discovered_count: 10,
            },
            { id: 2, name: 'Tidal one', source: 'tidal', total_count: 10, discovered_count: 10 },
          ],
        })}
      />,
    );
    expect(container.querySelectorAll('.explorer-picker-card')).toHaveLength(1);
    expect(screen.getByText('Tidal one')).toBeTruthy();

    fireEvent.click(screen.getByText('Spotify', { exact: false }));
    expect(onSelectSource).toHaveBeenCalledWith('spotify');
  });

  it('leaves an under-discovered card unclickable and gives it a Discover button', () => {
    const onSelectPlaylist = vi.fn();
    const onStartDiscovery = vi.fn();
    const { container } = render(
      <ExplorerPicker
        {...pickerProps({
          onSelectPlaylist,
          onStartDiscovery,
          playlists: [
            { id: 7, name: 'Half', source: 'spotify', total_count: 10, discovered_count: 2 },
          ],
        })}
      />,
    );
    const card = container.querySelector('.explorer-picker-card') as HTMLElement;
    expect(card.className).toContain('not-ready');
    fireEvent.click(card);
    expect(onSelectPlaylist).not.toHaveBeenCalled();

    const discover = screen.getByRole('button', { name: 'Discover' });
    fireEvent.click(discover);
    expect(onStartDiscovery).toHaveBeenCalledWith(7);
    // The click must not also select the card behind it.
    expect(onSelectPlaylist).not.toHaveBeenCalled();
  });

  it('selects a ready card and renders no Discover button on it', () => {
    const onSelectPlaylist = vi.fn();
    const { container } = render(
      <ExplorerPicker
        {...pickerProps({
          onSelectPlaylist,
          playlists: [
            { id: 3, name: 'Ready', source: 'spotify', total_count: 10, discovered_count: 9 },
          ],
        })}
      />,
    );
    fireEvent.click(container.querySelector('.explorer-picker-card') as HTMLElement);
    expect(onSelectPlaylist).toHaveBeenCalledWith(3);
    expect(screen.queryByRole('button', { name: 'Discover' })).toBeNull();
  });

  it('shows the badge and the meta line the card view derives', () => {
    const { container } = render(
      <ExplorerPicker
        {...pickerProps({
          playlists: [
            {
              id: 1,
              name: 'Owned',
              source: 'spotify',
              total_count: 10,
              discovered_count: 10,
              in_library_count: 9,
              wishlisted_count: 2,
            },
          ],
        })}
      />,
    );
    const badge = container.querySelector('.explorer-picker-card-badge') as HTMLElement;
    expect(badge.className).toContain('downloaded');
    expect(badge.getAttribute('title')).toBe('Most tracks in library');
    expect(screen.getByText('9 in library')).toBeTruthy();
    expect(screen.getByText('2 wishlisted')).toBeTruthy();
  });

  it('replaces the meta line with a live discovery percentage', () => {
    render(
      <ExplorerPicker
        {...pickerProps({
          liveDiscovery: { 5: 42.4 },
          playlists: [{ id: 5, name: 'Live', source: 'spotify', total_count: 10 }],
        })}
      />,
    );
    expect(screen.getByText('Discovering... 42%')).toBeTruthy();
    expect(screen.queryByText('0% discovered')).toBeNull();
  });

  it('walks the Discover button through starting and open', () => {
    const { rerender } = render(
      <ExplorerPicker
        {...pickerProps({
          discoverStates: { 1: 'starting' },
          playlists: [{ id: 1, name: 'X', source: 'spotify', total_count: 10 }],
        })}
      />,
    );
    const starting = screen.getByRole('button', { name: 'Starting...' }) as HTMLButtonElement;
    expect(starting.disabled).toBe(true);

    rerender(
      <ExplorerPicker
        {...pickerProps({
          discoverStates: { 1: 'open' },
          playlists: [{ id: 1, name: 'X', source: 'spotify', total_count: 10 }],
        })}
      />,
    );
    const open = screen.getByRole('button', { name: 'Open' }) as HTMLButtonElement;
    expect(open.disabled).toBe(false);
    expect(open.getAttribute('title')).toBe('Reopen discovery modal');
  });

  it('names the selected playlist in the build hint', () => {
    const { rerender, container } = render(<ExplorerPicker {...pickerProps()} />);
    expect(container.querySelector('#explorer-build-hint')?.textContent).toBe(
      'Select a playlist above, then explore',
    );
    rerender(
      <ExplorerPicker
        {...pickerProps({
          selectedPlaylistId: 1,
          playlists: [
            { id: 1, name: 'Road trip', source: 'spotify', total_count: 4, discovered_count: 4 },
          ],
        })}
      />,
    );
    expect(container.querySelector('#explorer-build-hint')?.textContent).toBe('Ready: Road trip');
  });

  it('switches modes and keeps the active class in step', () => {
    const onSetMode = vi.fn();
    const { container } = render(<ExplorerPicker {...pickerProps({ onSetMode })} />);
    const [albums, discog] = [...container.querySelectorAll('.explorer-mode-btn')];
    expect(albums?.className).toContain('active');
    expect(discog?.className).not.toContain('active');
    fireEvent.click(discog as HTMLElement);
    expect(onSetMode).toHaveBeenCalledWith('discographies');
  });

  it('keeps the shorter build label once a tree has been built', () => {
    const { rerender } = render(<ExplorerPicker {...pickerProps()} />);
    expect(screen.getByRole('button', { name: /Explore Selected Playlist/ })).toBeTruthy();

    rerender(<ExplorerPicker {...pickerProps({ building: true })} />);
    const busy = screen.getByRole('button', { name: /Building/ }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);

    rerender(<ExplorerPicker {...pickerProps({ hasBuilt: true })} />);
    expect(screen.getByRole('button', { name: /^Explore$/ })).toBeTruthy();
  });
});

const ARTISTS: ExplorerArtist[] = [
  {
    name: 'Boards of Canada',
    image_url: 'boc.jpg',
    albums: [
      { spotify_id: 'al1', title: 'Geogaddi', year: 2002, track_count: 23, album_type: 'album' },
      { spotify_id: 'al2', title: 'Twoism', year: 1995, track_count: 8, owned: true },
      { title: 'No id', album_type: 'single', in_playlist: true },
    ],
  },
  { name: 'Aphex Twin', albums: [{ spotify_id: 'al3', title: 'SAW II' }] },
  { name: 'Nowhere', error: 'not found', albums: [] },
];

function treeProps(overrides: Partial<Parameters<typeof ExplorerTree>[0]> = {}) {
  return {
    meta: { type: 'meta' as const, playlist_name: 'Mix', total_tracks: 40, total_artists: 3 },
    artists: ARTISTS,
    expandedArtists: new Set<string>(),
    artistsWithSelection: new Set<string>(),
    selectedAlbums: new Set<string>(),
    addedAlbums: new Set<string>(),
    expandedTracks: {},
    onToggleArtist: vi.fn(),
    onAlbumClick: vi.fn(),
    zoom: 1,
    connections: NO_CONNECTIONS,
    ...overrides,
  };
}

describe('ExplorerTree', () => {
  it('shows the empty state until a tree has been built', () => {
    const { container } = render(<ExplorerTree {...treeProps({ meta: null })} />);
    expect(container.querySelector('#explorer-empty')).toBeTruthy();
    expect(container.querySelector('#explorer-root')).toBeNull();
  });

  it('renders the root node from the stream meta', () => {
    const { container } = render(<ExplorerTree {...treeProps()} />);
    expect(container.querySelector('#explorer-root')).toBeTruthy();
    expect(screen.getByText('SOURCE')).toBeTruthy();
    expect(screen.getByText('40 tracks · 3 artists')).toBeTruthy();
  });

  it('lays the artists out in rows of two then three', () => {
    const { container } = render(<ExplorerTree {...treeProps()} />);
    const rows = [...container.querySelectorAll('.explorer-tier-artists')];
    expect(rows.map((row) => row.children.length)).toEqual([2, 1]);
  });

  it('keys each branch by the sanitised artist name', () => {
    const { container } = render(<ExplorerTree {...treeProps()} />);
    expect(container.querySelector('#explorer-node-Boards_of_Canada')).toBeTruthy();
    expect(container.querySelector('#explorer-children-Aphex_Twin')).toBeTruthy();
  });

  it('marks an errored artist and refuses to expand it', () => {
    const onToggleArtist = vi.fn();
    const { container } = render(<ExplorerTree {...treeProps({ onToggleArtist })} />);
    const errored = container.querySelector('#explorer-node-Nowhere') as HTMLElement;
    expect(errored.className).toContain('error');
    expect(screen.getByText('Not found')).toBeTruthy();
    fireEvent.click(errored);
    expect(onToggleArtist).not.toHaveBeenCalled();
  });

  it('expands an artist and toggles it back', () => {
    const onToggleArtist = vi.fn();
    const { container } = render(<ExplorerTree {...treeProps({ onToggleArtist })} />);
    fireEvent.click(container.querySelector('#explorer-node-Boards_of_Canada') as HTMLElement);
    expect(onToggleArtist).toHaveBeenCalledWith('Boards_of_Canada');
  });

  it('renders albums only for expanded artists, with the right ids and badges', () => {
    const { container } = render(
      <ExplorerTree
        {...treeProps({
          expandedArtists: new Set(['Boards_of_Canada']),
          selectedAlbums: new Set(['al1']),
          addedAlbums: new Set(['al2']),
        })}
      />,
    );
    const albums = [...container.querySelectorAll('.explorer-node-album')];
    expect(albums.map((node) => node.getAttribute('data-id'))).toEqual([
      'al1',
      'al2',
      // No spotify_id, so the positional fallback keyed by the artist.
      'Boards_of_Canada_2',
    ]);
    expect(albums[0]?.className).toContain('selected');
    expect(albums[1]?.className).toContain('owned');
    expect(albums[1]?.className).toContain('added');
    expect(albums[2]?.className).toContain('in-playlist');
    // The collapsed artist stays collapsed.
    expect(container.querySelector('#explorer-children-Aphex_Twin')?.children.length).toBe(0);
  });

  it('describes an album in its tooltip', () => {
    const { container } = render(
      <ExplorerTree {...treeProps({ expandedArtists: new Set(['Boards_of_Canada']) })} />,
    );
    const owned = container.querySelectorAll('.explorer-node-album')[1] as HTMLElement;
    expect(owned.getAttribute('title')).toBe(
      'Twoism\n1995 · Album · 8 tracks\n✓ Already in library\nClick to select · Double-click for tracklist',
    );
  });

  it('reports an album click by node id', () => {
    const onAlbumClick = vi.fn();
    const { container } = render(
      <ExplorerTree {...treeProps({ expandedArtists: new Set(['Aphex_Twin']), onAlbumClick })} />,
    );
    fireEvent.click(container.querySelector('.explorer-node-album') as HTMLElement);
    expect(onAlbumClick).toHaveBeenCalledWith('al3');
  });

  it('renders a fetched tracklist under its album', () => {
    const { container } = render(
      <ExplorerTree
        {...treeProps({
          expandedArtists: new Set(['Aphex_Twin']),
          expandedTracks: {
            al3: [
              { track_number: 1, name: 'Radiator', duration_ms: 215000 },
              { track_number: 2, name: 'Rhubarb', duration_ms: 0 },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText('1. Radiator')).toBeTruthy();
    expect(screen.getByText('3:35')).toBeTruthy();
    expect(container.querySelectorAll('.explorer-node-track')).toHaveLength(2);
  });

  it('lights the ring on an artist whose album is selected', () => {
    const { container } = render(
      <ExplorerTree {...treeProps({ artistsWithSelection: new Set(['Aphex_Twin']) })} />,
    );
    expect(
      (container.querySelector('#explorer-node-Aphex_Twin') as HTMLElement).className,
    ).toContain('has-selection');
    expect(
      (container.querySelector('#explorer-node-Boards_of_Canada') as HTMLElement).className,
    ).not.toContain('has-selection');
  });

  it('scales from the top centre so the connection maths stays predictable', () => {
    const { container } = render(<ExplorerTree {...treeProps({ zoom: 0.5 })} />);
    const tree = container.querySelector('#explorer-tree') as HTMLElement;
    expect(tree.style.transform).toBe('scale(0.5)');
    expect(tree.style.transformOrigin).toBe('top center');
  });
});

describe('ExplorerConnections', () => {
  it('sizes the canvas and draws the paths it is given', () => {
    const { container } = render(
      <ExplorerConnections
        width={800}
        height={600}
        paths={[
          {
            id: 'p1',
            d: 'M 0 0 C 0 45, 10 45, 10 100',
            stroke: 'url(#explorer-grad-root)',
            strokeWidth: '1.5',
            animated: true,
            length: 120,
          },
          {
            id: 'p2',
            d: 'M 1 1 C 1 2, 3 2, 3 4',
            stroke: 'rgba(255,255,255,0.05)',
            strokeWidth: '0.8',
            animated: false,
            length: 5,
          },
        ]}
      />,
    );
    const svg = container.querySelector('#explorer-svg') as SVGElement;
    expect(svg.getAttribute('viewBox')).toBe('0 0 800 600');

    const paths = [...container.querySelectorAll('path')];
    expect(paths[0]?.getAttribute('class')).toBe('explorer-line explorer-line-animated');
    expect((paths[0] as unknown as HTMLElement).style.strokeDashoffset).toBe('120');
    expect(paths[1]?.getAttribute('class')).toBe('explorer-line');
    expect((paths[1] as unknown as HTMLElement).style.strokeDashoffset).toBe('');
    // Both gradients the strokes reference must exist.
    expect(container.querySelector('#explorer-grad-root')).toBeTruthy();
    expect(container.querySelector('#explorer-grad-album')).toBeTruthy();
  });
});

describe('the chrome', () => {
  it('counts the selection and wires the three actions', () => {
    const onSelectAll = vi.fn();
    const onDeselectAll = vi.fn();
    const onAddToWishlist = vi.fn();
    render(
      <ExplorerActionBar
        selectedCount={1}
        onSelectAll={onSelectAll}
        onDeselectAll={onDeselectAll}
        onAddToWishlist={onAddToWishlist}
      />,
    );
    expect(screen.getByText('1 album selected')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Select All/ }));
    fireEvent.click(screen.getByRole('button', { name: /Deselect/ }));
    fireEvent.click(screen.getByRole('button', { name: /Add to Wishlist/ }));
    expect(onSelectAll).toHaveBeenCalled();
    expect(onDeselectAll).toHaveBeenCalled();
    expect(onAddToWishlist).toHaveBeenCalled();
  });

  it('fills the progress bar to the given percentage', () => {
    const { container } = render(
      <ExplorerProgress percent={40} text="Discovering artists... 2 of 5" />,
    );
    expect((container.querySelector('#explorer-progress-fill') as HTMLElement).style.width).toBe(
      '40%',
    );
    expect(screen.getByText('Discovering artists... 2 of 5')).toBeTruthy();
  });

  it('zooms by the vanilla steps, fits, and resets', () => {
    const onZoom = vi.fn();
    const onFitToView = vi.fn();
    const onResetZoom = vi.fn();
    render(
      <ExplorerZoomControls onZoom={onZoom} onFitToView={onFitToView} onResetZoom={onResetZoom} />,
    );
    fireEvent.click(screen.getByTitle('Zoom in'));
    fireEvent.click(screen.getByTitle('Zoom out'));
    fireEvent.click(screen.getByTitle('Fit to view'));
    fireEvent.click(screen.getByTitle('Reset zoom'));
    expect(onZoom.mock.calls).toEqual([[0.15], [-0.15]]);
    expect(onFitToView).toHaveBeenCalled();
    expect(onResetZoom).toHaveBeenCalled();
  });
});
