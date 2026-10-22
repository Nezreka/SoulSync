import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { openDownloadTaskInspector } from '@/features/downloads/inspector-modal';

import type { AdlDownload } from '../-adl.types';

import { AdlHeader } from './adl-header';
import { BatchFilterBanner } from './adl-list';
import { AdlRow } from './adl-row';

vi.mock('@/features/downloads/inspector-modal', () => ({ openDownloadTaskInspector: vi.fn() }));

afterEach(cleanup);

const row = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({
    task_id: 't1',
    title: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW',
    artwork: '',
    status: 'downloading',
    progress: 50,
    error: null,
    verification_status: null,
    batch_id: '',
    batch_name: '',
    batch_source: '',
    playlist_id: 'p1',
    track_index: 0,
    batch_total: 1,
    timestamp: 0,
    priority: 0,
    quality: '',
    is_persistent_history: false,
    ...over,
  }) as AdlDownload;

describe('AdlRow', () => {
  const open = (container: HTMLElement) =>
    fireEvent.click(container.querySelector('.dlc-row') as HTMLElement);

  it('renders title, sub line and the status class', () => {
    const { container } = render(<AdlRow dl={row({ status: 'queued' })} />);
    const el = container.querySelector('.dlc-row') as HTMLElement;
    expect(el.className).toBe('dlc-row dlc-row-queued');
    expect(el.getAttribute('data-task-id')).toBe('t1');
    expect(container.querySelector('.dlc-title')?.textContent).toBe('Xtal');
    expect(container.querySelector('.dlc-sub')?.textContent).toBe('Aphex Twin · SAW');
  });

  it('falls back for an untitled row', () => {
    const { container } = render(<AdlRow dl={row({ title: '' })} />);
    expect(container.querySelector('.dlc-title')?.textContent).toBe('Unknown Track');
  });

  it('puts the batch position in the sheet, from batch_position only', () => {
    const { container } = render(
      <AdlRow
        dl={row({
          status: 'queued',
          batch_name: 'My Batch',
          batch_total: 19,
          track_index: 2,
          batch_position: 3,
        })}
      />,
    );
    open(container);
    expect(container.querySelector('.dlc-facts')?.textContent).toContain(
      'My Batch · track 3 of 19',
    );
    // #1183: track_index must NEVER be the ordinal. without a server
    // position the label is left out rather than wrong
    cleanup();
    const noPos = render(
      <AdlRow
        dl={row({ status: 'queued', batch_name: 'Wishlist', batch_total: 2, track_index: 4929 })}
      />,
    );
    open(noPos.container);
    const facts = noPos.container.querySelector('.dlc-facts')?.textContent ?? '';
    expect(facts).toContain('Wishlist');
    expect(facts).not.toContain('4929');
  });

  it('shows a failed row its error instead of a second "Failed"', () => {
    const { container } = render(<AdlRow dl={row({ status: 'failed', error: 'peer offline' })} />);
    expect(container.querySelector('.dlc-sub-bad')?.textContent).toBe('peer offline');
    expect(container.querySelector('.dlc-state')).toBeNull();
    cleanup();
    const clean = render(<AdlRow dl={row({ status: 'failed' })} />);
    expect(clean.container.querySelector('.dlc-state')?.textContent).toBe('Failed');
  });

  it('draws a ring while downloading and a spinner while searching', () => {
    const { container } = render(<AdlRow dl={row({ status: 'downloading', progress: 40 })} />);
    expect(container.querySelector('.dlc-ring')).not.toBeNull();
    expect(container.querySelector('.dlc-rowbar i')?.getAttribute('style')).toContain('40%');
    cleanup();
    const search = render(<AdlRow dl={row({ status: 'searching' })} />);
    expect(search.container.querySelector('.dlc-spin')).not.toBeNull();
    expect(search.container.querySelector('.dlc-ring')).toBeNull();
    cleanup();
    const done = render(<AdlRow dl={row({ status: 'completed' })} />);
    expect(done.container.querySelector('.dlc-spin, .dlc-ring')).toBeNull();
  });

  it('shows the verification shield and quality tag on a completed row', () => {
    const { container } = render(
      <AdlRow
        dl={row({
          status: 'completed',
          verification_status: 'human_verified',
          quality: 'FLAC 16/44',
        })}
      />,
    );
    expect(container.querySelector('.dlc-shield-human')).not.toBeNull();
    expect(container.querySelector('.dlc-tag')?.textContent).toBe('FLAC 16/44');
    cleanup();
    const unv = render(
      <AdlRow dl={row({ status: 'completed', verification_status: 'unverified' })} />,
    );
    expect(unv.container.querySelector('.dlc-shield-warn')).not.toBeNull();
  });

  it('turns a podcast mime type into a format', () => {
    const { container } = render(
      <AdlRow dl={row({ status: 'completed', quality: 'audio/mpeg' })} />,
    );
    expect(container.querySelector('.dlc-tag')?.textContent).toBe('MP3');
  });

  it('hides both while the row is still downloading', () => {
    // they describe the finished file; showing them mid-flight would be a lie
    const { container } = render(
      <AdlRow
        dl={row({ status: 'downloading', verification_status: 'verified', quality: 'FLAC' })}
      />,
    );
    expect(container.querySelector('.dlc-shield')).toBeNull();
    expect(container.querySelector('.dlc-tag:not(.adl-retry-info)')).toBeNull();
  });

  describe('retry tag', () => {
    it('shows attempt info while in flight', () => {
      const { container } = render(
        <AdlRow dl={row({ status: 'downloading', retry_info: '2/5' })} />,
      );
      const chip = container.querySelector('.adl-retry-info') as HTMLElement;
      expect(chip.textContent).toContain('Retry 2/5');
    });

    it('says AcoustID when AcoustID caused the retry', () => {
      const { container } = render(
        <AdlRow
          dl={row({ status: 'downloading', retry_info: '2/5', retry_trigger: 'acoustid' })}
        />,
      );
      const chip = container.querySelector('.adl-retry-info') as HTMLElement;
      expect(chip.textContent).toContain('AcoustID');
      expect(chip.getAttribute('title')).toContain('quarantined (AcoustID)');
    });

    it('names any other trigger instead', () => {
      const { container } = render(
        <AdlRow
          dl={row({ status: 'downloading', retry_info: '1/3', retry_trigger: 'integrity' })}
        />,
      );
      const chip = container.querySelector('.adl-retry-info') as HTMLElement;
      expect(chip.textContent).not.toContain('AcoustID');
      expect(chip.getAttribute('title')).toContain('triggered by integrity');
    });

    it('disappears once the row is terminal', () => {
      const { container } = render(<AdlRow dl={row({ status: 'completed', retry_info: '3/5' })} />);
      expect(container.querySelector('.adl-retry-info')).toBeNull();
    });
  });

  describe('download next button', () => {
    it('appears only for queued rows and calls the handler', () => {
      const onDownloadNext = vi.fn();
      const queued = render(
        <AdlRow dl={row({ status: 'queued' })} onDownloadNext={onDownloadNext} />,
      );
      fireEvent.click(queued.container.querySelector('.dlc-next') as HTMLElement);
      expect(onDownloadNext).toHaveBeenCalled();

      cleanup();
      const active = render(
        <AdlRow dl={row({ status: 'downloading' })} onDownloadNext={onDownloadNext} />,
      );
      expect(active.container.querySelector('.dlc-next')).toBeNull();
    });

    it('does not also open the row', () => {
      const { container } = render(
        <AdlRow dl={row({ status: 'queued' })} onDownloadNext={vi.fn()} />,
      );
      fireEvent.click(container.querySelector('.dlc-next') as HTMLElement);
      expect(container.querySelector('.dlc-sheet')).toBeNull();
    });

    it('survives three clicks dispatched in the SAME tick', () => {
      const onDownloadNext = vi.fn(() => new Promise<void>(() => {}));
      const { container } = render(
        <AdlRow dl={row({ status: 'queued' })} onDownloadNext={onDownloadNext} />,
      );
      const btn = container.querySelector('.dlc-next') as HTMLButtonElement;
      act(() => {
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
      expect(onDownloadNext).toHaveBeenCalledTimes(1);
    });
  });

  describe('cancel button', () => {
    it('appears for in-flight rows with cancel coordinates', () => {
      const onCancel = vi.fn();
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      fireEvent.click(container.querySelector('.dlc-cancel') as HTMLElement);
      expect(onCancel).toHaveBeenCalled();
    });

    it('accepts track_index 0, which is a real index', () => {
      const { container } = render(
        <AdlRow dl={row({ status: 'queued', track_index: 0 })} onCancel={vi.fn()} />,
      );
      expect(container.querySelector('.dlc-cancel')).not.toBeNull();
    });

    it('is absent on terminal rows and without a playlist id', () => {
      const done = render(<AdlRow dl={row({ status: 'completed' })} onCancel={vi.fn()} />);
      expect(done.container.querySelector('.dlc-cancel')).toBeNull();
      cleanup();
      const orphan = render(
        <AdlRow dl={row({ status: 'queued', playlist_id: '' })} onCancel={vi.fn()} />,
      );
      expect(orphan.container.querySelector('.dlc-cancel')).toBeNull();
    });

    it('does not also open the row', () => {
      const onCancel = vi.fn();
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      fireEvent.click(container.querySelector('.dlc-cancel') as HTMLElement);
      expect(onCancel).toHaveBeenCalled();
      expect(container.querySelector('.dlc-sheet')).toBeNull();
    });

    it('locks itself while the cancel is in flight', async () => {
      // two clicks send two cancels, and the second fails on a task the first
      // already took. the user gets an error toast for an action that worked.
      let settle: () => void = () => {};
      const onCancel = vi.fn(() => new Promise<void>((resolve) => (settle = resolve)));
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      const btn = container.querySelector('.dlc-cancel') as HTMLButtonElement;

      fireEvent.click(btn);
      await waitFor(() => expect(btn.className).toContain('dlc-pending'));
      expect(btn.disabled).toBe(true);

      fireEvent.click(btn);
      fireEvent.click(btn);
      expect(onCancel).toHaveBeenCalledTimes(1);

      await act(async () => {
        settle();
      });
      // released once it settles: on a failed cancel the row stays, and the
      // button has to be usable again
      await waitFor(() => expect(btn.disabled).toBe(false));
      expect(btn.className).not.toContain('dlc-pending');
    });

    it('survives three clicks dispatched in the SAME tick', () => {
      // all three handlers run before React re-renders, so all three read the
      // same stale state. only the synchronous ref stops the later ones. raw
      // dispatchEvent in ONE act(), because fireEvent flushes between clicks
      // and hides exactly this race.
      const onCancel = vi.fn(() => new Promise<void>(() => {}));
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      const btn = container.querySelector('.dlc-cancel') as HTMLButtonElement;
      act(() => {
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
      expect(onCancel).toHaveBeenCalledTimes(1);
    });

    it('releases the lock when the cancel rejects', async () => {
      const onCancel = vi.fn(() => Promise.reject(new Error('boom')));
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      const btn = container.querySelector('.dlc-cancel') as HTMLButtonElement;
      await act(async () => {
        fireEvent.click(btn);
      });
      await waitFor(() => expect(btn.disabled).toBe(false));
    });

    it('still works for a handler that returns nothing', async () => {
      const onCancel = vi.fn();
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      const btn = container.querySelector('.dlc-cancel') as HTMLButtonElement;
      await act(async () => {
        fireEvent.click(btn);
      });
      expect(onCancel).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(btn.disabled).toBe(false));
    });
  });

  describe('search manually', () => {
    it('is the primary action on a live failed row and opens the inspector', () => {
      const { container } = render(<AdlRow dl={row({ status: 'not_found', task_id: 'tf' })} />);
      const btn = container.querySelector('.dlc-acts .dlc-btn-primary') as HTMLButtonElement;
      expect(btn.textContent).toContain('Search');
      fireEvent.click(btn);
      expect(openDownloadTaskInspector).toHaveBeenCalledWith('tf', {
        name: 'Xtal',
        artist: 'Aphex Twin',
        album: 'SAW',
      });
      // the row didn't open from the same click
      expect(container.querySelector('.dlc-sheet')).toBeNull();
    });

    it('is not offered on history rows or non-music batches', () => {
      const history = render(
        <AdlRow
          dl={row({ status: 'failed', is_persistent_history: true, task_id: 'history-1' })}
        />,
      );
      expect(history.container.querySelector('.dlc-acts .dlc-btn-primary')).toBeNull();
      cleanup();
      const book = render(<AdlRow dl={row({ status: 'failed' })} canInspect={false} />);
      expect(book.container.querySelector('.dlc-acts .dlc-btn-primary')).toBeNull();
    });
  });

  it('a click opens the sheet with its steps, a second closes it', () => {
    const { container } = render(<AdlRow dl={row({ status: 'searching' })} />);
    open(container);
    expect(container.querySelector('.dlc-sheet')).not.toBeNull();
    expect(container.querySelector('.dlc-step-now')?.textContent).toContain('Searching');
    open(container);
    expect(container.querySelector('.dlc-sheet')).toBeNull();
  });

  it('numbers the row instead of showing art in tracklist mode', () => {
    const { container } = render(
      <AdlRow dl={row({ status: 'queued', artwork: '/x.jpg', batch_position: 4 })} numbered />,
    );
    expect(container.querySelector('.dlc-num')?.textContent).toBe('4');
    expect(container.querySelector('img.dlc-art')).toBeNull();
    cleanup();
    const art = render(<AdlRow dl={row({ status: 'queued', artwork: '/x.jpg' })} />);
    expect(art.container.querySelector('img.dlc-art')).not.toBeNull();
  });

  it('puts the artist photo on the sub line, and in the lead when there is no cover', () => {
    const { container } = render(
      <AdlRow dl={row({ status: 'completed', artwork: '', artist_image: '/a.jpg' })} />,
    );
    expect(container.querySelector('.dlc-sub .dlc-avatar')?.getAttribute('src')).toBe('/a.jpg');
    expect(container.querySelector('.dlc-lead img.dlc-art-avatar')?.getAttribute('src')).toBe(
      '/a.jpg',
    );
    cleanup();
    const withCover = render(
      <AdlRow dl={row({ status: 'completed', artwork: '/c.jpg', artist_image: '/a.jpg' })} />,
    );
    expect(withCover.container.querySelector('.dlc-lead img.dlc-art')?.getAttribute('src')).toBe(
      '/c.jpg',
    );
    expect(withCover.container.querySelector('.dlc-art-avatar')).toBeNull();
  });

  it('opens with the cover, the artist and their photo on top', () => {
    const { container } = render(
      <AdlRow
        dl={row({
          status: 'queued',
          artwork: '/c.jpg',
          artist_image: '/a.jpg',
          batch_total: 12,
          batch_position: 3,
        })}
      />,
    );
    fireEvent.click(container.querySelector('.dlc-row') as HTMLElement);
    const hero = container.querySelector('.dlc-mhero') as HTMLElement;
    expect(hero.querySelector('.dlc-mhero-cover')?.getAttribute('src')).toBe('/c.jpg');
    expect(hero.querySelector('.dlc-mhero-artist')?.textContent).toBe('Aphex Twin');
    expect(hero.querySelector('.dlc-mhero-artist .dlc-avatar')?.getAttribute('src')).toBe('/a.jpg');
    expect(hero.querySelector('.dlc-eyebrow')?.textContent).toBe('Track 3 of 12');
  });

  it('renders a placeholder when there is no artwork', () => {
    const { container } = render(<AdlRow dl={row({ status: 'queued', artwork: '' })} />);
    expect(container.querySelector('.dlc-art-empty')?.textContent).toBe('X');
  });
});

describe('BatchFilterBanner', () => {
  it('names the batch and clears on click', () => {
    const onClear = vi.fn();
    const { container } = render(
      <BatchFilterBanner batchId="b1" batchName="My Batch" onClear={onClear} />,
    );
    expect(container.textContent).toContain('My Batch');
    expect(container.querySelector('.adl-filter-banner-dot')).not.toBeNull();
    fireEvent.click(container.querySelector('.adl-filter-banner-clear') as HTMLElement);
    expect(onClear).toHaveBeenCalled();
  });
});

describe('AdlHeader', () => {
  const counts = { active: 0, queued: 0, failed: 0, total: 0, completedOrFailed: 0 };
  const props = {
    filter: 'all' as const,
    counts,
    chipCounts: { all: 0, active: 0, queued: 0, completed: 0, failed: 0 },
    hasRunningWork: false,
    acoustidEnabled: true,
    reviewCount: null,
    live: null,
    quiet: 'Nothing downloading.',
    historyCount: null,
    onOpenHistory: vi.fn(),
    onFilter: vi.fn(),
    onCancelAll: vi.fn(),
    onClearCompleted: vi.fn(),
    cancelAllPending: false,
  };
  const openMenu = (container: HTMLElement) =>
    fireEvent.click(container.querySelector('#adl-actions .dlc-more') as HTMLElement);

  it('splits navigation: three view tabs, five count chips', () => {
    const { container } = render(<AdlHeader {...props} filter="queued" />);
    const tabs = [...container.querySelectorAll('#adl-view-tabs .dlh-tab')];
    expect(tabs.map((t) => t.textContent)).toEqual(['Downloads', 'Review', 'Clients']);
    const chips = [...container.querySelectorAll('#adl-filter-pills .dlh-chip')];
    expect(chips.map((p) => p.getAttribute('data-filter'))).toEqual([
      'all',
      'active',
      'queued',
      'completed',
      'failed',
    ]);
    expect(container.querySelector('.dlh-chip-on')?.getAttribute('data-filter')).toBe('queued');
    // a status filter is still the Downloads view
    expect(container.querySelector('.dlh-tab-on')?.textContent).toBe('Downloads');
  });

  it('puts each count on its chip, greys a zero and reds the failures', () => {
    const { container } = render(
      <AdlHeader
        {...props}
        chipCounts={{ all: 9, active: 2, queued: 0, completed: 6, failed: 1 }}
      />,
    );
    const chip = (f: string) =>
      container.querySelector(`[data-filter="${f}"].dlh-chip`) as HTMLElement;
    expect(chip('all').textContent).toBe('All9');
    expect(chip('queued').textContent).toBe('Queued');
    expect(chip('queued').className).toContain('dlh-chip-zero');
    expect(chip('failed').className).toContain('dlh-chip-bad');
  });

  it('tabs map back onto the old filter values', () => {
    const onFilter = vi.fn();
    const { container } = render(<AdlHeader {...props} onFilter={onFilter} />);
    fireEvent.click(container.querySelector('[data-filter="unverified"]') as HTMLElement);
    fireEvent.click(
      container.querySelector('#adl-view-tabs [data-filter="clients"]') as HTMLElement,
    );
    fireEvent.click(container.querySelector('#adl-view-tabs [data-filter="all"]') as HTMLElement);
    expect(onFilter.mock.calls.map((c) => c[0])).toEqual(['unverified', 'clients', 'all']);
  });

  it('hides the chips and the strip outside the Downloads view', () => {
    const review = render(<AdlHeader {...props} filter="unverified" />);
    expect(review.container.querySelector('#adl-filter-pills')).toBeNull();
    expect(review.container.querySelector('.dlh-quiet')).toBeNull();
    expect(review.container.querySelector('.dlh-tab-on')?.textContent).toBe('Review');
    cleanup();
    const clients = render(<AdlHeader {...props} filter="clients" />);
    expect(clients.container.querySelector('#adl-filter-pills')).toBeNull();
    expect(clients.container.querySelector('.dlh-tab-on')?.textContent).toBe('Clients');
  });

  it('shows the live strip while something moves, the quiet line when not', () => {
    const live = {
      speedText: '1.2 MB/s',
      speedBps: 1_200_000,
      downloading: 2,
      done: 3,
      active: 2,
      queued: 1,
      failed: 0,
      nowTitle: 'Xtal',
      nowSub: 'Aphex Twin',
      nowMore: 1,
      etaText: 'about 4m left',
    };
    const { container } = render(<AdlHeader {...props} live={live} />);
    expect(container.querySelector('.dlh-speed strong')?.textContent).toBe('1.2 MB/s');
    expect(container.querySelector('.dlh-now-line')?.textContent).toBe(
      'Now: Xtal · Aphex Twin, and 1 more · about 4m left',
    );
    // no failed segment in the legend when nothing failed
    expect(container.querySelector('.dlh-l-failed')).toBeNull();
    expect(container.querySelector('.dlh-quiet')).toBeNull();
    cleanup();
    const idle = render(<AdlHeader {...props} quiet="Nothing downloading. 2 finished today." />);
    expect(idle.container.querySelector('.dlh-strip')).toBeNull();
    expect(idle.container.querySelector('.dlh-quiet')?.textContent).toBe(
      'Nothing downloading. 2 finished today.',
    );
  });

  it('has no menu with nothing to act on, and only the actions that apply', () => {
    const idle = render(<AdlHeader {...props} />);
    expect(idle.container.querySelector('#adl-actions .dlc-more')).toBeNull();
    cleanup();
    const busy = render(
      <AdlHeader
        {...props}
        hasRunningWork
        counts={{ ...counts, active: 2, queued: 3, completedOrFailed: 4 }}
      />,
    );
    openMenu(busy.container);
    expect(screen.getByRole('menuitem', { name: 'Clear finished (4)' })).not.toBeNull();
    const cancel = screen.getByRole('menuitem', { name: 'Cancel all (5)' });
    expect(cancel.className).toContain('danger');
  });

  it('locks Cancel all while it is running', () => {
    const { container } = render(
      <AdlHeader {...props} hasRunningWork cancelAllPending counts={{ ...counts, active: 1 }} />,
    );
    openMenu(container);
    expect(
      (screen.getByRole('menuitem', { name: 'Cancel all (1)' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('opens the history from the header, with its count', () => {
    const onOpenHistory = vi.fn();
    const { container } = render(
      <AdlHeader {...props} historyCount={4835} onOpenHistory={onOpenHistory} />,
    );
    const btn = container.querySelector('.dlh-history') as HTMLElement;
    expect(btn.textContent).toBe(`History${(4835).toLocaleString()}`);
    fireEvent.click(btn);
    expect(onOpenHistory).toHaveBeenCalled();
  });

  it('relabels the review tab when there can be no unverified queue', () => {
    const on = render(<AdlHeader {...props} acoustidEnabled reviewCount={4} />);
    const enabled = on.container.querySelector('[data-filter="unverified"]') as HTMLElement;
    expect(enabled.textContent).toBe('Review4');
    expect(enabled.querySelector('.dlh-badge')?.textContent).toBe('4');

    cleanup();
    const off = render(<AdlHeader {...props} acoustidEnabled={false} />);
    const disabled = off.container.querySelector('[data-filter="unverified"]') as HTMLElement;
    expect(disabled.textContent).toBe('Quarantine');
    expect(disabled.getAttribute('title')).toContain('require-verified');
  });
});
