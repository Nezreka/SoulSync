import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SyncButton, SyncStatus } from './sync-status';

/**
 * The shared sync-status panel.
 *
 * Its ids are load-bearing — the poller writes into them by id — and its
 * arithmetic has one rule that is easy to get wrong in a way that looks like a
 * hang: a FAILED track is processed, not pending.
 */

afterEach(cleanup);

describe('the sync panel', () => {
  const progress = { total_tracks: 10, matched_tracks: 6, failed_tracks: 2 };

  it('is absent until a sync is running', () => {
    const { container } = render(<SyncStatus statusBase="seasonal-playlist" visible={false} />);
    expect(container.querySelector('.discover-sync-status')).toBeNull();
  });

  it('derives every id from the status base', () => {
    const { container } = render(
      <SyncStatus statusBase="seasonal-playlist" progress={progress} visible />,
    );
    for (const id of [
      '#seasonal-playlist-sync-status',
      '#seasonal-playlist-sync-completed',
      '#seasonal-playlist-sync-pending',
      '#seasonal-playlist-sync-failed',
      '#seasonal-playlist-sync-percentage',
    ]) {
      expect(container.querySelector(id), id).not.toBeNull();
    }
  });

  it('gives a different base a different set of ids — ALL of them', () => {
    // Two panels on one page sharing any of these would have the poller writing
    // into whichever the browser found first. Checking one id would leave the
    // other four free to be hard-coded.
    const { container } = render(
      <SyncStatus statusBase="decade-1990" progress={progress} visible />,
    );
    for (const suffix of ['status', 'completed', 'pending', 'failed', 'percentage']) {
      expect(container.querySelector(`#decade-1990-sync-${suffix}`), suffix).not.toBeNull();
      expect(container.querySelector(`#seasonal-playlist-sync-${suffix}`), suffix).toBeNull();
    }
  });

  it('counts failures as processed, not pending', () => {
    // 6 matched + 2 failed of 10 → 2 pending and 80%. Counting failures as
    // pending would sit at 60% and never finish.
    const { container } = render(
      <SyncStatus statusBase="seasonal-playlist" progress={progress} visible />,
    );
    expect(container.querySelector('#seasonal-playlist-sync-completed')!.textContent).toBe('6');
    expect(container.querySelector('#seasonal-playlist-sync-failed')!.textContent).toBe('2');
    expect(container.querySelector('#seasonal-playlist-sync-pending')!.textContent).toBe('2');
    expect(container.querySelector('#seasonal-playlist-sync-percentage')!.textContent).toBe('80');
  });

  it('reaches 100% even when everything failed', () => {
    const { container } = render(
      <SyncStatus
        statusBase="seasonal-playlist"
        progress={{ total_tracks: 4, matched_tracks: 0, failed_tracks: 4 }}
        visible
      />,
    );
    expect(container.querySelector('#seasonal-playlist-sync-percentage')!.textContent).toBe('100');
    expect(container.querySelector('#seasonal-playlist-sync-pending')!.textContent).toBe('0');
  });

  it('shows zeroes rather than NaN before any progress arrives', () => {
    const { container } = render(<SyncStatus statusBase="seasonal-playlist" visible />);
    expect(container.querySelector('#seasonal-playlist-sync-percentage')!.textContent).toBe('0');
    expect(container.querySelector('#seasonal-playlist-sync-pending')!.textContent).toBe('0');
  });
});

describe('the sync button', () => {
  it('is pressable when idle', () => {
    const onClick = vi.fn();
    const { container } = render(
      <SyncButton id="seasonal-playlist-sync-btn" running={false} onClick={onClick} />,
    );
    const btn = container.querySelector('button') as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    expect(btn.style.opacity).toBe('1');
    expect(btn.style.cursor).toBe('pointer');
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalled();
  });

  it('moves all three states together while running', () => {
    // A disabled button that still looks and behaves clickable is worse than
    // one that plainly cannot be pressed.
    const onClick = vi.fn();
    const { container } = render(
      <SyncButton id="seasonal-playlist-sync-btn" running onClick={onClick} />,
    );
    const btn = container.querySelector('button') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.style.opacity).toBe('0.5');
    expect(btn.style.cursor).toBe('not-allowed');
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('keeps the id the poller re-enables it by', () => {
    const { container } = render(
      <SyncButton id="decade-1990-sync-btn" running={false} onClick={vi.fn()} />,
    );
    expect(container.querySelector('#decade-1990-sync-btn')).not.toBeNull();
  });

  it('takes a custom label', () => {
    const { container } = render(
      <SyncButton id="x" running={false} label="Sync to Plex" onClick={vi.fn()} />,
    );
    expect(container.querySelector('button')!.textContent).toBe('Sync to Plex');
  });
});
