/**
 * Parity tests for the blocklist modal - the first classic-script TS port.
 *
 * Pins the vanilla file's observable behavior: the exact overlay markup ids
 * and classes (style.css targets them), the 300ms debounce with the
 * out-of-order guard, html-escaping through window.escapeHtml, the block /
 * unblock request shapes, and the window-export census the remaining classic
 * scripts and inline onclick handlers rely on.
 */

import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
  onBlocklistSearchInput,
  openBlocklistModal,
  switchBlocklistTab,
  unblockEntry,
} from './blocklist';
import { SHELL_WINDOW_EXPORTS } from './index';

let toasts: string[] = [];

beforeEach(() => {
  document.body.innerHTML = '';
  toasts = [];
  window.showToast = (m: string) => {
    toasts.push(m);
  };
  window.escapeHtml = (text: unknown) => {
    const div = document.createElement('div');
    div.textContent = String(text ?? '');
    return div.innerHTML;
  };
  server.use(
    http.get('/api/blocklist', () => HttpResponse.json({ success: true, entries: [] })),
  );
});

afterEach(() => {
  vi.useRealTimers();
  server.resetHandlers();
  delete window.showToast;
  delete window.escapeHtml;
  document.getElementById('blocklist-modal-overlay')?.remove();
});

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('the modal shell', () => {
  it('creates the overlay under the VANILLA ids and classes, and reuses it', () => {
    openBlocklistModal('artist');
    const overlay = document.getElementById('blocklist-modal-overlay')!;
    expect(overlay.className).toBe('modal-overlay blocklist-modal-overlay');
    for (const id of ['blocklist-search-input', 'blocklist-search-spinner',
                      'blocklist-search-results', 'blocklist-current']) {
      expect(document.getElementById(id), id).not.toBeNull();
    }
    expect(document.querySelectorAll('.blocklist-tab')).toHaveLength(3);
    openBlocklistModal('artist');
    expect(document.querySelectorAll('#blocklist-modal-overlay')).toHaveLength(1);
  });

  it('an unknown initial type falls back to artist', () => {
    openBlocklistModal('nonsense');
    const active = document.querySelector('.blocklist-tab.active') as HTMLElement;
    expect(active.dataset.bl).toBe('artist');
  });

  it('switching tabs moves the active class, retitles the placeholder and clears results', async () => {
    openBlocklistModal('artist');
    document.getElementById('blocklist-search-results')!.innerHTML = 'stale';
    switchBlocklistTab('album');
    await settle();
    const active = document.querySelector('.blocklist-tab.active') as HTMLElement;
    expect(active.dataset.bl).toBe('album');
    const input = document.getElementById('blocklist-search-input') as HTMLInputElement;
    expect(input.placeholder).toBe('Search albums to block…');
    expect(document.getElementById('blocklist-search-results')!.innerHTML).toBe('');
  });
});

describe('search', () => {
  it('debounces 300ms and html-escapes result names', async () => {
    vi.useFakeTimers();
    const hits: string[] = [];
    server.use(
      http.get('/api/blocklist/search', ({ request }) => {
        hits.push(new URL(request.url).searchParams.get('q') ?? '');
        return HttpResponse.json({
          success: true,
          source: 'deezer',
          results: [{ id: 1, name: '<img src=x onerror=alert(1)>', extra: 'Evil' }],
        });
      }),
    );
    openBlocklistModal('artist');
    const input = document.getElementById('blocklist-search-input') as HTMLInputElement;
    input.value = 'ev';
    onBlocklistSearchInput();
    input.value = 'evil';
    onBlocklistSearchInput();
    await vi.advanceTimersByTimeAsync(350);
    await vi.runOnlyPendingTimersAsync();
    expect(hits).toEqual(['evil']);
    const box = document.getElementById('blocklist-search-results')!;
    expect(box.innerHTML).toContain('&lt;img');
    expect(box.querySelector('img[onerror="alert(1)"]')).toBeNull();
  });

  it('an empty query renders nothing and fires no request', async () => {
    vi.useFakeTimers();
    const spy = vi.fn();
    server.use(http.get('/api/blocklist/search', () => { spy(); return HttpResponse.json({ success: true, results: [] }); }));
    openBlocklistModal('artist');
    onBlocklistSearchInput();
    await vi.advanceTimersByTimeAsync(350);
    await vi.runOnlyPendingTimersAsync();
    expect(spy).not.toHaveBeenCalled();
    expect(document.getElementById('blocklist-search-results')!.innerHTML).toBe('');
  });
});

describe('unblock', () => {
  it('DELETEs the entry, toasts, and reloads the current list', async () => {
    const deleted: string[] = [];
    server.use(
      http.delete('/api/blocklist/:id', ({ params }) => {
        deleted.push(String(params.id));
        return HttpResponse.json({ success: true });
      }),
    );
    openBlocklistModal('artist');
    await settle();
    await unblockEntry(42);
    expect(deleted).toEqual(['42']);
    expect(toasts).toContain('Removed from blocklist');
  });

  it('a refused delete surfaces the server error', async () => {
    server.use(
      http.delete('/api/blocklist/:id', () =>
        HttpResponse.json({ success: false, error: 'nope' })),
    );
    openBlocklistModal('artist');
    await settle();
    await unblockEntry(7);
    expect(toasts).toContain('nope');
  });
});

describe('the window contract', () => {
  it('exports exactly the names the classic scripts and onclick handlers use', () => {
    expect(Object.keys(SHELL_WINDOW_EXPORTS).sort()).toEqual([
      'blockFromSearch',
      'closeBlocklistModal',
      'closeDownloadOriginsModal',
      'closeMyAccountsModal',
      'closeServiceSwitchModal',
      'closeWatchlistHistoryModal',
      'connectMyAccount',
      'deleteSelectedOriginEntries',
      'disconnectMyAccount',
      'onBlocklistSearchInput',
      'openBlocklistModal',
      'openDownloadOriginsModal',
      'openMyAccountsModal',
      'openServiceSwitchModal',
      'openWatchlistHistoryModal',
      'saveMyAccountToken',
      'setActiveSource',
      'setDownloadMode',
      'switchBlocklistTab',
      'switchDownloadOriginTab',
      'switchServiceSwitchTab',
      'toggleAllOriginEntries',
      'toggleOriginEntry',
      'toggleOriginGroup',
      'toggleWatchlistHistoryRun',
      'unblockEntry',
    ]);
    // importing the entry assigned them
    expect(window.openBlocklistModal).toBe(SHELL_WINDOW_EXPORTS.openBlocklistModal);
  });
});
