import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { AdlDownload, AdlQuarantineEntry } from './-adl.types';

import {
  approveAllQuarantine,
  approveAllUnverified,
  cleanOrphans,
  clearAllQuarantine,
  deleteAllUnverified,
  quarantineApproveEntry,
  quarantineAudit,
  quarantineCompare,
  quarantineDeleteEntry,
  quarantinePlayEntry,
  quarantineRecoverEntry,
  reviewableHistoryIds,
  unverifiedApprove,
  unverifiedAudit,
  unverifiedCompare,
  unverifiedDelete,
  unverifiedPlay,
} from './-adl.verif-actions';

let toasts: { message: string; type?: string }[] = [];
let confirmCalls: Record<string, unknown>[] = [];
let confirmAnswer = true;

beforeEach(() => {
  toasts = [];
  confirmCalls = [];
  confirmAnswer = true;
  window.showToast = vi.fn((message: string, type?: string) => {
    toasts.push({ message, type });
  });
  window.showConfirmDialog = vi.fn((options?: Record<string, unknown>) => {
    confirmCalls.push(options ?? {});
    return Promise.resolve(confirmAnswer);
  });
  window.setTrackInfo = vi.fn();
  window.showLoadingAnimation = vi.fn();
  window.hideLoadingAnimation = vi.fn();
  window.startAudioPlayback = vi.fn();
  window.startStream = vi.fn();
  window.openDownloadAuditModal = vi.fn();
});

afterEach(() => {
  for (const key of [
    'showToast',
    'showConfirmDialog',
    'setTrackInfo',
    'showLoadingAnimation',
    'hideLoadingAnimation',
    'startAudioPlayback',
    'startStream',
    'openDownloadAuditModal',
  ] as const) {
    delete window[key];
  }
});

const entry = (over: Partial<AdlQuarantineEntry> = {}): AdlQuarantineEntry =>
  ({
    id: 'q1',
    expected_track: 'Xtal',
    expected_artist: 'Aphex Twin',
    original_filename: 'x.flac',
    has_full_context: true,
    ...over,
  }) as AdlQuarantineEntry;

const dl = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({
    task_id: 'history-42',
    title: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW',
    artwork: '/art.jpg',
    status: 'completed',
    verification_status: 'unverified',
    is_persistent_history: true,
    ...over,
  }) as AdlDownload;

const ok = (
  path: string,
  body: Record<string, unknown> = { success: true },
  method: 'post' | 'get' | 'delete' = 'post',
) => {
  const calls: unknown[] = [];
  server.use(
    http[method](path, async ({ request }) => {
      calls.push(request.method === 'POST' ? await request.text() : null);
      return HttpResponse.json(body);
    }),
  );
  return calls;
};

// ── Destructive actions must confirm FIRST ────────────────────────────────

describe('nothing destructive happens without a confirmation', () => {
  it('does not delete an unverified file when the dialog is declined', async () => {
    confirmAnswer = false;
    const calls = ok('/api/verification/:id/delete');
    const onDone = vi.fn();

    await unverifiedDelete('42', onDone);

    expect(confirmCalls).toHaveLength(1);
    expect(calls).toHaveLength(0);
    expect(onDone).not.toHaveBeenCalled();
  });

  it('does not delete a quarantined file when declined', async () => {
    confirmAnswer = false;
    const calls = ok('/api/quarantine/:id', { success: true }, 'delete');
    await quarantineDeleteEntry(entry(), vi.fn());
    expect(calls).toHaveLength(0);
  });

  it('does not delete ALL unverified files when declined', async () => {
    confirmAnswer = false;
    const calls = ok('/api/verification/:id/delete');
    await deleteAllUnverified(['1', '2', '3'], vi.fn());
    expect(calls).toHaveLength(0);
  });

  it('does not clear the quarantine when declined', async () => {
    confirmAnswer = false;
    const calls = ok('/api/quarantine/clear');
    await clearAllQuarantine([entry()], vi.fn());
    expect(calls).toHaveLength(0);
  });

  it('does not approve everything when declined', async () => {
    confirmAnswer = false;
    const unv = ok('/api/verification/:id/approve');
    await approveAllUnverified(['1'], vi.fn());
    expect(unv).toHaveLength(0);
  });

  it('does not clean orphans when declined', async () => {
    confirmAnswer = false;
    const calls = ok('/api/verification/clean-orphans');
    await cleanOrphans(vi.fn());
    expect(calls).toHaveLength(0);
  });

  it('marks the file-destroying dialogs as destructive', async () => {
    // The destructive flag is what makes the confirm button read as dangerous.
    confirmAnswer = false;
    await unverifiedDelete('42', vi.fn());
    await quarantineDeleteEntry(entry(), vi.fn());
    await deleteAllUnverified(['1'], vi.fn());
    await clearAllQuarantine([entry()], vi.fn());
    expect(confirmCalls.map((c) => c.destructive)).toEqual([true, true, true, true]);
  });

  it('does NOT mark the non-destructive ones destructive', async () => {
    // Approving and cleaning log rows destroy nothing; a red button there
    // trains people to click through the red buttons that matter.
    confirmAnswer = false;
    await approveAllUnverified(['1'], vi.fn());
    await cleanOrphans(vi.fn());
    await approveAllQuarantine([entry()], vi.fn());
    expect(confirmCalls.map((c) => c.destructive)).toEqual([undefined, undefined, undefined]);
  });

  it('names the count in the bulk dialogs, so the scale is visible', async () => {
    confirmAnswer = false;
    await deleteAllUnverified(['1', '2', '3'], vi.fn());
    expect(String(confirmCalls[0].message)).toContain('3');
    confirmCalls = [];
    await clearAllQuarantine([entry(), entry({ id: 'q2' })], vi.fn());
    expect(String(confirmCalls[0].message)).toContain('2');
  });
});

// ── Single actions ────────────────────────────────────────────────────────

describe('single unverified actions', () => {
  it('fills the player before requesting playback', async () => {
    ok('/api/verification/:id/play');
    await unverifiedPlay(dl(), '42');
    expect(window.setTrackInfo).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Xtal', artist: 'Aphex Twin', is_library: true }),
    );
    expect(window.startAudioPlayback).toHaveBeenCalled();
  });

  it('hides the loader and says so when playback fails', async () => {
    ok('/api/verification/:id/play', { success: false, error: 'file gone' });
    await unverifiedPlay(dl(), '42');
    expect(window.hideLoadingAnimation).toHaveBeenCalled();
    expect(window.startAudioPlayback).not.toHaveBeenCalled();
    expect(toasts[0].message).toContain('file gone');
  });

  it('names an untitled row rather than showing a blank player', async () => {
    ok('/api/verification/:id/play');
    await unverifiedPlay(undefined, '42');
    expect(window.setTrackInfo).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Review track' }),
    );
  });

  it('streams a comparison candidate and releases the busy flag', async () => {
    ok('/api/verification/:id/compare-stream', { success: true, result: { filename: 'a.mp3' } });
    const setBusy = vi.fn();
    await unverifiedCompare('42', setBusy);
    expect(window.startStream).toHaveBeenCalledWith({ filename: 'a.mp3' });
    expect(setBusy).toHaveBeenNthCalledWith(1, true);
    expect(setBusy).toHaveBeenLastCalledWith(false);
  });

  it('releases the busy flag even when the comparison finds nothing', async () => {
    // Otherwise the button stays disabled forever.
    ok('/api/verification/:id/compare-stream', { success: false, error: 'no candidate' });
    const setBusy = vi.fn();
    await unverifiedCompare('42', setBusy);
    expect(setBusy).toHaveBeenLastCalledWith(false);
    expect(toasts.some((t) => t.message === 'no candidate')).toBe(true);
  });

  it('opens the audit modal with the entry', async () => {
    ok('/api/verification/:id/entry', { success: true, entry: { id: 42 } }, 'get');
    await unverifiedAudit('42');
    expect(window.openDownloadAuditModal).toHaveBeenCalledWith({ id: 42 });
  });

  it('refreshes after a successful approve, not after a failed one', async () => {
    ok('/api/verification/:id/approve');
    const onDone = vi.fn();
    await unverifiedApprove('42', onDone);
    expect(onDone).toHaveBeenCalled();

    ok('/api/verification/:id/approve', { success: false, error: 'nope' });
    const onDone2 = vi.fn();
    await unverifiedApprove('42', onDone2);
    expect(onDone2).not.toHaveBeenCalled();
  });
});

describe('single quarantine actions', () => {
  it('marks the player title as quarantined', async () => {
    // So it cannot be mistaken for the imported copy.
    ok('/api/quarantine/:id/play');
    await quarantinePlayEntry(entry());
    expect(window.setTrackInfo).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Xtal (quarantined)' }),
    );
  });

  it('names the track it is searching for', async () => {
    ok('/api/quarantine/:id/compare-stream', { success: true, result: {} });
    await quarantineCompare(entry(), vi.fn());
    expect(toasts[0].message).toContain('Xtal');
  });

  it('opens the audit modal for a never-imported file', async () => {
    ok('/api/quarantine/:id/entry', { success: true, entry: { synthesised: true } }, 'get');
    await quarantineAudit(entry());
    expect(window.openDownloadAuditModal).toHaveBeenCalledWith({ synthesised: true });
  });

  it('reports how many duplicate candidates the approve removed', async () => {
    ok('/api/quarantine/:id/approve', { success: true, removed_siblings: ['a', 'b'] });
    await quarantineApproveEntry(entry(), vi.fn());
    expect(toasts[0].message).toContain('2 duplicate candidates removed');
  });

  it('singularises that count', async () => {
    ok('/api/quarantine/:id/approve', { success: true, removed_siblings: ['a'] });
    await quarantineApproveEntry(entry(), vi.fn());
    expect(toasts[0].message).toContain('1 duplicate candidate removed');
  });

  it('says nothing about siblings when none were removed', async () => {
    ok('/api/quarantine/:id/approve', { success: true, removed_siblings: [] });
    await quarantineApproveEntry(entry(), vi.fn());
    expect(toasts[0].message).not.toContain('duplicate');
  });

  it('points recovery at the Import page', async () => {
    ok('/api/quarantine/:id/recover');
    await quarantineRecoverEntry(entry({ has_full_context: false }), vi.fn());
    expect(toasts[0].message).toContain('Import page');
  });
});

// ── Bulk ──────────────────────────────────────────────────────────────────

describe('bulk actions', () => {
  it('selects exactly the rows the review filter shows', async () => {
    const ids = reviewableHistoryIds([
      dl({ task_id: 'history-1', status: 'completed', verification_status: 'unverified' }),
      dl({ task_id: 'history-2', status: 'skipped', verification_status: 'force_imported' }),
      dl({ task_id: 'history-3', status: 'completed', verification_status: 'verified' }),
      dl({ task_id: 'history-4', status: 'downloading', verification_status: 'unverified' }),
      // No id to act on — must not produce an undefined entry.
      dl({ task_id: 'live', is_persistent_history: false, history_id: null }),
    ]);
    expect(ids).toEqual(['1', '2']);
  });

  it('keeps going when one row fails, and reports the tally', async () => {
    let n = 0;
    server.use(
      http.post('/api/verification/:id/approve', () => {
        n += 1;
        return HttpResponse.json(n === 2 ? { success: false } : { success: true });
      }),
    );
    const onDone = vi.fn();
    await approveAllUnverified(['1', '2', '3'], onDone);
    expect(n).toBe(3);
    expect(toasts[0].message).toContain('Approved 2/3');
    expect(toasts[0].type).toBe('error');
    expect(onDone).toHaveBeenCalled();
  });

  it('does nothing at all for an empty selection', async () => {
    const calls = ok('/api/verification/:id/approve');
    await approveAllUnverified([], vi.fn());
    expect(confirmCalls).toHaveLength(0);
    expect(calls).toHaveLength(0);
  });

  it('skips legacy quarantine sidecars and explains why', async () => {
    const calls = ok('/api/quarantine/:id/approve');
    await approveAllQuarantine([entry({ has_full_context: false })], vi.fn());
    expect(calls).toHaveLength(0);
    expect(toasts[0].message).toContain('legacy sidecars need Recover');
    // Never even asked — there was nothing it could do.
    expect(confirmCalls).toHaveLength(0);
  });

  it('staggers bulk quarantine approvals', async () => {
    // Each approve spawns a server-side re-import thread; firing fifty at once
    // is a stampede.
    ok('/api/quarantine/:id/approve');
    const sleeps: number[] = [];
    await approveAllQuarantine(
      [entry(), entry({ id: 'q2' }), entry({ id: 'q3' })],
      vi.fn(),
      async (ms) => {
        sleeps.push(ms);
      },
    );
    expect(sleeps).toEqual([500, 500, 500]);
  });

  it('pluralises the clean-orphans result correctly', async () => {
    ok('/api/verification/clean-orphans', { success: true, removed: 1, checked: 10 });
    await cleanOrphans(vi.fn());
    expect(toasts[0].message).toBe('Removed 1 orphaned entry (checked 10)');

    toasts = [];
    ok('/api/verification/clean-orphans', { success: true, removed: 4, checked: 10 });
    await cleanOrphans(vi.fn());
    expect(toasts[0].message).toBe('Removed 4 orphaned entries (checked 10)');
  });

  it('does not clear an already-empty quarantine', async () => {
    const calls = ok('/api/quarantine/clear');
    await clearAllQuarantine([], vi.fn());
    expect(confirmCalls).toHaveLength(0);
    expect(calls).toHaveLength(0);
  });
});
