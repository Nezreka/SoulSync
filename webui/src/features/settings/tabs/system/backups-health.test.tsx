import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { BackupsView, checkedWhen } from './backups-view';

afterEach(() => {
  Reflect.deleteProperty(window, 'showConfirmDialog');
  Reflect.deleteProperty(window, 'showToast');
});

function serve(healthy: boolean | null, repair: Record<string, unknown> = {}) {
  const calls: string[] = [];
  server.use(
    http.get('*/api/database/health', () =>
      HttpResponse.json({
        success: true,
        healthy,
        detail: healthy === false ? 'Tree 3 page 3: bad' : '',
        checked_at: Date.now() / 1000 - 120,
        checking: false,
        repair_tool: true,
      }),
    ),
    http.post('*/api/database/repair', () => {
      calls.push('repair');
      return HttpResponse.json(repair, { status: repair.success === false ? 422 : 200 });
    }),
    http.get('*/api/database/backups', () => HttpResponse.json({ success: true, backups: [] })),
    http.get('*/api/database/companions', () =>
      HttpResponse.json({ success: true, companions: [] }),
    ),
    http.get('*/api/video/backups', () => HttpResponse.json({ success: true, backups: [] })),
  );
  return calls;
}

describe('database health on the Backups page', () => {
  it('says when it was checked', () => {
    expect(checkedWhen(null)).toBe('Not checked yet');
    expect(checkedWhen(1_000, 1_000 * 1000 + 30_000)).toBe('Checked just now');
    expect(checkedWhen(1_000, 1_000 * 1000 + 5 * 60_000)).toBe('Checked 5 min ago');
  });

  it('a healthy database offers a check, not a repair', async () => {
    serve(true);
    render(<BackupsView shown opened={1} />);
    expect(await screen.findByText('Healthy')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check now' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Repair' })).toBeNull();
  });

  it('a damaged one says backups stopped, repairs after asking, and offers the backup when it fails', async () => {
    const calls = serve(false, {
      success: false,
      repaired: false,
      error: '2 tables couldn’t be read.',
      kept: 'damaged_20261125_120000',
      backup: 'music_library.db.backup_20261112_114908',
    });
    window.showConfirmDialog = vi.fn(async () => true) as never;
    window.showToast = vi.fn() as never;
    render(<BackupsView shown opened={1} />);
    expect(await screen.findByText('Damaged')).toBeInTheDocument();
    expect(screen.getByText(/Backups have stopped/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Repair' }));
    await waitFor(() => expect(calls).toEqual(['repair']));
    expect(await screen.findByText(/2 tables couldn’t be read/)).toBeInTheDocument();
    expect(screen.getByText(/damaged_20261125_120000/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Restore / })).toBeInTheDocument();
  });

  it('nothing is repaired when the answer is no', async () => {
    const calls = serve(false);
    window.showConfirmDialog = vi.fn(async () => false) as never;
    render(<BackupsView shown opened={1} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Repair' }));
    await new Promise((r) => setTimeout(r, 50));
    expect(calls).toEqual([]);
  });
});
