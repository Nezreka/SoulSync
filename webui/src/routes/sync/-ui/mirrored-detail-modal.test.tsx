/**
 * The tracks detail modal, transcribed from openMirroredPlaylistModal
 * (stats-automations.js 1120-1157). Chrome and copy are pinned as literals;
 * the numeric helpers have their own differential coverage in
 * -sync.mirrored.test.ts.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { descriptionLink, MirroredDetailModal, placeCoverMenu } from './mirrored-detail-modal';

const NOW = Date.UTC(2026, 7, 5, 12, 0, 0);

/** the hero's ⋯, where everything but play / sync / identify lives now */
function openMore() {
  fireEvent.click(screen.getByLabelText('More actions'));
}

function renderModal(data: Parameters<typeof MirroredDetailModal>[0]['data'], on = {}) {
  const handlers = {
    onClose: vi.fn(),
    onDelete: vi.fn(),
    onEditSource: vi.fn(),
    onRunPipeline: vi.fn(),
    onDiscover: vi.fn(),
    onRefresh: vi.fn(),
    ...on,
  };
  render(<MirroredDetailModal playlistId={3} data={data} now={NOW} {...handlers} />);
  return handlers;
}

describe('MirroredDetailModal', () => {
  it('renders the hero, the meta line and the track rows (1120-1146)', () => {
    renderModal({
      name: 'Road Trip',
      source: 'spotify',
      owner: 'boulder',
      tracks: [
        {
          position: 1,
          track_name: 'Alright',
          artist_name: 'Kendrick Lamar',
          album_name: 'TPAB',
          duration_ms: 219000,
        },
      ],
    });
    expect(document.querySelector('#mirrored-track-modal')).not.toBeNull();
    expect(screen.getByText('Mirrored Playlist')).toBeInTheDocument();
    expect(screen.getByText('Road Trip')).toBeInTheDocument();
    expect(screen.getByText('Spotify')).toBeInTheDocument();
    expect(screen.getByText('boulder')).toBeInTheDocument();
    expect(screen.getByText('1 tracks')).toBeInTheDocument();
    expect(screen.getByText('Alright')).toBeInTheDocument();
    expect(screen.getByText('3:39')).toBeInTheDocument();
    // The column head, exactly as at 1143.
    expect(document.querySelector('.mm-col-dur')?.textContent).toBe('Time');
  });

  it("#1613: says what the source couldn't load", () => {
    renderModal({
      name: "Cam's playlist",
      source: 'tidal',
      source_skipped: { videos: 1, unavailable: 29 },
      tracks: [],
    });
    expect(document.querySelector('.mm-skipped-note')?.textContent).toBe(
      "29 tracks couldn't be loaded from Tidal (removed or not available in your region) · 1 video skipped",
    );
  });

  it('#1613: no note when nothing was skipped', () => {
    renderModal({ name: 'Whole', source: 'tidal', source_skipped: null, tracks: [] });
    expect(document.querySelector('.mm-skipped-note')).toBeNull();
  });

  it('shows the empty line rather than an empty list (1145)', () => {
    renderModal({ name: 'Empty', source: 'spotify', tracks: [] });
    expect(screen.getByText('No tracks in this mirror yet.')).toBeInTheDocument();
    expect(screen.getByText('0 tracks')).toBeInTheDocument();
  });

  it('an unknown source keeps its raw name and the clipboard tile (1088-1089)', () => {
    renderModal({ name: 'Odd', source: 'navidrome', tracks: [] });
    expect(screen.getByText('navidrome')).toBeInTheDocument();
    expect(document.querySelector('.mm-cover-empty')?.textContent).toBe('📋');
  });

  it('uses the hero cover when there is art, and the tile when there is not', () => {
    const { unmount } = render(
      <MirroredDetailModal
        playlistId={3}
        data={{ name: 'A', source: 'tidal', image_url: 'http://cover', tracks: [] }}
        now={NOW}
        onClose={vi.fn()}
        onDelete={vi.fn()}
        onEditSource={vi.fn()}
        onRunPipeline={vi.fn()}
        onDiscover={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(document.querySelector('.mm-cover-empty')).toBeNull();
    expect(document.querySelector('.mm-hero-bg')).not.toBeNull();
    unmount();
    renderModal({ name: 'A', source: 'tidal', tracks: [] });
    expect(document.querySelector('.mm-cover-empty')).not.toBeNull();
    expect(document.querySelector('.mm-hero-bg')).toBeNull();
  });

  it('omits the runtime segment entirely when nothing has a duration (1133)', () => {
    renderModal({ name: 'A', source: 'spotify', tracks: [{ track_name: 'x' }] });
    expect(screen.queryByText('0 min')).toBeNull();
  });

  it('Refresh from {source} shows when wired and calls its handler (#1413)', () => {
    const onRefreshFromSource = vi.fn();
    renderModal({ name: 'A', source: 'youtube', tracks: [] }, { onRefreshFromSource });
    // #1289: it names the source ("Refresh from YouTube"), in the ⋯ menu now
    openMore();
    const item = screen.getByRole('menuitem', { name: /Refresh from YouTube/ });
    expect(item).toHaveTextContent('pull the latest tracks, nothing pushed or downloaded');
    fireEvent.click(item);
    expect(onRefreshFromSource).toHaveBeenCalled();
  });

  it('no handler, no refresh button', () => {
    renderModal({ name: 'A', source: 'youtube', tracks: [] });
    openMore();
    expect(screen.queryByText('Refresh from YouTube')).toBeNull();
  });

  it('labels the actions by direction (#1289): sync pushes, refresh only pulls', () => {
    renderModal({ name: 'A', source: 'spotify', tracks: [] }, { onRefreshFromSource: vi.fn() });
    expect(screen.getByText('Sync & download')).toHaveAttribute(
      'title',
      "Refresh from the source, match, push to your server and download what's missing",
    );
    openMore();
    expect(screen.getByRole('menuitem', { name: /Refresh from Spotify/ })).toHaveTextContent(
      'nothing pushed or downloaded',
    );
  });

  it('wires the five actions, and Delete CLOSES first (1148)', () => {
    const h = renderModal({ name: 'A', source: 'spotify', tracks: [] });
    fireEvent.click(screen.getByText('Identify'));
    expect(h.onDiscover).toHaveBeenCalled();
    fireEvent.click(screen.getByText('Sync & download'));
    expect(h.onRunPipeline).toHaveBeenCalled();
    openMore();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit source link' }));
    expect(h.onEditSource).toHaveBeenCalled();
    // the ✕ is the only close now; the footer bar is gone
    fireEvent.click(screen.getByLabelText('Close'));
    expect(h.onClose).toHaveBeenCalled();

    h.onClose.mockClear();
    openMore();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete mirror' }));
    expect(h.onClose).toHaveBeenCalled();
    expect(h.onDelete).toHaveBeenCalled();
  });

  it('closes on the backdrop but NOT on the panel (1159)', () => {
    const h = renderModal({ name: 'A', source: 'spotify', tracks: [] });
    fireEvent.click(document.querySelector('.mirrored-modal') as Element);
    expect(h.onClose).not.toHaveBeenCalled();
    fireEvent.click(document.querySelector('#mirrored-track-modal') as Element);
    expect(h.onClose).toHaveBeenCalled();
  });
});

describe('MirroredDetailModal — a user playlist', () => {
  const MINE = {
    name: 'Late night',
    source: 'soulsync',
    tracks: [
      { position: 1, track_name: 'One', artist_name: 'A' },
      { position: 2, track_name: 'Two', artist_name: 'B' },
      { position: 3, track_name: 'Three', artist_name: 'C' },
    ],
  };

  it('reads as yours: no source pill, no Edit Source, no refresh', () => {
    renderModal(MINE, {
      onRefreshFromSource: vi.fn(),
      onRemoveTrack: vi.fn(),
      onReorder: vi.fn(),
    });
    expect(screen.getByText('Your Playlist')).toBeInTheDocument();
    expect(screen.queryByText('SoulSync')).toBeNull();
    expect(screen.getByText('Sync & download')).toBeInTheDocument();
    openMore();
    expect(screen.queryByText('Edit source link')).toBeNull();
    expect(screen.queryByText(/^Refresh from/)).toBeNull();
    expect(screen.getByRole('menuitem', { name: 'Delete playlist' })).toBeInTheDocument();
  });

  it('removes by position, and locks the other rows until the refetch lands', () => {
    const onRemoveTrack = vi.fn();
    renderModal(MINE, { onRemoveTrack, onReorder: vi.fn() });
    fireEvent.click(screen.getByLabelText('Remove Two from this playlist'));
    expect(onRemoveTrack).toHaveBeenCalledWith(2);
    // a stale position could hit the wrong track
    expect(
      (screen.getByLabelText('Remove One from this playlist') as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('drag and drop hands over the whole new order', () => {
    const onReorder = vi.fn();
    renderModal(MINE, { onRemoveTrack: vi.fn(), onReorder });
    const rows = document.querySelectorAll('.mm-row');
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
    fireEvent.dragStart(rows[2], { dataTransfer });
    fireEvent.dragOver(rows[0], { dataTransfer });
    fireEvent.drop(rows[0], { dataTransfer });
    expect(onReorder).toHaveBeenCalledWith([3, 1, 2]);
  });

  it('dropping a row on itself does nothing', () => {
    const onReorder = vi.fn();
    renderModal(MINE, { onRemoveTrack: vi.fn(), onReorder });
    const row = document.querySelectorAll('.mm-row')[1];
    const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
    fireEvent.dragStart(row, { dataTransfer });
    fireEvent.drop(row, { dataTransfer });
    expect(onReorder).not.toHaveBeenCalled();
  });

  it('a synced mirror stays read-only even if edit handlers are passed', () => {
    renderModal({ ...MINE, source: 'spotify' }, { onRemoveTrack: vi.fn(), onReorder: vi.fn() });
    expect(document.querySelector('.mm-row-remove')).toBeNull();
    expect(document.querySelector('.mm-row')?.getAttribute('draggable')).toBeNull();
  });

  it('an empty one says how to fill it, and opens straight onto Add songs', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ success: true, tracks: [] }))),
    );
    renderModal({ ...MINE, tracks: [] }, { onRemoveTrack: vi.fn(), onReorder: vi.fn() });
    expect(
      screen.getByText('Nothing in here yet. Use Add songs above, or the + on any track.'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Search for songs')).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});

describe('MirroredDetailModal — add to playlist', () => {
  it('every row of a synced mirror can be copied onto your playlist', () => {
    renderModal({
      name: 'Release Radar',
      source: 'spotify',
      tracks: [
        { position: 1, track_name: 'One', artist_name: 'A' },
        { position: 2, track_name: 'Two', artist_name: 'B' },
      ],
    });
    expect(screen.getByLabelText('Add One to a playlist')).toHaveClass('mm-row-add');
    expect(screen.getByLabelText('Add Two to a playlist')).toBeInTheDocument();
    // still read-only: copying is not editing
    expect(document.querySelector('.mm-row-remove')).toBeNull();
  });

  it('your own playlist gets it beside remove, so a song can go to another list', () => {
    renderModal(
      {
        name: 'Mine',
        source: 'soulsync',
        tracks: [{ position: 1, track_name: 'One', artist_name: 'A' }],
      },
      { onRemoveTrack: vi.fn(), onReorder: vi.fn() },
    );
    expect(screen.getByLabelText('Add One to a playlist')).toBeInTheDocument();
    expect(screen.getByLabelText('Remove One from this playlist')).toBeInTheDocument();
  });
});

describe('MirroredDetailModal — user playlist meta', () => {
  const own = {
    name: 'Mine',
    source: 'soulsync',
    tracks: [{ position: 1, track_name: 'One', artist_name: 'A', image_url: 'https://img/a.jpg' }],
  };

  it('your own playlist gets a change-cover button and a description prompt', () => {
    renderModal(own, { onRemoveTrack: vi.fn(), onReorder: vi.fn() });
    expect(screen.getByRole('button', { name: 'Change cover' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add a description' })).toBeInTheDocument();
  });

  it('a synced mirror gets neither', () => {
    renderModal({ name: 'Theirs', source: 'spotify', tracks: [] });
    expect(screen.queryByRole('button', { name: 'Change cover' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add a description' })).toBeNull();
  });

  it('an existing description shows with an edit affordance', () => {
    renderModal(
      { ...own, description: 'road trip, no skips' },
      { onRemoveTrack: vi.fn(), onReorder: vi.fn() },
    );
    expect(screen.getByText('road trip, no skips')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit description' })).toBeInTheDocument();
  });

  it('a mirror shows the source description read-only', () => {
    renderModal({ name: 'Theirs', source: 'spotify', description: 'from spotify', tracks: [] });
    expect(screen.getByText('from spotify')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit description' })).toBeNull();
  });

  it('the cover menu offers upload, track art, url and remove', () => {
    renderModal(
      { ...own, image_url: 'https://img/cover.jpg' },
      { onRemoveTrack: vi.fn(), onReorder: vi.fn() },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Change cover' }));
    expect(screen.getByText('Upload a photo…')).toBeInTheDocument();
    expect(screen.getByText('From this playlist')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('https://…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove cover' })).toBeInTheDocument();
  });

  it('the cover menu renders on body, outside the clipping hero', () => {
    renderModal(own, { onRemoveTrack: vi.fn(), onReorder: vi.fn() });
    fireEvent.click(screen.getByRole('button', { name: 'Change cover' }));
    const menu = document.querySelector('.mm-cover-menu');
    expect(menu?.parentElement).toBe(document.body);
    expect(menu?.closest('.mm-hero')).toBeNull();
  });

  it('the cover menu closes on escape and on an outside click, not an inside one', () => {
    renderModal(own, { onRemoveTrack: vi.fn(), onReorder: vi.fn() });
    const toggle = screen.getByRole('button', { name: 'Change cover' });
    fireEvent.click(toggle);
    fireEvent.mouseDown(screen.getByPlaceholderText('https://…'));
    expect(document.querySelector('.mm-cover-menu')).not.toBeNull();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.querySelector('.mm-cover-menu')).toBeNull();
    fireEvent.click(toggle);
    fireEvent.mouseDown(screen.getByText('One'));
    expect(document.querySelector('.mm-cover-menu')).toBeNull();
  });

  it('duplicate calls through and the tab opens the copy', async () => {
    const onDuplicate = vi.fn(async () => {});
    renderModal(own, { onRemoveTrack: vi.fn(), onReorder: vi.fn(), onDuplicate });
    openMore();
    fireEvent.click(screen.getByRole('menuitem', { name: /Duplicate/ }));
    await waitFor(() => expect(onDuplicate).toHaveBeenCalledTimes(1));
    // the menu closed on select; open it again once the busy state resets
    await waitFor(() => {
      openMore();
      expect(screen.getByRole('menuitem', { name: /^Duplicate/ })).not.toBeDisabled();
    });
  });
});

describe('placeCoverMenu', () => {
  const menu = { width: 248, height: 300 };
  const vp = { width: 1200, height: 900 };

  it('drops below the cover when it fits', () => {
    expect(placeCoverMenu({ top: 100, bottom: 228, left: 300 }, menu, vp)).toEqual({
      top: 236,
      left: 300,
    });
  });

  it('flips above when below would run off screen', () => {
    expect(placeCoverMenu({ top: 500, bottom: 628, left: 300 }, menu, vp)).toEqual({
      top: 192,
      left: 300,
    });
  });

  it('stays on screen when neither side fits', () => {
    const pos = placeCoverMenu({ top: 120, bottom: 248, left: 300 }, menu, {
      width: 1200,
      height: 360,
    });
    expect(pos.top).toBe(8);
  });

  it('pulls left on a narrow screen', () => {
    expect(
      placeCoverMenu({ top: 100, bottom: 228, left: 200 }, menu, { width: 390, height: 900 }).left,
    ).toBe(134);
  });
});

describe('the made-over hero', () => {
  it('a url-only description is an Open on link, not a raw url', () => {
    renderModal({
      name: 'thomasclan test',
      source: 'deezer',
      description: 'https://www.deezer.com/us/playlist/15769125861',
      tracks: [],
    });
    expect(screen.queryByText('https://www.deezer.com/us/playlist/15769125861')).toBeNull();
    const link = screen.getByText('Open on Deezer ↗');
    expect(link).toHaveAttribute('href', 'https://www.deezer.com/us/playlist/15769125861');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('a description with words stays a description', () => {
    renderModal({ name: 'A', source: 'deezer', description: 'songs for the gym', tracks: [] });
    expect(screen.getByText('songs for the gym')).toBeInTheDocument();
    expect(screen.queryByText(/Open on/)).toBeNull();
  });

  it('descriptionLink only takes a bare http(s) url', () => {
    expect(descriptionLink(' https://x.y/z ')).toBe('https://x.y/z');
    expect(descriptionLink('see https://x.y/z')).toBeNull();
    expect(descriptionLink('javascript:alert(1)')).toBeNull();
    expect(descriptionLink(null)).toBeNull();
  });

  it('the source label sits in the eyebrow', () => {
    renderModal({ name: 'A', source: 'deezer', tracks: [] });
    expect(document.querySelector('.mm-eyebrow .mm-source-pill')?.textContent).toBe('Deezer');
  });

  it('there is no footer bar any more', () => {
    renderModal({ name: 'A', source: 'spotify', tracks: [] });
    expect(document.querySelector('.mm-actions')).toBeNull();
  });

  it('the ⋯ menu closes on Escape and on a second press', () => {
    renderModal({ name: 'A', source: 'spotify', tracks: [] });
    openMore();
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    openMore();
    openMore();
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
