/**
 * Differential parity: run the REAL vanilla functions against the port.
 *
 * Reading a function and re-implementing it is how the search port shipped an
 * artist link that went nowhere — the code looked right and no test disagreed.
 * This file removes the judgement call: it lifts the original function bodies
 * out of the vanilla, evaluates them, and asserts the port produces
 * byte-identical output across a matrix of inputs including the awkward ones
 * (0, negative, null, non-finite, unknown enum values).
 *
 * The vanilla itself is gone now, so the source is a FROZEN FIXTURE captured
 * the moment before deletion. That is deliberate: converting these into
 * hand-written expectations would replace "matches the code it replaced" with
 * "matches what I believed the code did", which is exactly the failure mode
 * the file exists to rule out. These 16 functions have no other coverage.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { AdlDownload, AdlQuarantineEntry } from './-adl.types';

import {
  batchColorIndex,
  bundleProgressPercent,
  bundleProgressText,
  bundleStateLabel,
  formatBytes,
  formatDuration,
  formatSpeed,
  qualityChipTitle,
  quarantineSourceLabel,
  quarantineTrigger,
  reasonBadge,
  showQualityChip,
  sourceLabel,
  statusClass,
  statusLabel,
  unverifiedKey,
  verificationBadge,
  verificationHistoryId,
} from './-adl.helpers';

/**
 * The vanilla's source, as it stood immediately before deletion.
 *
 * Read from a committed fixture rather than from static/pages-extra.js, which
 * no longer contains this code. Keeping the ORIGINAL text means the parity
 * claim stays checkable: the port is still compared against the real functions
 * it replaced, byte for byte, rather than against expectations I typed out
 * myself. The fixture is frozen — nothing should ever edit it.
 */
const SOURCE = readFileSync(
  resolve(process.cwd(), 'src/routes/active-downloads/__fixtures__/-vanilla-adl.js'),
  'utf8',
);

/**
 * Pull one top-level `function name(...) { ... }` out of the vanilla by
 * brace-matching from its declaration. Regex alone cannot do this — these
 * bodies contain nested braces, template literals and object literals.
 */
function extractFunction(name: string): string {
  const start = SOURCE.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`vanilla function not found: ${name}`);
  const open = SOURCE.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < SOURCE.length; i++) {
    const ch = SOURCE[i];
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) return SOURCE.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced braces reading ${name}`);
}

/**
 * Evaluate the named vanilla functions together and hand them back.
 *
 * `preamble` supplies the module-level state some of them close over —
 * _getBatchColor memoises into _batchColorMap, so without it the extracted
 * function throws a ReferenceError rather than returning a colour.
 */
function loadVanilla<T extends Record<string, unknown>>(names: string[], preamble = ''): T {
  const body = names.map(extractFunction).join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${preamble}\n${body}\nreturn { ${names.join(', ')} };`)() as T;
}

const vanilla = loadVanilla<{
  _adlStatusClass: (s: string) => string;
  _adlStatusLabel: (s: string) => string;
  _adlFormatBytes: (b: unknown) => string;
  _adlFormatSpeed: (b: unknown) => string;
  _adlFmtDuration: (s: number) => string;
  _adlBundleProgressPercent: (b: unknown) => number;
  _adlSourceLabel: (s: unknown) => string;
  _adlBundleStateLabel: (s: unknown) => string;
  _adlBundleProgressText: (b: unknown) => string;
  _getBatchColor: (id: unknown) => number;
}>(
  [
    '_adlStatusClass',
    '_adlStatusLabel',
    '_adlFormatBytes',
    '_adlFormatSpeed',
    '_adlFmtDuration',
    '_adlBundleProgressPercent',
    '_adlSourceLabel',
    '_adlBundleStateLabel',
    '_adlBundleProgressText',
    '_getBatchColor',
  ],
  'const _batchColorMap = {}; let _batchColorNext = 0;',
);

/**
 * The verification helpers, which need `_adlEsc` and `_adlSourceLabel` in scope
 * because they escape into markup / reuse the source table.
 */
const vanillaVerif = loadVanilla<{
  verifHistoryId: (dl: unknown) => string | null;
  _verifUnvKey: (dl: unknown) => string;
  _adlVerifBadge: (dl: unknown) => string;
  _adlQualityBadge: (dl: unknown) => string;
  _verifReasonBadge: (dl: unknown) => string;
  _verifQuarSourceLabel: (q: unknown) => string;
}>(
  [
    'verifHistoryId',
    '_verifUnvKey',
    '_adlVerifBadge',
    '_adlQualityBadge',
    '_verifReasonBadge',
    '_verifQuarSourceLabel',
  ],
  [
    extractFunction('_adlEsc'),
    extractFunction('_adlSourceLabel'),
    // The module-level constant _verifQuarSourceLabel reads.
    /const _VERIF_QUAR_STREAMING_SOURCES = \[[^\]]*\];/.exec(SOURCE)?.[0] ?? '',
  ].join('\n'),
);

/** Every status the server can emit, plus junk the mapper must survive. */
const STATUSES = [
  'downloading',
  'searching',
  'post_processing',
  'queued',
  'pending',
  'completed',
  'skipped',
  'already_owned',
  'failed',
  'not_found',
  'cancelled',
  'unknown_future_status',
  '',
];

const NUMBERS = [
  0,
  1,
  511,
  512,
  1023,
  1024,
  1025,
  9999,
  10240,
  1048576,
  1073741824,
  1099511627776,
  -1,
  -1024,
  0.5,
  1.5,
  9.94,
  10.5,
  Number.NaN,
  Number.POSITIVE_INFINITY,
];

describe('status mapping is identical to the vanilla', () => {
  it('maps every status to the same class', () => {
    for (const status of STATUSES) {
      expect(statusClass(status), status).toBe(vanilla._adlStatusClass(status));
    }
  });

  it('produces the same label markup', () => {
    for (const status of STATUSES) {
      const mine = statusLabel(status);
      // The vanilla returns HTML; the port splits the spinner out so React can
      // render a node. Reassemble to compare like for like.
      const rebuilt = mine.spinner ? `<span class="adl-spinner"></span>${mine.text}` : mine.text;
      expect(rebuilt, status).toBe(vanilla._adlStatusLabel(status));
    }
  });
});

describe('formatters are identical to the vanilla', () => {
  it('formats bytes the same, including the decimal rule', () => {
    for (const n of NUMBERS) {
      expect(formatBytes(n), String(n)).toBe(vanilla._adlFormatBytes(n));
    }
    for (const junk of [null, undefined, '', 'abc', '2048']) {
      expect(formatBytes(junk), String(junk)).toBe(vanilla._adlFormatBytes(junk));
    }
  });

  it('formats speed the same', () => {
    for (const n of NUMBERS) {
      expect(formatSpeed(n), String(n)).toBe(vanilla._adlFormatSpeed(n));
    }
  });

  it('formats durations the same across every branch', () => {
    for (const s of [
      0,
      1,
      30,
      59,
      60,
      61,
      599,
      3599,
      3600,
      3601,
      7325,
      -5,
      Number.NaN,
      Number.POSITIVE_INFINITY,
    ]) {
      expect(formatDuration(s), String(s)).toBe(vanilla._adlFmtDuration(s));
    }
  });
});

describe('album bundle rendering is identical to the vanilla', () => {
  const BUNDLES = [
    null,
    {},
    { progress: 0 },
    { progress: 0.42 },
    { progress: 1 },
    { progress: 42 },
    { progress: 420 },
    { progress: -5 },
    { progress_percent: 66, progress: 12 },
    { progress_percent: Number.NaN },
    { state: 'searching', source: 'torrent' },
    { state: 'staged', source: 'usenet', release: 'Some Album' },
    { state: 'weird_state', source: 'nothing-known' },
    { state: 'downloading', source: 'soulseek', speed: '1.2 MB/s', size: '300 MB' },
    { state: 'downloading', release: 'X', progress_percent: 50, speed: '1 MB/s' },
  ];

  it('computes the same percent, including the <=1 rescale', () => {
    for (const b of BUNDLES) {
      expect(bundleProgressPercent(b), JSON.stringify(b)).toBe(
        vanilla._adlBundleProgressPercent(b),
      );
    }
  });

  it('builds the same progress sentence', () => {
    for (const b of BUNDLES) {
      expect(bundleProgressText(b), JSON.stringify(b)).toBe(vanilla._adlBundleProgressText(b));
    }
  });

  it('labels states the same, including the underscore fallback', () => {
    for (const s of [
      'searching',
      'downloading',
      'staged',
      'failed',
      'some_other_state',
      '',
      null,
      undefined,
    ]) {
      expect(bundleStateLabel(s), String(s)).toBe(vanilla._adlBundleStateLabel(s));
    }
  });
});

describe('source labels are identical to the vanilla', () => {
  it('covers every known source plus the fallbacks', () => {
    const sources = [
      'torrent',
      'usenet',
      'soulseek',
      'youtube',
      'tidal',
      'qobuz',
      'hifi',
      'deezer_dl',
      'amazon',
      'lidarr',
      'soundcloud',
      'TORRENT',
      'YouTube',
      'something-else',
      '',
      null,
      undefined,
    ];
    for (const s of sources) {
      expect(sourceLabel(s), String(s)).toBe(vanilla._adlSourceLabel(s));
    }
  });
});

describe('batch colour is identical to the vanilla', () => {
  it('hashes the same index for the same id', () => {
    const ids = [
      '',
      'a',
      'batch-1',
      'batch-2',
      'wishlist',
      'b1e7f2c0-4a3d-4f6e-9c1b-2f8a7d6e5c4b',
      'a-very-long-batch-identifier-with-lots-of-characters-in-it',
      '🎵-unicode-batch',
    ];
    for (const id of ids) {
      expect(batchColorIndex(id), id).toBe(vanilla._getBatchColor(id));
    }
    expect(batchColorIndex(null)).toBe(vanilla._getBatchColor(null));
    expect(batchColorIndex(undefined)).toBe(vanilla._getBatchColor(undefined));
  });

  it('stays inside the 8-colour palette', () => {
    for (let i = 0; i < 400; i++) {
      const idx = batchColorIndex(`batch-${i}`);
      expect(idx).toBeGreaterThanOrEqual(0);
      expect(idx).toBeLessThan(8);
    }
  });
});

describe('verification helpers are identical to the vanilla', () => {
  /** Rows spanning both server shapes plus the degenerate cases. */
  const ROWS: Partial<AdlDownload>[] = [
    {},
    { task_id: 'history-42', is_persistent_history: true },
    { task_id: 'history-42', is_persistent_history: false },
    { task_id: 'history-abc', is_persistent_history: true },
    { task_id: 'history-', is_persistent_history: true },
    { task_id: 'live-7', is_persistent_history: false, history_id: 99 },
    { task_id: 'live-7', is_persistent_history: false, history_id: '101' },
    { task_id: 'live-7', is_persistent_history: false, history_id: 0 },
    { task_id: 'live-7', is_persistent_history: false, history_id: null },
    { task_id: '', is_persistent_history: false },
    // A persistent row that ALSO carries history_id — the first branch wins.
    { task_id: 'history-5', is_persistent_history: true, history_id: 9 },
  ];

  it('derives the same history id', () => {
    for (const row of ROWS) {
      expect(verificationHistoryId(row as AdlDownload), JSON.stringify(row)).toBe(
        vanillaVerif.verifHistoryId(row),
      );
    }
  });

  it('derives the same unverified key', () => {
    for (const row of ROWS) {
      expect(unverifiedKey(row as AdlDownload), JSON.stringify(row)).toBe(
        vanillaVerif._verifUnvKey(row),
      );
    }
  });

  const STATUS_ROWS: Partial<AdlDownload>[] = [];
  for (const status of ['completed', 'downloading', 'failed', 'skipped']) {
    for (const vs of [
      'force_imported',
      'unverified',
      'verified',
      'human_verified',
      'something_else',
      null,
    ]) {
      STATUS_ROWS.push({ status, verification_status: vs });
    }
  }

  it('renders the same verification badge, including when it renders none', () => {
    for (const row of STATUS_ROWS) {
      const mine = verificationBadge(row as AdlDownload);
      const rebuilt = mine
        ? ` <span class="${mine.className}" title="${mine.title}">${mine.glyph}</span>`
        : '';
      expect(rebuilt, JSON.stringify(row)).toBe(vanillaVerif._adlVerifBadge(row));
    }
  });

  it('shows the quality chip on exactly the same rows', () => {
    const rows: Partial<AdlDownload>[] = [];
    for (const status of ['completed', 'downloading', 'failed', 'skipped', 'already_owned']) {
      for (const quality of ['FLAC 16/44', 'MP3 320', '', undefined]) {
        rows.push({ status, quality: quality as string });
      }
    }
    for (const row of rows) {
      const mine = showQualityChip(row as AdlDownload)
        ? ` <span class="adl-quality-chip" title="${qualityChipTitle()}">${row.quality}</span>`
        : '';
      expect(mine, JSON.stringify(row)).toBe(vanillaVerif._adlQualityBadge(row));
    }
  });

  it('renders the same reason badge', () => {
    for (const row of STATUS_ROWS) {
      const mine = reasonBadge(row as AdlDownload);
      const rebuilt = mine
        ? `<span class="${mine.className}" title="${mine.title}">${mine.label}</span>`
        : '';
      expect(rebuilt, JSON.stringify(row)).toBe(vanillaVerif._verifReasonBadge(row));
    }
  });

  it('labels the same quarantine source, collapsing peer names to Soulseek', () => {
    const entries = [
      'youtube',
      'tidal',
      'qobuz',
      'hifi',
      'deezer_dl',
      'lidarr',
      'soundcloud',
      'amazon',
      'torrent',
      'usenet',
      'YouTube',
      'TIDAL',
      'some_random_peer',
      'xX_uploader_Xx',
      '',
    ].map((source_username) => ({ source_username }));
    for (const entry of entries) {
      expect(quarantineSourceLabel(entry as AdlQuarantineEntry), entry.source_username).toBe(
        vanillaVerif._verifQuarSourceLabel(entry),
      );
    }
    // Missing field entirely.
    expect(quarantineSourceLabel({} as AdlQuarantineEntry)).toBe(
      vanillaVerif._verifQuarSourceLabel({}),
    );
  });
});

describe('quarantine triggers match the vanilla table', () => {
  it('maps each trigger and falls back to QUARANTINED', () => {
    // Read the table out of the source rather than restating it, so a change
    // to the vanilla shows up here.
    const table = /const _VERIF_QUAR_TRIGGERS = \{([\s\S]*?)\};/.exec(SOURCE);
    expect(table).not.toBeNull();
    const entries = [...table![1].matchAll(/(\w+):\s*\['([^']+)',\s*'([^']+)'\]/g)];
    expect(entries.length).toBe(4);
    for (const [, key, label, cls] of entries) {
      expect(quarantineTrigger(key), key).toEqual([label, cls]);
    }
    expect(quarantineTrigger('nope')).toEqual(['QUARANTINED', 'verif-rb-unv']);
    expect(quarantineTrigger(undefined)).toEqual(['QUARANTINED', 'verif-rb-unv']);
  });
});
