/**
 * Differential tests for the sync shell's pure core, against the tab markup at
 * index.html 2249-2295 and the header at 2237-2241 — now preserved in
 * `__fixtures__/-vanilla-sync-markup.html`, since the flip deleted the original.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  SYNC_DEFAULT_TAB,
  SYNC_HEADER_ACTIONS,
  SYNC_PRIMARY_TAB_IDS,
  SYNC_TABS,
  normalizeSyncTab,
  isPrimarySyncTab,
  syncStripTabs,
} from './-sync.shell';

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
    expect(normalizeSyncTab('nonsense')).toBe(SYNC_DEFAULT_TAB);
    expect(normalizeSyncTab('')).toBe(SYNC_DEFAULT_TAB);
    expect(normalizeSyncTab(null)).toBe(SYNC_DEFAULT_TAB);
    expect(normalizeSyncTab(undefined)).toBe(SYNC_DEFAULT_TAB);
    // Near-misses, including the id the vanilla deliberately renamed away from.
    expect(normalizeSyncTab('listenbrainz')).toBe(SYNC_DEFAULT_TAB);
    expect(normalizeSyncTab('Server')).toBe(SYNC_DEFAULT_TAB);
    expect(normalizeSyncTab('spotify ')).toBe(SYNC_DEFAULT_TAB);
  });

  it('does not treat inherited Object keys as tab ids', () => {
    // A plain-object lookup would say yes to these; the table is a Set.
    expect(normalizeSyncTab('toString')).toBe(SYNC_DEFAULT_TAB);
    expect(normalizeSyncTab('constructor')).toBe(SYNC_DEFAULT_TAB);
    expect(normalizeSyncTab('__proto__')).toBe(SYNC_DEFAULT_TAB);
  });
});

describe('the tab table matches the markup it was transcribed from', () => {
  /**
   * The vanilla markup, kept as a FIXTURE now that the flip has deleted it from
   * index.html. This slice used to read `index.html` lines 2225-3318 directly;
   * once those lines went, every assertion here matched an empty string and
   * five tests failed at once.
   *
   * Keeping the fixture rather than freezing the expectations as literals is
   * the same choice discover made (`__fixtures__/-vanilla-discover.js`), and
   * for the same reason: these are DIFFERENTIAL tests. Their value is that two
   * independently-written things agree. Transcribing the answers into the test
   * would leave it asserting that the table equals itself.
   */
  const SHELL = readFileSync(resolve(__dirname, '__fixtures__/-vanilla-sync-markup.html'), 'utf8');

  /**
   * drop items not in the vanilla JS code: ytmusic, my-playlists, and request-lists
   */
  const TRANSCRIBED_TABS = SYNC_TABS.filter(
    (t) => t.id !== 'ytmusic' && t.id !== 'my-playlists' && t.id !== 'request-lists',
  );

  it('has the same fifteen ids, in the same order', () => {
    const inMarkup = [
      ...SHELL.matchAll(/class="sync-tab-button[^"]*"[^>]*data-tab="([^"]+)"/g),
    ].map((m) => m[1]);
    expect(inMarkup).toHaveLength(15);
    expect(TRANSCRIBED_TABS.map((t) => t.id)).toEqual(inMarkup);
  });

  it('has the same label for each, and uses it as the title too', () => {
    for (const t of TRANSCRIBED_TABS) {
      const row = new RegExp(
        `data-tab="${t.id}"[^>]*title="([^"]+)"[\\s\\S]{0,160}?sync-tab-label">([^<]+)<`,
      ).exec(SHELL);
      expect(row, `markup row for ${t.id}`).not.toBeNull();
      expect(row?.[1]).toBe(t.label);
      expect(row?.[2]).toBe(t.label);
    }
  });

  it('has the same sprite for each', () => {
    for (const t of TRANSCRIBED_TABS) {
      const row = new RegExp(`data-tab="${t.id}"[^>]*>\\s*<span class="tab-icon ([^"]+)"`).exec(
        SHELL,
      );
      expect(row?.[1], `sprite for ${t.id}`).toBe(t.icon);
    }
  });

  it('marks the same tabs as link tabs', () => {
    const inMarkup = [...SHELL.matchAll(/data-tab="([^"]+)" data-link="true"/g)].map((m) => m[1]);
    expect(TRANSCRIBED_TABS.filter((t) => t.link).map((t) => t.id)).toEqual(inMarkup);
  });

  it('DIVERGES from the markup on which tab opens, deliberately', () => {
    // The vanilla markup marks Server Playlists active. The page now opens on
    // Mirrored: it is the persistent record of every playlist regardless of
    // source — the only surface holding sync state for all of them — and it sat
    // at position fifteen, a peer of the fourteen inputs feeding it.
    const active = /class="sync-tab-button[^"]*\bactive[^"]*"[^>]*data-tab="([^"]+)"/.exec(SHELL);
    expect(active?.[1]).toBe('server');
    expect(SYNC_DEFAULT_TAB).toBe('mirrored');
  });
});

describe('the header actions', () => {
  it('leads with the actions that CHANGE something', () => {
    // Bulk schedule sits first so it lands beside Add playlist: those two
    // decide what the page will do, and the four after them only report what
    // already happened. The pools moved up from the Mirrored tab's own header,
    // since both are app-level overlays over everything rather than one tab's
    // controls.
    expect(SYNC_HEADER_ACTIONS.map((a) => a.label)).toEqual([
      'Bulk schedule',
      'Match Review',
      'Wing It Pool',
      'Library Match',
      'Activity',
      'Download Origins',
    ]);
    expect(SYNC_HEADER_ACTIONS.map((a) => a.key)).toEqual([
      'auto-sync',
      'discovery-pool',
      'wing-it-pool',
      'library-match',
      'activity',
      'download-origins',
    ]);
    expect(SYNC_HEADER_ACTIONS.every((a) => a.title.length > 0)).toBe(true);
  });
});

describe('the pills (syncStripTabs)', () => {
  it('shows the four collections, and no source tabs', () => {
    // Every source tab (and Beatport) is a way of ADDING a playlist, so it
    // opens from Add as a panel, not as a place you live in.
    expect(syncStripTabs().map((t) => t.id)).toEqual([
      'mirrored',
      'my-playlists',
      'request-lists',
      'server',
    ]);
  });

  it('opens on Mirrored — the library, not a source directory', () => {
    expect(SYNC_DEFAULT_TAB).toBe('mirrored');
    expect(SYNC_PRIMARY_TAB_IDS).toContain(SYNC_DEFAULT_TAB);
  });

  it('every other tab is a source panel, and still exists', () => {
    const panels = SYNC_TABS.filter((t) => !isPrimarySyncTab(t.id));
    expect(panels.map((t) => t.id)).toContain('beatport');
    expect(panels.map((t) => t.id)).toContain('import-file');
    expect(panels.length).toBe(SYNC_TABS.length - 4);
  });
});
