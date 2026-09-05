/**
 * the deleted-files view (the music recycle bin): row rendering, the
 * explainer copy, and the action layer's confirm gating.
 */

import { fireEvent, render } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { AdlDeletedEntry } from '@/routes/active-downloads/-adl.types';

import {
  emptyDeletedBin,
  purgeDeletedEntry,
  restoreAllDeleted,
  restoreDeletedEntry,
} from '@/routes/active-downloads/-adl.verif-actions';
import {
  AdlDeletedList,
  AdlDeletedRow,
  AdlReviewExplainer,
} from '@/routes/active-downloads/-ui/adl-review';
import { server } from '@/test/msw';

const ENTRY: AdlDeletedEntry = {
  id: 'deleted:Artist/Album/01 - Song.flac',
  name: '01 - Song.flac',
  rel: 'Artist/Album/01 - Song.flac',
  size: 12_345_678,
  deleted_at: '2026-08-20T10:00:00+00:00',
  source: 'duplicate-cleaner',
  original_path: '/music/Artist/Album/01 - Song.flac',
};

let toasts: string[] = [];
let confirmAnswer = true;
let confirms: Record<string, unknown>[] = [];

beforeEach(() => {
  toasts = [];
  confirms = [];
  confirmAnswer = true;
  window.showToast = vi.fn((message: string) => {
    toasts.push(message);
  });
  window.showConfirmDialog = vi.fn((options?: Record<string, unknown>) => {
    confirms.push(options ?? {});
    return Promise.resolve(confirmAnswer);
  });
});

describe('AdlDeletedRow', () => {
  it('shows name, destination, source and size, with both verbs wired', () => {
    const onRestore = vi.fn();
    const onPurge = vi.fn();
    const { container } = render(<AdlDeletedRow entry={ENTRY} handlers={{ onRestore, onPurge }} />);
    expect(container.textContent).toContain('01 - Song.flac');
    expect(container.textContent).toContain('/music/Artist/Album/01 - Song.flac');
    expect(container.textContent).toContain('Duplicate cleaner');
    expect(container.textContent).toContain('12 MB');
    fireEvent.click(container.querySelector('.verif-act-ok') as HTMLElement);
    expect(onRestore).toHaveBeenCalled();
    const { container: c2 } = render(
      <AdlDeletedRow entry={ENTRY} handlers={{ onRestore: vi.fn(), onPurge }} />,
    );
    fireEvent.click(c2.querySelector('.verif-act-del') as HTMLElement);
    expect(onPurge).toHaveBeenCalled();
  });

  it('says "age unknown" for a pre-manifest file instead of lying', () => {
    const { container } = render(
      <AdlDeletedRow
        entry={{ ...ENTRY, deleted_at: null, source: null }}
        handlers={{ onRestore: vi.fn(), onPurge: vi.fn() }}
      />,
    );
    expect(container.textContent).toContain('age unknown');
  });
});

describe('AdlDeletedList', () => {
  const handlersFor = () => ({ onRestore: vi.fn(), onPurge: vi.fn() });
  const base = { totalSize: 0, keepDays: 0, handlersFor, onKeepDays: vi.fn() };

  it('shows a loading state before the first fetch lands', () => {
    const { container } = render(<AdlDeletedList {...base} entries={[]} loaded={false} />);
    expect(container.textContent).toContain('Loading deleted files');
  });

  it('shows the empty-bin copy once loaded, retention still reachable', () => {
    const { container } = render(<AdlDeletedList {...base} entries={[]} loaded />);
    expect(container.textContent).toContain('the bin is empty');
    expect(container.querySelector('.adl-deleted-retention')).not.toBeNull();
  });

  it('renders a header with count and total size above the rows', () => {
    const { container } = render(
      <AdlDeletedList
        {...base}
        entries={[ENTRY, { ...ENTRY, id: 'deleted:b.mp3', name: 'b.mp3' }]}
        totalSize={24_691_356}
        loaded
      />,
    );
    expect(container.textContent).toContain('2 files');
    expect(container.querySelectorAll('[data-deleted-id]')).toHaveLength(2);
  });

  it('the retention select reports the chosen window', () => {
    const onKeepDays = vi.fn();
    const { container } = render(
      <AdlDeletedList {...base} entries={[ENTRY]} loaded onKeepDays={onKeepDays} />,
    );
    fireEvent.change(container.querySelector('.adl-deleted-retention') as HTMLElement, {
      target: { value: '30' },
    });
    expect(onKeepDays).toHaveBeenCalledWith(30);
  });
});

describe('AdlReviewExplainer', () => {
  it('answers the question each sub-view raises', () => {
    const unv = render(<AdlReviewExplainer subView="unverified" />);
    expect(unv.container.textContent).toContain('human-verified');
    const quar = render(<AdlReviewExplainer subView="quarantine" />);
    expect(quar.container.textContent).toContain('never imported');
    expect(quar.container.textContent).toContain('nothing is renamed');
    const del = render(<AdlReviewExplainer subView="deleted" />);
    expect(del.container.textContent).toContain('restore puts one back');
  });
});

describe('the deleted-file actions', () => {
  it('restore calls the endpoint and reports the count', async () => {
    let body: unknown;
    server.use(
      http.post('/api/deleted-files/restore', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true, restored: [ENTRY.id], errors: [] });
      }),
    );
    const onDone = vi.fn();
    await restoreDeletedEntry(ENTRY, onDone);
    expect(body).toEqual({ ids: [ENTRY.id] });
    expect(onDone).toHaveBeenCalled();
    expect(toasts[0]).toBe('Restored 1 file');
  });

  it('purge is confirm-gated: cancelling never touches the endpoint', async () => {
    const hit = vi.fn();
    server.use(
      http.post('/api/deleted-files/purge', () => {
        hit();
        return HttpResponse.json({ success: true, purged: [ENTRY.id], errors: [] });
      }),
    );
    confirmAnswer = false;
    const onDone = vi.fn();
    await purgeDeletedEntry(ENTRY, onDone);
    expect(hit).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalled();
    expect(confirms[0].destructive).toBe(true);
  });

  it('empty bin purges everything only after a destructive confirm', async () => {
    let body: unknown;
    server.use(
      http.post('/api/deleted-files/purge', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true, purged: ['a', 'b'], errors: [] });
      }),
    );
    await emptyDeletedBin(2, vi.fn());
    expect(body).toEqual({ all: true });
    expect(confirms[0].destructive).toBe(true);
    expect(toasts[0]).toBe('Deleted 2 files');
  });

  it('restore-all reports partial failures honestly', async () => {
    server.use(
      http.post('/api/deleted-files/restore', () =>
        HttpResponse.json({
          success: true,
          restored: ['deleted:a.mp3'],
          errors: [{ id: 'deleted:b.mp3', error: 'a file already exists at /music/b.mp3' }],
        }),
      ),
    );
    await restoreAllDeleted([ENTRY, { ...ENTRY, id: 'deleted:b.mp3' }], vi.fn());
    expect(toasts[0]).toContain('Restored 1, 1 failed');
    expect(toasts[0]).toContain('already exists');
  });

  it('empty bin with nothing in it asks nothing and does nothing', async () => {
    await emptyDeletedBin(0, vi.fn());
    expect(confirms).toHaveLength(0);
  });
});
