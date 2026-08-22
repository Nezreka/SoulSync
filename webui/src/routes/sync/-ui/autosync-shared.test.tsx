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
