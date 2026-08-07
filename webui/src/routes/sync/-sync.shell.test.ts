/**
 * Differential tests for the sync shell's pure core, against the tab markup at
 * index.html 2249-2295 and the header at 2237-2241.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { SYNC_DEFAULT_TAB, SYNC_HEADER_ACTIONS, SYNC_TABS, normalizeSyncTab } from './-sync.shell';

describe('normalizeSyncTab', () => {
  it('passes every real tab id through', () => {
    for (const t of SYNC_TABS) {
      expect(normalizeSyncTab(t.id)).toBe(t.id);
    }
  });

  it('takes anything else back to the default', () => {
    // The vanilla has no equivalent — it trusts dataset.tab and then does an
    // unguarded getElementById, which throws mid-handler on a bad id and
    // leaves the strip half-updated.
    expect(normalizeSyncTab('nonsense')).toBe('server');
    expect(normalizeSyncTab('')).toBe('server');
    expect(normalizeSyncTab(null)).toBe('server');
    expect(normalizeSyncTab(undefined)).toBe('server');
    // Near-misses, including the id the vanilla deliberately renamed away from.
    expect(normalizeSyncTab('listenbrainz')).toBe('server');
    expect(normalizeSyncTab('Server')).toBe('server');
    expect(normalizeSyncTab('spotify ')).toBe('server');
  });

  it('does not treat inherited Object keys as tab ids', () => {
    // A plain-object lookup would say yes to these; the table is a Set.
    expect(normalizeSyncTab('toString')).toBe('server');
    expect(normalizeSyncTab('constructor')).toBe('server');
    expect(normalizeSyncTab('__proto__')).toBe('server');
  });
});

describe('the tab table matches the markup it was transcribed from', () => {
  const HTML = readFileSync(resolve(__dirname, '../../../index.html'), 'utf8');
  const SHELL = HTML.split('\n').slice(2225, 3318).join('\n');

  it('has the same fifteen ids, in the same order', () => {
    const inMarkup = [
      ...SHELL.matchAll(/class="sync-tab-button[^"]*"[^>]*data-tab="([^"]+)"/g),
    ].map((m) => m[1]);
    expect(inMarkup).toHaveLength(15);
    expect(SYNC_TABS.map((t) => t.id)).toEqual(inMarkup);
  });

  it('has the same label for each, and uses it as the title too', () => {
    for (const t of SYNC_TABS) {
      const row = new RegExp(
        `data-tab="${t.id}"[^>]*title="([^"]+)"[\\s\\S]{0,160}?sync-tab-label">([^<]+)<`,
      ).exec(SHELL);
      expect(row, `markup row for ${t.id}`).not.toBeNull();
      expect(row?.[1]).toBe(t.label);
      expect(row?.[2]).toBe(t.label);
    }
  });

  it('has the same sprite for each', () => {
    for (const t of SYNC_TABS) {
      const row = new RegExp(`data-tab="${t.id}"[^>]*>\\s*<span class="tab-icon ([^"]+)"`).exec(
        SHELL,
      );
      expect(row?.[1], `sprite for ${t.id}`).toBe(t.icon);
    }
  });

  it('marks the same tabs as link tabs', () => {
    const inMarkup = [...SHELL.matchAll(/data-tab="([^"]+)" data-link="true"/g)].map((m) => m[1]);
    expect(SYNC_TABS.filter((t) => t.link).map((t) => t.id)).toEqual(inMarkup);
  });

  it('opens on the tab the markup marks active', () => {
    const active = /class="sync-tab-button[^"]*\bactive[^"]*"[^>]*data-tab="([^"]+)"/.exec(SHELL);
    expect(active?.[1]).toBe(SYNC_DEFAULT_TAB);
  });
});

describe('the header actions', () => {
  it('lists the four in markup order with their tooltips', () => {
    expect(SYNC_HEADER_ACTIONS.map((a) => a.label)).toEqual([
      'Auto-Sync',
      'Library Match',
      'Sync History',
      'Download Origins',
    ]);
    expect(SYNC_HEADER_ACTIONS.map((a) => a.key)).toEqual([
      'auto-sync',
      'library-match',
      'sync-history',
      'download-origins',
    ]);
    expect(SYNC_HEADER_ACTIONS.every((a) => a.title.length > 0)).toBe(true);
  });
});
