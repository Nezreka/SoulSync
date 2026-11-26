import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getBulkState, resetBulkStoreForTests, startBulkRun } from '../-sync.bulk-store';
import { flush, gatedServer } from '../__fixtures__/bulk-server';
import { BulkRunHost } from './bulk-run-host';

const rows = [
  { id: 1, name: 'Road Trip' },
  { id: 2, name: 'Gym' },
];

afterEach(() => {
  act(() => resetBulkStoreForTests());
});

async function start(mode: 'sync' | 'download' = 'sync') {
  const s = gatedServer();
  await act(async () => {
    startBulkRun(mode, rows, s.deps as never);
    await flush();
  });
  return s;
}

const pill = () => screen.getByRole('button', { name: /Show progress/ });

describe('the bulk run pill and panel (#2320)', () => {
  it('shows nothing with no run', () => {
    render(<BulkRunHost />);
    expect(screen.queryByRole('button', { name: /Show progress/ })).toBeNull();
  });

  it('an outside click hides the panel and the pill brings it back', async () => {
    render(
      <div>
        <p>elsewhere</p>
        <BulkRunHost />
      </div>,
    );
    await start('download');
    expect(screen.getByRole('dialog').textContent).toContain('Sync & download · Syncing 1 of 2');
    // the dismiss listener attaches a tick after opening
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1));
    });
    fireEvent.click(screen.getByText('elsewhere'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(pill().textContent).toBe('Syncing 1 of 2');
    fireEvent.click(pill());
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('Stop after this one')).toBeTruthy();
  });

  it('Escape and the hide button hide it without stopping the run', async () => {
    render(<BulkRunHost />);
    await start();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(pill());
    fireEvent.click(screen.getByText('Hide'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(getBulkState().run!.running).toBe(true);
    expect(pill()).toBeTruthy();
  });

  it('survives leaving the page and coming back', async () => {
    const first = render(<BulkRunHost />);
    const s = await start();
    first.unmount();
    await act(async () => {
      await s.release(1);
    });
    render(<BulkRunHost />);
    expect(pill().textContent).toBe('Syncing 2 of 2');
    expect(screen.getByRole('dialog').textContent).toContain('Road TripDone');
  });

  it('stop from the panel, then the pill says how it went and the list reloads', async () => {
    const onFinished = vi.fn();
    render(<BulkRunHost onFinished={onFinished} />);
    const s = await start();
    fireEvent.click(screen.getByText('Stop after this one'));
    expect(screen.getByText('Stopping after this one…')).toBeTruthy();
    await act(async () => {
      await s.release(1);
    });
    expect(onFinished).toHaveBeenCalledTimes(1);
    expect(pill().textContent).toBe('Done · 1 synced, 1 not started');
    fireEvent.click(screen.getByText('Close'));
    expect(screen.queryByRole('button', { name: /Show progress/ })).toBeNull();
  });
});
