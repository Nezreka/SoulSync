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
  it('lists them in order, pools first, with their tooltips', () => {
    // Discovery Pool and Wing It Pool moved up from the Mirrored tab's own
    // header: both are app-level overlays over everything, not one tab's
    // controls, and the Tools page opens the Discovery Pool through the same
    // seam.
    expect(SYNC_HEADER_ACTIONS.map((a) => a.label)).toEqual([
      'Discovery Pool',
      'Wing It Pool',
      'Auto-Sync',
      'Library Match',
      'Sync History',
      'Download Origins',
    ]);
    expect(SYNC_HEADER_ACTIONS.map((a) => a.key)).toEqual([
      'discovery-pool',
      'wing-it-pool',
      'auto-sync',
      'library-match',
      'sync-history',
      'download-origins',
    ]);
    expect(SYNC_HEADER_ACTIONS.every((a) => a.title.length > 0)).toBe(true);
  });
});

describe('the strip (syncStripTabs)', () => {
  it('shows three permanent chips, not fifteen', () => {
    // Six of the fifteen were duplicates of each other; the four paste-a-URL
    // tabs differed at the input step not at all. They are reached through Add
    // playlist now.
    expect(syncStripTabs('mirrored').map((t) => t.id)).toEqual(['mirrored', 'server', 'beatport']);
  });

  it('opens on Mirrored — the library, not a source directory', () => {
    expect(SYNC_DEFAULT_TAB).toBe('mirrored');
    expect(SYNC_PRIMARY_TAB_IDS).toContain(SYNC_DEFAULT_TAB);
  });

  it('shows a routed tab WHILE it is active, so there is a way back', () => {
    // A panel with no chip is a room with no door: the strip would highlight
    // nothing and the user could not leave.
    const ids = syncStripTabs('spotify-public').map((t) => t.id);
    expect(ids).toEqual(['mirrored', 'server', 'beatport', 'spotify-public']);
  });

  it('appends the routed chip, so the permanent three never move', () => {
    for (const routed of ['deezer-link', 'youtube', 'tidal', 'import-file']) {
      const ids = syncStripTabs(routed).map((t) => t.id);
      expect(ids.slice(0, 3)).toEqual(['mirrored', 'server', 'beatport']);
      expect(ids[3]).toBe(routed);
    }
  });

  it('drops the routed chip again once a permanent tab is active', () => {
    expect(syncStripTabs('server').map((t) => t.id)).toHaveLength(3);
  });

  it('never renders a chip twice when a permanent tab is active', () => {
    for (const id of SYNC_PRIMARY_TAB_IDS) {
      const ids = syncStripTabs(id).map((t) => t.id);
      expect(new Set(ids).size).toBe(ids.length);
    }
  });

  it('every tab is still REACHABLE even when it is not in the strip', () => {
    // The panels all still render; only the chips were reduced.
    const stripped = SYNC_TABS.filter((t) => !SYNC_PRIMARY_TAB_IDS.includes(t.id));
    expect(stripped.length).toBe(12);
    for (const t of stripped) {
      expect(syncStripTabs(t.id).map((x) => x.id)).toContain(t.id);
    }
  });
});
