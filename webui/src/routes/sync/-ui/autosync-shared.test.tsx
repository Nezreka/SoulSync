/**
 * Differential tests for the chrome both Auto-Sync boards render —
 * auto-sync.js 197-203, 436-457, 764-812 and 1951-1976 / 979-1024.
 */

import { fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MirroredRow } from '../-sync.autosync';
import type { AutoSyncCardActions, AutoSyncSidebarBadge } from './autosync-shared';

import {
  AutoSyncBoardIntro,
  AutoSyncLane,
  AutoSyncPickMenu,
  AutoSyncScheduledCard,
  AutoSyncSidebar,
  AutoSyncSourceIcon,
} from './autosync-shared';

function makeActions(): AutoSyncCardActions {
  return { onRun: vi.fn(), onUnschedule: vi.fn(), onOrganizeChange: vi.fn() };
}

const row = (over: Partial<MirroredRow> = {}): MirroredRow => ({
  id: 1,
  name: 'Late Night',
  source: 'spotify',
  track_count: 42,
  ...over,
});

const unscheduled: AutoSyncSidebarBadge = {
  stateClass: '',
  assigned: 'Unscheduled',
  active: false,
};

afterEach(() => {
  delete window.playlistQualityProfileSelectHtml;
  delete window.hydratePlaylistQualityProfileSelects;
});

describe('AutoSyncSourceIcon (197-203)', () => {
  it('renders the brand logo with the source stamped on it', () => {
    const { container } = render(<AutoSyncSourceIcon source="tidal" />);
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toBe('/static/img/brands/tidal.svg');
    expect(img?.getAttribute('data-svc')).toBe('tidal');
    // Decorative: no alt text, hidden from the a11y tree.
    expect(img?.getAttribute('alt')).toBe('');
    expect(img?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders NOTHING for a source with no logo, not a broken image', () => {
    const { container } = render(<AutoSyncSourceIcon source="beatport" />);
    expect(container.querySelector('img')).toBeNull();
  });

  it('hides itself when the image fails to load', () => {
    const { container } = render(<AutoSyncSourceIcon source="spotify" />);
    const img = container.querySelector('img') as HTMLImageElement;
    expect(img.style.display).toBe('');
    fireEvent.error(img);
    expect(img.style.display).toBe('none');
  });
});

describe('AutoSyncScheduledCard (1951-1976 / 979-1024)', () => {
  const renderCard = (
    playlist: MirroredRow,
    over: Partial<React.ComponentProps<typeof AutoSyncScheduledCard>> = {},
    actions = makeActions(),
  ) => ({
    actions,
    ...render(
      <AutoSyncScheduledCard
        playlist={playlist}
        enabled
        timing={<span>Every 24 hours</span>}
        history={[]}
        actions={actions}
        unscheduleTitle="Remove this Auto-Sync schedule"
        {...over}
      />,
    ),
  });

  it('shows the source and the track count', () => {
    const { container } = renderCard(row());
    expect(container.querySelector('.auto-sync-scheduled-meta')?.textContent).toBe(
      'Spotify · 42 tracks',
    );
  });

  it('renders whatever timing line the board hands it', () => {
    const { container } = renderCard(row(), {
      timing: (
        <>
          <span>Mon @ 09:00</span>
          <small>Europe/London</small>
        </>
      ),
    });
    expect(container.querySelector('.auto-sync-scheduled-timing')?.textContent).toBe(
      'Mon @ 09:00Europe/London',
    );
  });

  it('marks a DISABLED schedule but not an enabled one', () => {
    const { container: on } = renderCard(row());
    expect(on.querySelector('.auto-sync-scheduled-card')?.className).not.toContain('disabled');
    const { container: off } = renderCard(row(), { enabled: false });
    expect(off.querySelector('.auto-sync-scheduled-card')?.className).toContain('disabled');
  });

  it("carries the board's extra class and unschedule wording", () => {
    const { container } = renderCard(row(), {
      extraClass: 'auto-sync-weekly-card',
      unscheduleTitle: 'Remove this weekly schedule',
    });
    expect(container.querySelector('.auto-sync-scheduled-card')?.className).toContain(
      'auto-sync-weekly-card',
    );
    const buttons = container.querySelectorAll('.auto-sync-scheduled-actions button');
    expect(buttons[1].getAttribute('title')).toBe('Remove this weekly schedule');
  });

  it('disables Run now while the pipeline is running, and relabels it', () => {
    const { container } = renderCard(row({ pipeline_state: { status: 'running' } }));
    const btn = container.querySelector('button.run') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toBe('Running');
  });

  it('fires run and unschedule with the playlist id', () => {
    const { container, actions } = renderCard(row({ id: 91 }));
    fireEvent.click(container.querySelector('button.run') as HTMLElement);
    expect(actions.onRun).toHaveBeenCalledWith(91);
    const buttons = container.querySelectorAll('.auto-sync-scheduled-actions button');
    fireEvent.click(buttons[1]);
    expect(actions.onUnschedule).toHaveBeenCalledWith(91);
  });

  it('opens the card click only from the card, never from its buttons (1019)', () => {
    const onCardClick = vi.fn();
    const { container } = renderCard(row(), { onCardClick });
    fireEvent.click(container.querySelector('.auto-sync-scheduled-card') as HTMLElement);
    expect(onCardClick).toHaveBeenCalledTimes(1);
    // Both action buttons stopPropagation, so neither reopens the editor.
    container.querySelectorAll('.auto-sync-scheduled-actions button').forEach((b) => {
      fireEvent.click(b);
    });
    expect(onCardClick).toHaveBeenCalledTimes(1);
  });

  it('keeps folder + quality behind ⋯ until asked, without opening the card', () => {
    // the board was a wall of checkboxes and dropdowns, one set per card.
    // they're still one click away, and the ⋯ must not reopen the weekly editor.
    const onCardClick = vi.fn();
    const { container } = renderCard(row(), { onCardClick });
    const panel = () => container.querySelector('.auto-sync-card-settings') as HTMLElement;
    expect(panel().className).not.toContain('open');
    // still mounted, so the quality select hydrates exactly as before
    expect(panel().querySelector('.auto-sync-organize-toggle')).not.toBeNull();
    const more = container.querySelector('.auto-sync-card-more') as HTMLElement;
    fireEvent.click(more);
    expect(panel().className).toContain('open');
    expect(more.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(more);
    expect(panel().className).not.toContain('open');
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it('offers no folder / quality settings on a personalized row (Daily Mix)', () => {
    // there's no mirrored playlist to save them onto yet. the quality select
    // used to render anyway and sit on "Loading…" forever.
    window.playlistQualityProfileSelectHtml = () => '<select></select>';
    const { container } = renderCard(row({ id: -1, _personalized: true }));
    expect(container.querySelector('.auto-sync-card-more')).toBeNull();
    expect(container.querySelector('.auto-sync-card-settings')).toBeNull();
    expect(container.querySelector('select')).toBeNull();
    // run and unschedule are untouched
    expect(container.querySelector('button.run')).not.toBeNull();
    expect(container.querySelectorAll('.auto-sync-scheduled-actions button')).toHaveLength(2);
  });

  it('keeps a filled quality select filled when the card re-renders', () => {
    // boulder, sept 30: every card sat on "Loading…". the vanilla hydrate fills
    // the select in place; react 19 rewrote dangerouslySetInnerHTML on any
    // re-render with a new {__html} object, putting it back to empty. opening
    // ⋯ or starting a drag re-renders, and hydrate never runs again.
    window.playlistQualityProfileSelectHtml = () =>
      '<select class="qp"><option value="">Loading…</option></select>';
    window.hydratePlaylistQualityProfileSelects = vi.fn(async () => {});
    const { container } = renderCard(row());
    const select = () => container.querySelector('select.qp') as HTMLSelectElement;
    // what the vanilla hydrate does
    select().innerHTML = '<option value="1">Default (Default)</option>';
    fireEvent.click(container.querySelector('.auto-sync-card-more') as HTMLElement);
    expect(select().options[0].text).toBe('Default (Default)');
  });

  it('leaves the organize toggle from reaching the card click', () => {
    const onCardClick = vi.fn();
    const { container } = renderCard(row(), { onCardClick });
    fireEvent.click(container.querySelector('.auto-sync-organize-toggle') as HTMLElement);
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it('shows no health marker when the recent runs are clean', () => {
    const { container } = renderCard(row({ id: 5 }), {
      history: [
        { playlist_id: 5, status: 'success' },
        { playlist_id: 5, status: 'success' },
      ],
    });
    expect(container.querySelector('.auto-sync-scheduled-health')).toBeNull();
    expect(container.querySelector('.auto-sync-scheduled-card')?.className).not.toContain(
      'warning',
    );
  });

  it('warns on a single failure and goes red on three (1978-1996)', () => {
    const { container: warn } = renderCard(row({ id: 5 }), {
      history: [
        { playlist_id: 5, status: 'error' },
        { playlist_id: 5, status: 'success' },
      ],
    });
    expect(warn.querySelector('.auto-sync-scheduled-health')?.textContent).toBe('⚠');
    expect(warn.querySelector('.auto-sync-scheduled-health')?.getAttribute('title')).toBe(
      '1 of last 2 runs failed',
    );
    expect(warn.querySelector('.auto-sync-scheduled-card')?.className).toContain('warning');

    const { container: fail } = renderCard(row({ id: 5 }), {
      history: [
        { playlist_id: 5, status: 'error' },
        { playlist_id: 5, status: 'skipped' },
        { playlist_id: 5, status: 'error' },
      ],
    });
    expect(fail.querySelector('.auto-sync-scheduled-health')?.textContent).toBe('!');
    expect(fail.querySelector('.auto-sync-scheduled-card')?.className).toContain('failing');
  });

  it('looks only at the THREE most recent runs (1985)', () => {
    const { container } = renderCard(row({ id: 5 }), {
      history: [
        { playlist_id: 5, status: 'success' },
        { playlist_id: 5, status: 'success' },
        { playlist_id: 5, status: 'success' },
        { playlist_id: 5, status: 'error' },
      ],
    });
    expect(container.querySelector('.auto-sync-scheduled-health')).toBeNull();
  });

  it("ignores ANOTHER playlist's failures", () => {
    const { container } = renderCard(row({ id: 5 }), {
      history: [
        { playlist_id: 9, status: 'error' },
        { playlist_id: 9, status: 'error' },
        { playlist_id: 9, status: 'error' },
      ],
    });
    expect(container.querySelector('.auto-sync-scheduled-health')).toBeNull();
  });

  it('reflects and reports the organize-by-playlist preference', () => {
    const { container, actions } = renderCard(row({ organize_by_playlist: true }));
    const box = container.querySelector('.auto-sync-organize-toggle input') as HTMLInputElement;
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(actions.onOrganizeChange).toHaveBeenCalledWith(1, false);
  });

  it('ignores a non-callable global rather than throwing (typeof guard, 1927)', () => {
    (
      window as unknown as { playlistQualityProfileSelectHtml: unknown }
    ).playlistQualityProfileSelectHtml = '<select class="qp-select"></select>';
    const { container } = renderCard(row());
    expect(container.querySelector('.qp-select')).toBeNull();
  });

  it('HYDRATES the select it renders — the other half of the seam', () => {
    // playlistQualityProfileSelectHtml emits an EMPTY select;
    // hydratePlaylistQualityProfileSelects fills it. Miss the second and the
    // control renders forever empty without throwing — the silent failure.
    window.playlistQualityProfileSelectHtml = () => '<select class="qp-select"></select>';
    window.hydratePlaylistQualityProfileSelects = vi.fn();
    renderCard(row({ source_playlist_id: 'abc', source: 'tidal', quality_profile_id: 7 }));
    expect(window.hydratePlaylistQualityProfileSelects).toHaveBeenCalledWith('abc', 'tidal', 7);
  });

  it('does not hydrate when there is no select to hydrate', () => {
    window.hydratePlaylistQualityProfileSelects = vi.fn();
    renderCard(row());
    expect(window.hydratePlaylistQualityProfileSelects).not.toHaveBeenCalled();
  });

  it('survives the hydrator being absent while the renderer is present', () => {
    window.playlistQualityProfileSelectHtml = () => '<select class="qp-select"></select>';
    const { container } = renderCard(row());
    expect(container.querySelector('.qp-select')).not.toBeNull();
  });

  it('renders the quality-profile seam only when the shared global exists', () => {
    const { container: without } = renderCard(row());
    expect(without.querySelector('.qp-select')).toBeNull();

    window.playlistQualityProfileSelectHtml = (id, source, compact) =>
      `<select class="qp-select" data-id="${id}" data-source="${source}" data-compact="${compact}"></select>`;
    const { container: with_ } = renderCard(row({ source_playlist_id: 'abc', source: 'tidal' }));
    const sel = with_.querySelector('.qp-select');
    expect(sel?.getAttribute('data-id')).toBe('abc');
    expect(sel?.getAttribute('data-source')).toBe('tidal');
    // 1928 passes `true` for the compact form.
    expect(sel?.getAttribute('data-compact')).toBe('true');
  });
});

describe('AutoSyncSidebar (764-812 / 892-914)', () => {
  const renderSidebar = (over: Partial<React.ComponentProps<typeof AutoSyncSidebar>> = {}) =>
    render(
      <AutoSyncSidebar
        groups={[{ source: 'spotify', rows: [row()] }]}
        unavailable={[]}
        badgeFor={() => unscheduled}
        filter=""
        onFilterChange={vi.fn()}
        expandedKinds={new Set()}
        onToggleKind={vi.fn()}
        {...over}
      />,
    );

  it('omits the Bulk button when no bulk handler is supplied (the weekly board)', () => {
    const { container } = renderSidebar();
    expect(container.querySelector('.auto-sync-source-bulk-btn')).toBeNull();
    const { container: withBulk } = renderSidebar({ onBulkMenu: vi.fn() });
    expect(withBulk.querySelector('.auto-sync-source-bulk-btn')).not.toBeNull();
  });

  it('applies whatever state class the board computes', () => {
    const { container } = renderSidebar({
      badgeFor: () => ({
        stateClass: 'scheduled-elsewhere',
        assigned: 'Hourly (every 8 hours)',
        active: true,
      }),
    });
    expect(container.querySelector('.auto-sync-playlist')?.className).toContain(
      'scheduled-elsewhere',
    );
    expect(container.querySelector('.auto-sync-playlist-meta')?.textContent).toBe(
      '42 tracks · Hourly (every 8 hours)',
    );
  });
});

describe('AutoSyncLane (809-826 / 933-949)', () => {
  const renderLane = (over: Partial<React.ComponentProps<typeof AutoSyncLane>> = {}) =>
    render(
      <AutoSyncLane
        title="Daily"
        subtitle="Every day"
        count={null}
        dataAttrs={{ 'data-hours': 24 }}
        hint="Drag a playlist here"
        onDropPlaylist={vi.fn()}
        {...over}
      >
        <div className="child-card">card</div>
      </AutoSyncLane>,
    );

  it('spreads whatever data attribute the board keys it by', () => {
    const { container } = renderLane({ dataAttrs: { 'data-day': 'monday' } });
    expect(container.querySelector('[data-day="monday"]')).not.toBeNull();
  });

  it('swaps the hint for the children once the lane is filled', () => {
    const { container: empty } = renderLane();
    expect(empty.querySelector('.child-card')).toBeNull();
    expect(empty.querySelector('.auto-sync-lane-hint')?.textContent).toBe('+ Drag a playlist here');

    const { container: filled } = renderLane({ count: 1 });
    expect(filled.querySelector('.child-card')).not.toBeNull();
    expect(filled.querySelector('.auto-sync-lane-hint')).toBeNull();
  });

  it('an empty lane carries its hint as a tooltip, since it shows as a chip', () => {
    // empty intervals render as small "+ 1h" drop chips now, the hint text is
    // hidden, so hovering the chip has to still say what dropping does
    const { container: empty } = renderLane();
    expect(empty.querySelector('.auto-sync-lane')?.getAttribute('title')).toBe(
      'Drag a playlist here',
    );
    const { container: filled } = renderLane({ count: 1 });
    expect(filled.querySelector('.auto-sync-lane')?.hasAttribute('title')).toBe(false);
  });
});

describe('AutoSyncBoardIntro (826-834 / 950-960)', () => {
  it('renders the board-specific copy, and carries no Refresh of its own', () => {
    const onRefresh = vi.fn();
    const { container } = render(
      <AutoSyncBoardIntro heading="Drag playlists onto a day" blurb="blurb" />,
    );
    expect(container.querySelector('strong')?.textContent).toBe('Drag playlists onto a day');
    // This intro renders on BOTH boards, so its button was two of the four
    // duplicate Refreshes on its own. The modal header owns the only one now.
    expect(container.querySelector('button')).toBeNull();
    expect(onRefresh).not.toHaveBeenCalled();
  });
});

describe('tap-to-schedule (NEW — no vanilla counterpart)', () => {
  const renderSidebar = (over: Partial<React.ComponentProps<typeof AutoSyncSidebar>> = {}) =>
    render(
      <AutoSyncSidebar
        groups={[{ source: 'spotify', rows: [row()] }]}
        unavailable={[]}
        badgeFor={() => unscheduled}
        filter=""
        onFilterChange={vi.fn()}
        expandedKinds={new Set()}
        onToggleKind={vi.fn()}
        {...over}
      />,
    );

  it('sidebar cards are inert until a pick handler is supplied', () => {
    const { container } = renderSidebar();
    const card = container.querySelector('.auto-sync-playlist');
    expect(card?.className).not.toContain('pickable');
    expect(card?.getAttribute('role')).toBeNull();
    fireEvent.click(card as HTMLElement);
  });

  it('a supplied pick handler makes the card a button and reports the tap', () => {
    const onPickPlaylist = vi.fn();
    const { container } = renderSidebar({ onPickPlaylist });
    const card = container.querySelector('.auto-sync-playlist') as HTMLElement;
    expect(card.className).toContain('pickable');
    expect(card.getAttribute('role')).toBe('button');
    fireEvent.click(card);
    expect(onPickPlaylist).toHaveBeenCalledTimes(1);
    expect(onPickPlaylist.mock.calls[0][1]).toMatchObject({ id: 1, name: 'Late Night' });
    // Enter activates it too, for keyboards and switch control.
    fireEvent.keyDown(card, { key: 'Enter' });
    expect(onPickPlaylist).toHaveBeenCalledTimes(2);
  });

  const renderMenu = (over: Partial<React.ComponentProps<typeof AutoSyncPickMenu>> = {}) =>
    render(
      <AutoSyncPickMenu
        playlist={row()}
        anchor={{ top: 100, left: 40 }}
        onSchedule={vi.fn()}
        onClose={vi.fn()}
        {...over}
      />,
    );

  it('the pick menu names the playlist and its current state', () => {
    const { container } = renderMenu();
    expect(container.querySelector('.auto-sync-pick-menu-name')?.textContent).toBe('Late Night');
    expect(container.querySelector('.auto-sync-bulk-menu-title')?.textContent).toBe(
      'Not scheduled',
    );
    expect(container.querySelector('.auto-sync-bulk-menu-unschedule')).toBeNull();
  });

  it('picking a bucket schedules it; the current bucket is marked', () => {
    const onSchedule = vi.fn();
    const onUnschedule = vi.fn();
    const { container } = renderMenu({ currentHours: 4, onSchedule, onUnschedule });
    expect(container.querySelector('.auto-sync-bulk-menu-title')?.textContent).toBe(
      'Scheduled · every 4 hours',
    );
    const buckets = [...container.querySelectorAll('.auto-sync-bulk-menu-buckets button')];
    expect(buckets.map((b) => b.textContent)).toContain('Every 4 hours');
    expect(buckets.find((b) => b.textContent === 'Every 4 hours')?.className).toContain('current');
    fireEvent.click(buckets.find((b) => b.textContent === 'Every 1 hour') as HTMLElement);
    expect(onSchedule).toHaveBeenCalledWith(1);
    // Unschedule exists only because a schedule does.
    fireEvent.click(container.querySelector('.auto-sync-bulk-menu-unschedule') as HTMLElement);
    expect(onUnschedule).toHaveBeenCalledTimes(1);
  });

  it('the custom interval validates through the bulk parser', () => {
    const onSchedule = vi.fn();
    const { container } = renderMenu({ onSchedule });
    fireEvent.click(container.querySelector('.auto-sync-bulk-menu-custom') as HTMLElement);
    const input = container.querySelector('input[type="number"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '0' } });
    fireEvent.click(
      [...container.querySelectorAll('button')].find(
        (b) => b.textContent === 'Schedule',
      ) as HTMLElement,
    );
    expect(onSchedule).not.toHaveBeenCalled();
    expect(container.querySelector('.auto-sync-bulk-menu-error')?.textContent).toBeTruthy();
    fireEvent.change(input, { target: { value: '36' } });
    fireEvent.click(
      [...container.querySelectorAll('button')].find(
        (b) => b.textContent === 'Schedule',
      ) as HTMLElement,
    );
    expect(onSchedule).toHaveBeenCalledWith(36);
  });

  it('Escape closes the menu without reaching the modal behind it', () => {
    const onClose = vi.fn();
    renderMenu({ onClose });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
