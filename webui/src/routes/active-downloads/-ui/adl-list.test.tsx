import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AdlDownload } from '../-adl.types';

import { AdlHeader } from './adl-header';
import { AdlList, ADL_EMPTY_TEXT, BatchFilterBanner } from './adl-list';
import { AdlRow } from './adl-row';

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
  it('renders title, meta and the status class', () => {
    const { container } = render(<AdlRow dl={row()} />);
    const el = container.querySelector('.adl-row') as HTMLElement;
    expect(el.className).toBe('adl-row adl-row-active');
    expect(el.getAttribute('data-task-id')).toBe('t1');
    expect(container.querySelector('.adl-row-title')?.textContent).toBe('Xtal');
    expect(container.querySelector('.adl-row-meta')?.textContent).toBe('Aphex Twin · SAW');
  });

  it('falls back for an untitled row', () => {
    const { container } = render(<AdlRow dl={row({ title: '' })} />);
    expect(container.querySelector('.adl-row-title')?.textContent).toBe('Unknown Track');
  });

  it('shows the track position only for a real multi-track batch', () => {
    const { container } = render(
      <AdlRow dl={row({ batch_name: 'My Batch', batch_total: 19, track_index: 2 })} />,
    );
    expect(container.querySelector('.adl-row-batch')?.textContent).toBe('My Batch · Track 3 of 19');

    cleanup();
    const single = render(<AdlRow dl={row({ batch_name: 'Solo', batch_total: 1 })} />);
    expect(single.container.querySelector('.adl-row-batch')?.textContent).toBe('Solo');
  });

  it('renders the error line only when there is one', () => {
    const { container } = render(<AdlRow dl={row({ error: 'peer offline' })} />);
    expect(container.querySelector('.adl-row-error')?.textContent).toBe('peer offline');
    cleanup();
    const clean = render(<AdlRow dl={row()} />);
    expect(clean.container.querySelector('.adl-row-error')).toBeNull();
  });

  it('renders a spinner for in-flight statuses only', () => {
    const { container } = render(<AdlRow dl={row({ status: 'searching' })} />);
    expect(container.querySelector('.adl-row-status .adl-spinner')).not.toBeNull();
    cleanup();
    const done = render(<AdlRow dl={row({ status: 'completed' })} />);
    expect(done.container.querySelector('.adl-row-status .adl-spinner')).toBeNull();
  });

  it('shows the verification and quality chips on a completed row', () => {
    const { container } = render(
      <AdlRow
        dl={row({
          status: 'completed',
          verification_status: 'human_verified',
          quality: 'FLAC 16/44',
        })}
      />,
    );
    expect(container.querySelector('.verif-badge.verif-human')?.textContent).toBe('🛡✔');
    expect(container.querySelector('.adl-quality-chip')?.textContent).toBe('FLAC 16/44');
  });

  it('hides both chips while the row is still downloading', () => {
    // They describe the finished file; showing them mid-flight would be a lie.
    const { container } = render(
      <AdlRow
        dl={row({ status: 'downloading', verification_status: 'verified', quality: 'FLAC' })}
      />,
    );
    expect(container.querySelector('.verif-badge')).toBeNull();
    expect(container.querySelector('.adl-quality-chip')).toBeNull();
  });

  describe('retry chip', () => {
    it('shows attempt info while in flight', () => {
      const { container } = render(
        <AdlRow dl={row({ status: 'downloading', retry_info: '2/5' })} />,
      );
      const chip = container.querySelector('.adl-retry-info') as HTMLElement;
      expect(chip.textContent).toContain('🔁');
      expect(chip.textContent).toContain('2/5');
    });

    it('adds the shield when AcoustID caused the retry', () => {
      const { container } = render(
        <AdlRow
          dl={row({ status: 'downloading', retry_info: '2/5', retry_trigger: 'acoustid' })}
        />,
      );
      const chip = container.querySelector('.adl-retry-info') as HTMLElement;
      expect(chip.textContent).toContain('🛡');
      expect(chip.getAttribute('title')).toContain('quarantined (AcoustID)');
    });

    it('names any other trigger instead', () => {
      const { container } = render(
        <AdlRow
          dl={row({ status: 'downloading', retry_info: '1/3', retry_trigger: 'integrity' })}
        />,
      );
      const chip = container.querySelector('.adl-retry-info') as HTMLElement;
      expect(chip.textContent).not.toContain('🛡');
      expect(chip.getAttribute('title')).toContain('triggered by integrity');
    });

    it('disappears once the row is terminal', () => {
      const { container } = render(<AdlRow dl={row({ status: 'completed', retry_info: '3/5' })} />);
      expect(container.querySelector('.adl-retry-info')).toBeNull();
    });
  });

  describe('cancel button', () => {
    it('appears for in-flight rows with cancel coordinates', () => {
      const onCancel = vi.fn();
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      fireEvent.click(container.querySelector('.adl-row-cancel') as HTMLElement);
      expect(onCancel).toHaveBeenCalled();
    });

    it('accepts track_index 0, which is a real index', () => {
      const { container } = render(
        <AdlRow dl={row({ status: 'queued', track_index: 0 })} onCancel={vi.fn()} />,
      );
      expect(container.querySelector('.adl-row-cancel')).not.toBeNull();
    });

    it('is absent on terminal rows and without a playlist id', () => {
      const done = render(<AdlRow dl={row({ status: 'completed' })} onCancel={vi.fn()} />);
      expect(done.container.querySelector('.adl-row-cancel')).toBeNull();
      cleanup();
      const orphan = render(
        <AdlRow dl={row({ status: 'queued', playlist_id: '' })} onCancel={vi.fn()} />,
      );
      expect(orphan.container.querySelector('.adl-row-cancel')).toBeNull();
    });

    it('does not also trigger the row click', () => {
      const onRowAudit = vi.fn();
      const onCancel = vi.fn();
      const { container } = render(
        <AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} onRowAudit={onRowAudit} />,
      );
      fireEvent.click(container.querySelector('.adl-row-cancel') as HTMLElement);
      expect(onCancel).toHaveBeenCalled();
      expect(onRowAudit).not.toHaveBeenCalled();
    });

    it('locks itself while the cancel is in flight', async () => {
      // Two clicks send two cancels, and the second fails on a task the first
      // already took — the user gets an error toast for an action that worked.
      // The vanilla guarded this with a dataset flag plus a pending class.
      let settle: () => void = () => {};
      const onCancel = vi.fn(() => new Promise<void>((resolve) => (settle = resolve)));
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      const btn = container.querySelector('.adl-row-cancel') as HTMLButtonElement;

      fireEvent.click(btn);
      await waitFor(() => expect(btn.className).toContain('adl-row-cancel-pending'));
      expect(btn.disabled).toBe(true);

      fireEvent.click(btn);
      fireEvent.click(btn);
      expect(onCancel).toHaveBeenCalledTimes(1);

      await act(async () => {
        settle();
      });
      // Released once it settles: on a failed cancel the row stays, and the
      // button has to be usable again.
      await waitFor(() => expect(btn.disabled).toBe(false));
      expect(btn.className).not.toContain('adl-row-cancel-pending');
    });

    it('survives three clicks dispatched in the SAME tick', () => {
      // The case neither `disabled` nor a state check can catch: all three
      // handlers run before React re-renders, so all three read the same stale
      // `false` out of state. Only a synchronous flag stops the later ones,
      // which is why the vanilla used `dataset.cancelling`.
      //
      // Raw dispatchEvent rather than fireEvent on purpose: fireEvent wraps
      // each click in act(), which flushes a re-render in between and hides
      // exactly the race this test exists for. One act() around all three
      // keeps them in a single tick.
      const onCancel = vi.fn(() => new Promise<void>(() => {}));
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      const btn = container.querySelector('.adl-row-cancel') as HTMLButtonElement;

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
      const btn = container.querySelector('.adl-row-cancel') as HTMLButtonElement;

      await act(async () => {
        fireEvent.click(btn);
      });
      await waitFor(() => expect(btn.disabled).toBe(false));
    });

    it('still works for a handler that returns nothing', async () => {
      // The prop is `void | Promise<void>`; a sync handler must not leave the
      // button stuck disabled.
      const onCancel = vi.fn();
      const { container } = render(<AdlRow dl={row({ status: 'queued' })} onCancel={onCancel} />);
      const btn = container.querySelector('.adl-row-cancel') as HTMLButtonElement;
      await act(async () => {
        fireEvent.click(btn);
      });
      expect(onCancel).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(btn.disabled).toBe(false));
    });
  });

  it('makes the whole row an audit trigger only when asked', () => {
    const onRowAudit = vi.fn();
    const { container } = render(<AdlRow dl={row()} onRowAudit={onRowAudit} />);
    fireEvent.click(container.querySelector('.adl-row') as HTMLElement);
    expect(onRowAudit).toHaveBeenCalled();

    cleanup();
    const plain = render(<AdlRow dl={row()} />);
    expect((plain.container.querySelector('.adl-row') as HTMLElement).style.cursor).toBe('');
  });

  it('renders a placeholder when there is no artwork', () => {
    const { container } = render(<AdlRow dl={row({ artwork: '' })} />);
    expect(container.querySelector('.adl-row-art-empty')).not.toBeNull();
    cleanup();
    const art = render(<AdlRow dl={row({ artwork: '/x.jpg' })} />);
    expect(art.container.querySelector('img.adl-row-art')).not.toBeNull();
  });

  it('carries a batch colour stripe only for batched rows', () => {
    const { container } = render(<AdlRow dl={row({ batch_id: 'b1' })} />);
    expect(container.querySelector('.adl-row-batch-color')).not.toBeNull();
    cleanup();
    const loose = render(<AdlRow dl={row({ batch_id: '' })} />);
    expect(loose.container.querySelector('.adl-row-batch-color')).toBeNull();
  });
});

describe('AdlList', () => {
  it('shows the empty message with nothing to list', () => {
    const { container } = render(<AdlList rows={[]} filter="all" onCancel={vi.fn()} />);
    expect(container.querySelector('#adl-empty')?.textContent).toBe(ADL_EMPTY_TEXT);
  });

  it('groups into sections with counts under the all filter', () => {
    const { container } = render(
      <AdlList
        rows={[
          row({ task_id: 'a', status: 'downloading' }),
          row({ task_id: 'b', status: 'queued' }),
          row({ task_id: 'c', status: 'completed' }),
          row({ task_id: 'd', status: 'cancelled' }),
        ]}
        filter="all"
        onCancel={vi.fn()}
      />,
    );
    const headers = [...container.querySelectorAll('.adl-section-header')].map(
      (h) => h.textContent,
    );
    // Cancelled belongs to Failed, which is why that header reads (1).
    expect(headers).toEqual(['Active (1)', 'Queued (1)', 'Completed (1)', 'Failed (1)']);
  });

  it('drops the headers under a specific filter', () => {
    // Every row is already that kind — a header would just repeat the pill.
    const { container } = render(
      <AdlList rows={[row({ status: 'completed' })]} filter="completed" onCancel={vi.fn()} />,
    );
    expect(container.querySelectorAll('.adl-section-header')).toHaveLength(0);
    expect(container.querySelectorAll('.adl-row')).toHaveLength(1);
  });

  it('omits sections that have no rows', () => {
    const { container } = render(
      <AdlList rows={[row({ status: 'downloading' })]} filter="all" onCancel={vi.fn()} />,
    );
    expect(
      [...container.querySelectorAll('.adl-section-header')].map((h) => h.textContent),
    ).toEqual(['Active (1)']);
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
  const counts = { active: 0, queued: 0, total: 0, completedOrFailed: 0 };
  const props = {
    filter: 'all' as const,
    counts,
    hasRunningWork: false,
    acoustidEnabled: true,
    onFilter: vi.fn(),
    onCancelAll: vi.fn(),
    onClearCompleted: vi.fn(),
    cancelAllPending: false,
  };

  it('renders six pills and marks the active one', () => {
    const { container } = render(<AdlHeader {...props} filter="queued" />);
    const pills = [...container.querySelectorAll('.adl-pill')];
    expect(pills).toHaveLength(6);
    expect(pills.map((p) => p.getAttribute('data-filter'))).toEqual([
      'all',
      'active',
      'queued',
      'completed',
      'failed',
      'unverified',
    ]);
    expect(container.querySelector('.adl-pill.active')?.getAttribute('data-filter')).toBe('queued');
  });

  it('drops zero counts but always shows the total', () => {
    const { container } = render(
      <AdlHeader {...props} counts={{ active: 2, queued: 0, total: 9, completedOrFailed: 0 }} />,
    );
    expect(container.querySelector('#adl-count')?.textContent).toBe('2 active / 9 total');
  });

  it('shows Cancel All only when there is running work', () => {
    const idle = render(<AdlHeader {...props} />);
    expect(idle.container.querySelector('#adl-cancel-all-btn')).toBeNull();
    cleanup();
    const busy = render(<AdlHeader {...props} hasRunningWork />);
    expect(busy.container.querySelector('#adl-cancel-all-btn')).not.toBeNull();
  });

  it('shows Clear Completed only when something can be cleared', () => {
    const empty = render(<AdlHeader {...props} />);
    expect(empty.container.querySelector('#adl-clear-btn')).toBeNull();
    cleanup();
    const has = render(<AdlHeader {...props} counts={{ ...counts, completedOrFailed: 3 }} />);
    expect(has.container.querySelector('#adl-clear-btn')).not.toBeNull();
  });

  it('relabels the review pill when there can be no unverified queue', () => {
    // The vanilla rewrote the button's textContent after the config fetch;
    // here it is derived, so it can never get out of step with the data.
    const on = render(<AdlHeader {...props} acoustidEnabled />);
    const enabled = on.container.querySelector('[data-filter="unverified"]') as HTMLElement;
    expect(enabled.textContent).toBe('⚠ Unverified/Quarantine');

    cleanup();
    const off = render(<AdlHeader {...props} acoustidEnabled={false} />);
    const disabled = off.container.querySelector('[data-filter="unverified"]') as HTMLElement;
    expect(disabled.textContent).toBe('🛡 Quarantine');
    expect(disabled.getAttribute('title')).toContain('require-verified');
  });

  it('locks Cancel All while it is running', () => {
    const { container } = render(<AdlHeader {...props} hasRunningWork cancelAllPending />);
    const btn = container.querySelector('#adl-cancel-all-btn') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.className).toContain('adl-cancel-all-pending');
  });
});
