/**
 * The playlist card.
 *
 * The behaviour worth pinning is what a card DOESN'T show: a healthy playlist
 * gets no ring, no warning colour and no visible buttons. That restraint is the
 * design, and it is the first thing a future change would erode.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import { PlaylistCard, playlistCardMeta, playlistCardPrimaryLabel } from './playlist-card';

function row(over: Partial<MirroredPlaylistRow> = {}): MirroredPlaylistRow {
  return { id: 1, name: 'Road Trip', source: 'spotify', track_count: 140, ...over };
}

function renderCard(over: Partial<MirroredPlaylistRow> = {}, name = 'Road Trip') {
  const onOpen = vi.fn();
  const onClick = vi.fn();
  const onMore = vi.fn();
  const r = row(over);
  const { container } = render(
    <PlaylistCard
      row={r}
      name={name}
      when="synced 3h ago"
      schedule="Every 6 hours"
      onOpen={onOpen}
      primary={{ label: playlistCardPrimaryLabel(r), onClick }}
      onMore={onMore}
    />,
  );
  return { container, onOpen, onClick, onMore };
}

describe('the ring — what a healthy card does NOT show', () => {
  it('a synced playlist gets no ring at all', () => {
    // A ring on every card is forty of them announcing nothing is wrong.
    const { container } = renderCard({ total_count: 140, discovered_count: 140 });
    expect(container.querySelector('.pl-ring')).toBeNull();
    expect(container.querySelector('.plc')?.getAttribute('data-state')).toBe('ok');
  });

  it('a never-touched playlist also gets no ring', () => {
    const { container } = renderCard({ total_count: 140, discovered_count: 0 });
    expect(container.querySelector('.pl-ring')).toBeNull();
  });

  it('a short playlist says how short, in the warning tone, with no ring', () => {
    const { container } = renderCard({ total_count: 86, discovered_count: 62 });
    expect(container.querySelector('.pl-ring')).toBeNull();
    expect(container.querySelector('.plc')?.getAttribute('data-state')).toBe('short');
    expect(container.querySelector('.plc-status')?.textContent).toBe('86 tracks · 24 not found');
  });

  it('a failed run says so on its status line', () => {
    const { container } = renderCard({ pipeline_state: { status: 'error' } });
    expect(container.querySelector('.plc-status')?.textContent).toContain('Last run failed');
  });

  it('a run in flight shows its progress along the cover', () => {
    const r = row({ pipeline_state: { status: 'running', phase: 'Discovering' } });
    const { container } = render(
      <PlaylistCard
        row={r}
        name="Road Trip"
        when="x"
        schedule="Every 6 hours"
        onOpen={vi.fn()}
        onMore={vi.fn()}
        progress={42}
      />,
    );
    const bar = container.querySelector('.pl-card-progress') as HTMLElement;
    expect(bar.getAttribute('aria-valuenow')).toBe('42');
    expect(container.querySelector('.plc-status')?.textContent).toBe('140 tracks · discovering');
  });

  it('a resting card carries no progress bar', () => {
    const { container } = renderCard({ total_count: 10, discovered_count: 5 });
    expect(container.querySelector('.pl-card-progress')).toBeNull();
  });
});

describe('the meta line', () => {
  it('states the count alone when everything is fine', () => {
    // "140 tracks · 140 discovered" says the same thing twice.
    expect(
      playlistCardMeta(row({ total_count: 140, discovered_count: 140 }), 'synced 3h ago'),
    ).toBe('140 tracks · synced 3h ago');
  });

  it('reports a failure and a run in flight in the user’s words', () => {
    expect(playlistCardMeta(row({ pipeline_state: { status: 'error' } }), 'x')).toContain(
      'last run failed',
    );
    expect(
      playlistCardMeta(row({ pipeline_state: { status: 'running', phase: 'Discovering' } }), 'x'),
    ).toBe('140 tracks · discovering');
  });

  it('singularises, and survives a row with no timestamp', () => {
    expect(playlistCardMeta(row({ track_count: 1, total_count: 1, discovered_count: 1 }), '')).toBe(
      '1 track',
    );
  });
});

describe('the primary action', () => {
  it('offers the fix that matches the state', () => {
    expect(playlistCardPrimaryLabel(row({ total_count: 86, discovered_count: 62 }))).toBe(
      'Sync & download',
    );
    expect(playlistCardPrimaryLabel(row({ pipeline_state: { status: 'error' } }))).toBe('Retry');
    // NOT "Find 24 missing": that label promised a narrower action than the
    // identical pipeline.run it actually performed.
    expect(playlistCardPrimaryLabel(row({ total_count: 10, discovered_count: 10 }))).toBe(
      'Sync & download',
    );
  });

  it('never offers Cancel — the controller has no cancel to call', () => {
    // A button that cannot do what it says is worse than one offering less.
    expect(playlistCardPrimaryLabel(row({ pipeline_state: { status: 'running' } }))).toBe(
      'View progress',
    );
  });

  it('fires without also opening the card behind it', () => {
    const { container, onClick, onOpen } = renderCard({ total_count: 10, discovered_count: 4 });
    fireEvent.click(container.querySelector('.plc-primary') as HTMLElement);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('the overflow trigger does not open the card either', () => {
    const { container, onMore, onOpen } = renderCard();
    fireEvent.click(container.querySelector('.plc-more') as HTMLElement);
    expect(onMore).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });
});

describe('the card body', () => {
  it('opens from a click and from the keyboard', () => {
    const { container, onOpen } = renderCard();
    fireEvent.click(container.querySelector('.plc') as HTMLElement);
    expect(onOpen).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(container.querySelector('.plc') as HTMLElement, { key: 'Enter' });
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  it('falls back to the source brand mark on the cover, and shows the schedule', () => {
    const { container } = renderCard();
    // the brand mark is the cover's fallback when there is no art
    expect(container.querySelector('.pl-card-art-img .spotify-icon')).not.toBeNull();
    // With no onSchedule the pill is inert, and says so rather than claiming
    // "Not scheduled" — which would imply you could schedule it.
    expect(container.querySelector('.plc-schedule--inert')?.textContent).toBe(
      'Can\u2019t be scheduled',
    );
  });

  it('a schedulable card shows its cadence on a CLICKABLE pill', () => {
    const onSchedule = vi.fn();
    const r = row();
    const { container } = render(
      <PlaylistCard
        row={r}
        name="Road Trip"
        when="synced 3h ago"
        schedule="Every 6 hours"
        onOpen={vi.fn()}
        primary={{ label: 'Sync & download', onClick: vi.fn() }}
        onMore={vi.fn()}
        onSchedule={onSchedule}
      />,
    );
    const pill = container.querySelector('.plc-schedule') as HTMLElement;
    expect(pill.tagName).toBe('BUTTON');
    expect(pill.textContent).toBe('Every 6 hours');
  });

  it('the pill opens the picker without also opening the card', () => {
    // The hover veil used to cover the whole card, so the pill was unreachable
    // exactly when hovering revealed the controls.
    const onSchedule = vi.fn();
    const onOpen = vi.fn();
    const r = row();
    const { container } = render(
      <PlaylistCard
        row={r}
        name="Road Trip"
        when="x"
        schedule="Every 6 hours"
        onOpen={onOpen}
        primary={{ label: 'Sync & download', onClick: vi.fn() }}
        onMore={vi.fn()}
        onSchedule={onSchedule}
      />,
    );
    fireEvent.click(container.querySelector('.plc-schedule') as HTMLElement);
    expect(onSchedule).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('renders the display name, which may be a rename', () => {
    renderCard({}, 'My own title');
    expect(screen.getByText('My own title')).toBeInTheDocument();
  });
});

describe('the pill only speaks up when it has something to say', () => {
  function pillOf(scheduled: boolean, schedule: string) {
    const { container } = render(
      <PlaylistCard
        row={row()}
        name="Road Trip"
        when="x"
        schedule={schedule}
        scheduled={scheduled}
        onOpen={vi.fn()}
        primary={{ label: 'Sync & download', onClick: vi.fn() }}
        onMore={vi.fn()}
        onSchedule={vi.fn()}
      />,
    );
    return container.querySelector('.plc-schedule') as HTMLElement;
  }

  it('a scheduled playlist wears its cadence at rest', () => {
    expect(pillOf(true, 'Every 1 day').className).toContain('plc-schedule--set');
  });

  it('an unscheduled one goes quiet, so the scheduled few stand out', () => {
    // 24 of 37 cards reading "Not scheduled" made the most-repeated element on
    // the page the one carrying no information.
    expect(pillOf(false, 'Not scheduled').className).not.toContain('plc-schedule--set');
  });

  it('quiet is a STYLE, not a removal — it still opens the picker', () => {
    // Reserving the box is what stops the grid jogging under the cursor on
    // hover, and the control has to stay reachable either way.
    const onSchedule = vi.fn();
    const { container } = render(
      <PlaylistCard
        row={row()}
        name="Road Trip"
        when="x"
        schedule="Not scheduled"
        scheduled={false}
        onOpen={vi.fn()}
        primary={{ label: 'Sync & download', onClick: vi.fn() }}
        onMore={vi.fn()}
        onSchedule={onSchedule}
      />,
    );
    const pill = container.querySelector('.plc-schedule') as HTMLElement;
    expect(pill.tagName).toBe('BUTTON');
    expect(pill.textContent).toBe('Not scheduled');
    fireEvent.click(pill);
    expect(onSchedule).toHaveBeenCalledTimes(1);
  });

  it('"Can’t be scheduled" is quiet too — it is rare, and it is not news', () => {
    const { container } = renderCard();
    const inert = container.querySelector('.plc-schedule--inert') as HTMLElement;
    expect(inert.tagName).toBe('SPAN');
    expect(inert.className).not.toContain('plc-schedule--set');
  });
});

describe('when there is nothing honest to offer', () => {
  it('renders no primary button at all', () => {
    // A Beatport or file-backed mirror cannot be refreshed by the pipeline —
    // the endpoint rejects it outright — and a button that fails on click is
    // worse than no button.
    const onMore = vi.fn();
    const { container } = render(
      <PlaylistCard
        row={row({ source: 'beatport' })}
        name="Top 100"
        when="Mirrored 1h ago"
        schedule="Not scheduled"
        onOpen={vi.fn()}
        primary={null}
        onMore={onMore}
      />,
    );
    expect(container.querySelector('.plc-primary')).toBeNull();
    // The overflow still works, so rename/export/delete stay reachable.
    expect(container.querySelector('.plc-more')).not.toBeNull();
  });
});

/**
 * The card's stacking lives in its stylesheet, which jsdom cannot hit-test, so
 * the rules that keep every control reachable are asserted as TEXT.
 */
describe('the card stylesheet, as it actually declares it', () => {
  const css = readFileSync(
    resolve(process.cwd(), 'src/routes/sync/-ui/playlists.module.css'),
    'utf8',
  ).replace(/\/\*[\s\S]*?\*\//g, '');
  /** Any @media whose condition list mentions `hover: none`. */
  const touchBlocks = () =>
    [...css.matchAll(/@media[^{]*\(hover:\s*none\)[^{]*\{([\s\S]*?)\n\}/g)].map((m) => m[1]);

  it('reveals the hover controls on touch, where :hover never fires', () => {
    const block = touchBlocks().join('\n');
    for (const cls of ['.fab', '.prim', '.kebab']) expect(block).toContain(cls);
    expect(block).toMatch(/opacity:\s*1/);
  });

  it('lifts every control on the cover above the art', () => {
    // the controls sit ON the cover; without a z-index the art paints over them
    for (const cls of ['fab', 'prim', 'kebab']) {
      const found = new RegExp(`\\.${cls}\\s*\\{([^}]*)\\}`).exec(css);
      expect(found?.[1], cls).toMatch(/z-index:\s*2/);
    }
  });
});

describe('the meta line reports the discovery shortfall', () => {
  it('names what is MISSING, not what is present', () => {
    // A count you have to subtract to reach is not "what is wrong".
    expect(playlistCardMeta(row({ total_count: 86, discovered_count: 62 }), 'x')).toBe(
      '86 tracks · 24 not found',
    );
  });

  it('counts what is NOT DOWNLOADED once the matcher has checked every track', () => {
    // Verified against the real library: this playlist reads 47 of 50, which is
    // what the media server itself reports. The old SQL join said 18.
    expect(
      playlistCardMeta(
        row({
          total_count: 50,
          discovered_count: 50,
          in_library_count: 47,
          library_checked_count: 50,
        }),
        'synced 3h ago',
      ),
    ).toBe('50 tracks · 3 not downloaded');
  });

  it('says NOTHING about ownership for a playlist never checked', () => {
    // 0 owned with 0 checked means nobody looked. Reporting that as "you own
    // none of it" is exactly how the previous attempt went wrong.
    expect(
      playlistCardMeta(
        row({ total_count: 140, discovered_count: 140, in_library_count: 0 }),
        'synced 3h ago',
      ),
    ).toBe('140 tracks · synced 3h ago');
  });

  it('says nothing when only SOME tracks were checked', () => {
    expect(
      playlistCardMeta(
        row({
          total_count: 140,
          discovered_count: 140,
          in_library_count: 12,
          library_checked_count: 20,
        }),
        'synced 3h ago',
      ),
    ).toBe('140 tracks · synced 3h ago');
  });

  it('the discovery shortfall still wins the line', () => {
    // Sequential, not alternative — and "not found" is the earlier problem.
    expect(
      playlistCardMeta(
        row({
          total_count: 86,
          discovered_count: 62,
          in_library_count: 40,
          library_checked_count: 86,
        }),
        'x',
      ),
    ).toBe('86 tracks · 24 not found');
  });

  it('the button is Sync & download whatever the shortfall', () => {
    // It always called the same pipeline.run; three names for one action bought
    // nothing, and spent the card's only button on a count.
    expect(playlistCardPrimaryLabel(row({ total_count: 86, discovered_count: 62 }))).toBe(
      'Sync & download',
    );
  });
});

describe('the organize-by-playlist marker', () => {
  it('shows on a playlist in organize mode', () => {
    const { container } = render(
      <PlaylistCard
        row={row({ organize_by_playlist: true })}
        name="Road Trip"
        when="x"
        schedule="Not scheduled"
        onOpen={vi.fn()}
        onMore={vi.fn()}
      />,
    );
    expect(container.querySelector('.plc-organize')).not.toBeNull();
  });

  it('is absent otherwise, so it marks a mode rather than decorating every card', () => {
    const { container } = renderCard();
    expect(container.querySelector('.plc-organize')).toBeNull();
  });

  it('its tooltip names the WISHLIST effect, which the setting’s name does not imply', () => {
    // organize_by_playlist also sets skip_wishlist_add, so misses are
    // downloaded directly instead of landing on the wishlist.
    const { container } = render(
      <PlaylistCard
        row={row({ organize_by_playlist: true })}
        name="Road Trip"
        when="x"
        schedule="Not scheduled"
        onOpen={vi.fn()}
        onMore={vi.fn()}
      />,
    );
    expect(container.querySelector('.plc-organize')?.getAttribute('title')).toMatch(/wishlist/);
  });
});

describe('the name it syncs under', () => {
  it('says so when another mirror shares its name, and stays quiet otherwise', () => {
    const r = row();
    const { rerender } = render(
      <PlaylistCard
        row={r}
        name="Release Radar"
        syncsAs="Release Radar - ThomasClan"
        when="synced 3h ago"
        schedule="Every 6 hours"
        onOpen={vi.fn()}
        onMore={vi.fn()}
      />,
    );
    expect(screen.getByText('On the server as “Release Radar - ThomasClan”')).toBeInTheDocument();
    rerender(
      <PlaylistCard
        row={r}
        name="Release Radar"
        when="synced 3h ago"
        schedule="Every 6 hours"
        onOpen={vi.fn()}
        onMore={vi.fn()}
      />,
    );
    expect(document.querySelector('.plc-syncs-as')).toBeNull();
  });
});

describe('play from the card', () => {
  function renderPlayable(onPlay: () => Promise<unknown>, over: Partial<MirroredPlaylistRow> = {}) {
    const onOpen = vi.fn();
    render(
      <PlaylistCard
        row={row(over)}
        name="Road Trip"
        when="synced 3h ago"
        schedule="Every 6 hours"
        onOpen={onOpen}
        onMore={vi.fn()}
        onPlay={onPlay}
      />,
    );
    return { onOpen };
  }

  it('plays without also opening the card', () => {
    const onPlay = vi.fn(async () => {});
    const { onOpen } = renderPlayable(onPlay);
    fireEvent.click(screen.getByLabelText('Play Road Trip'));
    expect(onPlay).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('cannot be started twice while the playlist is still loading', async () => {
    let finish: () => void = () => {};
    const onPlay = vi.fn(() => new Promise<void>((r) => (finish = r)));
    renderPlayable(onPlay);
    const button = screen.getByLabelText('Play Road Trip');
    fireEvent.click(button);
    fireEvent.click(button);
    expect(onPlay).toHaveBeenCalledTimes(1);
    expect(button).toHaveAttribute('aria-busy', 'true');
    finish();
    await vi.waitFor(() => expect(button).not.toHaveAttribute('aria-busy'));
  });

  it('no onPlay, no button', () => {
    renderCard();
    expect(screen.queryByLabelText('Play Road Trip')).toBeNull();
  });

  it('Liked Songs wears the heart until it has a cover of its own', () => {
    const { container } = renderCard(
      { source: 'soulsync', source_playlist_id: 'liked' },
      'Liked Songs',
    );
    expect(container.querySelector('.playlist-liked-art')).not.toBeNull();
    const own = renderCard(
      { source: 'soulsync', source_playlist_id: 'liked', image_url: '/c.jpg' },
      'Liked Songs',
    );
    expect(own.container.querySelector('.playlist-liked-art')).toBeNull();
  });
});

describe('the all-good check', () => {
  it('marks a playlist that is discovered and downloaded', () => {
    const { container } = renderCard({ total_count: 25, discovered_count: 25 });
    expect(container.querySelector('.plc-ok')).not.toBeNull();
  });

  it('stays off when the line says tracks are not downloaded', () => {
    // a check beside "23 not downloaded" contradicts its own words
    const { container } = renderCard({
      total_count: 25,
      discovered_count: 25,
      library_checked_count: 25,
      in_library_count: 2,
    });
    expect(container.querySelector('.plc-status')?.textContent).toContain('23 not downloaded');
    expect(container.querySelector('.plc-ok')).toBeNull();
  });
});
