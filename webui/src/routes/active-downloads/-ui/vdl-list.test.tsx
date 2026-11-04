/**
 * The video download cards: one action per row picked by state, season
 * groups that read like an episode list, and the detail sheet (art, cast,
 * steps, release facts).
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { VdlDownload, VdlMeta } from '../-vdl.types';
import type { VdlCardActions } from './vdl-list';

import { VdlList } from './vdl-list';

vi.mock('@/platform/shell/route-controllers', () => ({
  useProfile: () => ({ profileId: 1, isAdmin: true }),
}));

afterEach(cleanup);

const dl = (over: Partial<VdlDownload> = {}): VdlDownload => ({
  id: 1,
  kind: 'movie',
  title: 'Dune: Part Two',
  year: 2024,
  status: 'downloading',
  progress: 42,
  quality_label: '2160p WEB-DL',
  size_bytes: 18_000_000_000,
  speed_bps: 12_500_000,
  eta_seconds: 840,
  username: 'cinephile',
  release_title: 'Dune.Part.Two.2024.2160p.WEB-DL',
  media_id: 123,
  media_source: 'tmdb',
  search_ctx: '{}',
  ...over,
});

const actions = (): VdlCardActions => ({
  onCancel: vi.fn(),
  onRetry: vi.fn(),
  onBlock: vi.fn(),
  onRemove: vi.fn(),
  onToggle: vi.fn(),
});

function list(rows: VdlDownload[], over: Record<string, unknown> = {}) {
  const cardActions = actions();
  const view = render(
    <VdlList
      downloads={rows}
      filter="all"
      loaded
      expanded={new Set<number>()}
      collapsedGroups={new Set<string>()}
      metaFor={() => undefined}
      ensureMeta={() => {}}
      onToggleGroup={() => {}}
      cardActions={cardActions}
      onCancelGroup={() => {}}
      {...over}
    />,
  );
  return { ...view, cardActions };
}

describe('the row', () => {
  it('shows progress, speed and time left while downloading, cancel on the side', () => {
    const { container, cardActions } = list([dl()]);
    expect(container.querySelector('.dlc-meta')?.textContent).toContain('42%');
    expect(container.querySelector('.dlc-speed')?.textContent).toBe('12.5 MB/s');
    expect(container.querySelector('.dlc-sub')?.textContent).toContain('cinephile');
    fireEvent.click(container.querySelector('.dlc-cancel') as HTMLElement);
    expect(cardActions.onCancel).toHaveBeenCalledWith(1);
    // a poster keeps its art; no ring drawn over a 2:3 image
    expect(container.querySelector('.dlc-ring')).toBeNull();
    expect(container.querySelector('.dlc-rowbar i')?.getAttribute('style')).toContain('42%');
  });

  it('a downloading row says so in words and is marked live', () => {
    const { container } = list([dl()]);
    const row = container.querySelector('.dlc-row')!;
    expect(row.className).toContain('dlc-row-live');
    expect(container.querySelector('.dlc-live-pill')?.textContent).toBe('Downloading · 42%');
  });

  it('a download that has not moved yet reads as starting, not stalled', () => {
    // 0% and no speed used to show "0 MB of 0.9 GB" and a lone "0%", with no bar
    const { container } = list([dl({ progress: 0, speed_bps: 0, eta_seconds: null })]);
    expect(container.querySelector('.dlc-row')!.className).toContain('dlc-row-live');
    expect(container.querySelector('.dlc-live-pill')?.textContent).toBe('Starting');
    expect(container.querySelector('.dlc-sub')?.textContent).toBe('Connecting · cinephile');
    expect(container.querySelector('.dlc-sub')?.textContent).not.toContain('MB of');
    // the sweep says it's alive
    expect(container.querySelector('.dlc-rowbar-indet')).toBeTruthy();
  });

  it('only a downloading row is marked live', () => {
    for (const status of ['queued', 'searching', 'importing', 'completed', 'failed', 'cancelled']) {
      cleanup();
      const { container } = list([dl({ status })]);
      expect(container.querySelector('.dlc-row')!.className, status).not.toContain('dlc-row-live');
      expect(container.querySelector('.dlc-live-pill'), status).toBeNull();
    }
  });

  it('gives a failed row Retry as its one action and says why once', () => {
    const { container, cardActions } = list([
      dl({ status: 'failed', error: 'No seeders after 3 attempts', attempts: 3 }),
    ]);
    expect(container.querySelector('.dlc-sub-bad')?.textContent).toBe(
      'No seeders after 3 attempts',
    );
    expect(container.querySelector('.dlc-state')).toBeNull();
    const primary = container.querySelectorAll('.dlc-acts .dlc-btn-primary');
    expect(primary).toHaveLength(1);
    fireEvent.click(primary[0]);
    expect(cardActions.onRetry).toHaveBeenCalledWith(1);
  });

  it('gives an import failure Import instead of Retry', () => {
    const { container } = list([dl({ status: 'import_failed', error: 'Could not place file' })]);
    const primary = container.querySelectorAll('.dlc-acts .dlc-btn-primary');
    expect(primary).toHaveLength(1);
    expect(primary[0].textContent).toContain('Import');
  });

  it('keeps block in the menu, marked as the dangerous one', () => {
    const { container, cardActions } = list([
      dl({ status: 'failed', error: 'x', filename: 'Dune.mkv' }),
    ]);
    fireEvent.click(container.querySelector('.dlc-more') as HTMLElement);
    const block = screen.getByRole('menuitem', { name: 'Block release and retry' });
    expect(block.className).toContain('danger');
    fireEvent.click(block);
    expect(cardActions.onBlock).toHaveBeenCalled();
  });

  it('offers Remove from list on a failed row, without retrying', () => {
    const { container, cardActions } = list([
      dl({ id: 7, status: 'failed', error: 'x', filename: 'Dune.mkv' }),
    ]);
    fireEvent.click(container.querySelector('.dlc-more') as HTMLElement);
    const remove = screen.getByRole('menuitem', { name: 'Remove from list' });
    expect(remove.className).not.toContain('danger');
    fireEvent.click(remove);
    expect(cardActions.onRemove).toHaveBeenCalledWith(7);
    expect(cardActions.onRetry).not.toHaveBeenCalled();
    expect(cardActions.onBlock).not.toHaveBeenCalled();
  });

  it('offers Remove from list on a cancelled row', () => {
    const { container } = list([dl({ status: 'cancelled', error: 'Cancelled' })]);
    fireEvent.click(container.querySelector('.dlc-more') as HTMLElement);
    expect(screen.getByRole('menuitem', { name: 'Remove from list' })).not.toBeNull();
  });

  it('keeps Remove from list off rows Manual import or the queue still needs', () => {
    for (const status of ['import_failed', 'completed', 'downloading', 'queued']) {
      const { container } = list([dl({ status, error: 'x', dest_path: '/d/x.mkv' })]);
      const more = container.querySelector('.dlc-more') as HTMLElement | null;
      if (more) fireEvent.click(more);
      expect(screen.queryByRole('menuitem', { name: 'Remove from list' }), status).toBeNull();
      cleanup();
    }
  });

  it('marks a below-cutoff copy as on upgrade watch', () => {
    const { container } = list([dl({ status: 'completed', upgrade_watch: true })]);
    expect(container.querySelector('.dlc-tag-watch')?.textContent).toBe('upgrade watch');
  });

  it('toggles from a row click but not from a control', () => {
    const { container, cardActions } = list([dl()]);
    fireEvent.click(container.querySelector('.dlc-title') as HTMLElement);
    expect(cardActions.onToggle).toHaveBeenCalledWith(1);
    fireEvent.click(container.querySelector('.dlc-cancel') as HTMLElement);
    expect(cardActions.onToggle).toHaveBeenCalledTimes(1);
  });
});

describe('season groups', () => {
  const ep = (id: number, episode: number, over: Partial<VdlDownload> = {}) =>
    dl({
      id,
      kind: 'show',
      title: 'The Bear',
      media_id: 456,
      search_ctx: JSON.stringify({ season: 3, episode }),
      ...over,
    });

  it('heads the group with the show and season and numbers the episodes', () => {
    const { container } = list([
      ep(1, 1, { status: 'completed' }),
      ep(2, 2),
      ep(3, 3, { status: 'failed', error: 'stalled' }),
    ]);
    expect(container.querySelector('.dlc-eyebrow')?.textContent).toBe('TV · Season 3');
    expect(container.querySelector('.dlc-gtitle')?.textContent).toContain('The Bear');
    expect(container.querySelector('.dlc-gsub')?.textContent).toContain('1 of 3 imported');
    expect(container.querySelector('.dlc-gbad')?.textContent).toBe('1 failed');
    const nums = [...container.querySelectorAll('.dlc-group-rows .dlc-num')].map(
      (n) => n.textContent,
    );
    expect(nums).toEqual(['1', '2', '3']);
    expect(container.querySelector('.dlc-group-rows .dlc-title')?.textContent).toBe('Episode 1');
  });

  it('cancels every live episode from the group menu', () => {
    const onCancelGroup = vi.fn();
    const { container } = list([ep(1, 1), ep(2, 2), ep(3, 3, { status: 'completed' })], {
      onCancelGroup,
    });
    fireEvent.click(container.querySelector('.dlc-ghead .dlc-more') as HTMLElement);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Cancel all in this season' }));
    expect(onCancelGroup).toHaveBeenCalledWith([1, 2]);
  });

  it('standalone rows share one surface between groups', () => {
    const { container } = list([dl({ id: 7 }), dl({ id: 8, title: 'Oppenheimer' })]);
    const surfaces = container.querySelectorAll('[data-vdl-list] > .dlc-list');
    expect(surfaces).toHaveLength(1);
    expect(surfaces[0].querySelectorAll('.dlc-row')).toHaveLength(2);
  });
});

describe('the sheet', () => {
  const meta: VdlMeta = {
    title: 'Dune: Part Two',
    overview: 'Paul unites with the Fremen.',
    year: 2024,
    director: 'Denis Villeneuve',
    genres: ['Science Fiction'],
    trailer_url: 'https://example.com/t',
    cast: [{ name: 'Zendaya', character: 'Chani', photo: null }],
  };

  it('keeps the art, story and cast, then the steps and the release', () => {
    const { container } = list([dl({ created_at: '2026-10-20 10:00:00' })], {
      expanded: new Set([1]),
      metaFor: () => meta,
    });
    const sheet = container.querySelector('.dlc-sheet') as HTMLElement;
    expect(sheet.textContent).toContain('Paul unites with the Fremen.');
    expect(screen.getByText('Zendaya')).not.toBeNull();
    expect(screen.getByText('Chani')).not.toBeNull();
    expect(sheet.querySelector('.dlc-trailer')?.getAttribute('href')).toBe('https://example.com/t');
    expect(sheet.querySelector('.dlc-step-now')?.textContent).toContain('Downloading');
    expect(sheet.querySelector('.dlc-facts')?.textContent).toContain('Denis Villeneuve');
    expect(sheet.querySelector('.dlc-facts')?.textContent).toContain(
      'Dune.Part.Two.2024.2160p.WEB-DL',
    );
  });

  it('offers copy on a finished path', () => {
    const { container } = list([dl({ status: 'completed', dest_path: '/media/Dune.mkv' })], {
      expanded: new Set([1]),
      metaFor: () => meta,
    });
    expect(container.querySelector('.dlc-copy')).not.toBeNull();
  });
});

describe('youtube rows', () => {
  // the download panel saves a youtube grab with kind 'movie'. the card has to
  // go by the source, or it offers "Open movie" for a youtube video
  const yt = (over: Partial<VdlDownload> = {}) =>
    dl({
      kind: 'movie',
      title: 'How Pixar Lights a Scene',
      status: 'completed',
      media_id: 'abc123xyz00',
      media_source: 'youtube',
      search_ctx: JSON.stringify({ channel: 'Pixar', channel_id: 'UCpixar' }),
      ...over,
    });

  it('opens the channel as the show, never as a movie', () => {
    const open = vi.fn();
    window.SoulSyncVideo = { ...window.SoulSyncVideo, openDetail: open } as never;
    const { container } = list([yt()], { expanded: new Set([1]), metaFor: () => ({}) });
    expect(container.querySelector('.dlc-row')?.getAttribute('data-vtype')).toBe('youtube');
    expect(screen.queryByText('Open movie')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Open show' }));
    expect(open).toHaveBeenCalledWith({ kind: 'channel', id: 'UCpixar', source: 'youtube' });
  });

  it('falls back to youtube itself when the channel is unknown', () => {
    list([yt({ search_ctx: '{}' })], { expanded: new Set([1]), metaFor: () => ({}) });
    expect(screen.getByRole('button', { name: 'Open on YouTube' })).not.toBeNull();
  });
});
