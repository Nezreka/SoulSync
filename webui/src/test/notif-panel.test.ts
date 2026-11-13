import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The notification panel's own logic, run from the real script: what a note is
 * about (a path), which notes are one family, how they are grouped, and the
 * filters. The panel is vanilla, so these lift the functions out of
 * downloads.js rather than re-implement them.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/downloads.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');

const PATH_RE = /const _NOTIF_PATH_RE = (\/.*\/);\n/.exec(JS)![1];

function lift<T>(names: string[], returns: string, scope: Record<string, unknown> = {}): T {
  const keys = Object.keys(scope);
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const fn = new Function(
    ...keys,
    `const _NOTIF_PATH_RE = ${PATH_RE};\n${names.map((n) => extractFunction(n, JS)).join('\n')}\nreturn ${returns};`,
  );
  return fn(...keys.map((k) => scope[k])) as T;
}

const esc = (s: unknown) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

describe('a path inside a note', () => {
  const pathOf = lift<(m: string) => { path: string; name: string } | null>(
    ['_notifPathOf'],
    '_notifPathOf',
  );

  it('is found whole, spaces and all, and named by its file', () => {
    const found = pathOf(
      'Could not write tags to /music/Kraftwerk/Computer World/03 Numbers.flac: permission denied',
    )!;
    expect(found.path).toBe('/music/Kraftwerk/Computer World/03 Numbers.flac');
    expect(found.name).toBe('03 Numbers.flac');
    expect(pathOf('Moved E:\\Music\\A B\\01 - Song.mp3 to the library')!.name).toBe(
      '01 - Song.mp3',
    );
  });

  it('is not seen in a date, a ratio or a web address', () => {
    expect(pathOf('Scanned 21/58 artists')).toBeNull();
    expect(pathOf('Finished on 11/12/2026')).toBeNull();
    expect(pathOf('Downloaded "Currents" (13 tracks) from Tidal')).toBeNull();
  });

  it('shows the file name in the sentence, escaped', () => {
    const html = lift<(m: string) => string>(
      ['_notifPathOf', '_notifMessageHTML'],
      '_notifMessageHTML',
      { _escToast: esc },
    );
    expect(html('Bad file /a/b/R&B.flac here')).toBe('Bad file <b>R&amp;B.flac</b> here');
    expect(html('<img src=x>')).toBe('&lt;img src=x&gt;');
  });
});

describe('notes that say the same thing', () => {
  const family = lift<(e: { type: string; message: string }) => string>(
    ['_notifFamily'],
    '_notifFamily',
  );

  it('are one family whatever the name and the numbers', () => {
    const a = family({ type: 'success', message: 'Downloaded "Currents" (13 tracks) from Tidal' });
    const b = family({ type: 'success', message: 'Downloaded "Lonerism" (12 tracks) from Tidal' });
    expect(a).toBe(b);
  });

  it('are not one family across kinds or sentences', () => {
    const done = family({ type: 'success', message: 'Downloaded "Currents" from Tidal' });
    expect(family({ type: 'error', message: 'Downloaded "Currents" from Tidal' })).not.toBe(done);
    expect(family({ type: 'success', message: 'Added "Currents" to the wishlist' })).not.toBe(done);
  });
});

describe('the feed', () => {
  const NOW = Date.now();
  const note = (id: string, message: string, type: string, minsAgo: number, read: boolean) => ({
    id,
    message,
    type,
    read,
    timestamp: NOW - minsAgo * 60000,
    helpSection: null,
    action: null,
  });

  function feed(history: unknown[], filter = 'all') {
    const state = { history, filter, openFamilies: {} };
    const html = lift<() => string>(
      [
        '_notifPathOf',
        '_notifMessageHTML',
        '_notifFamily',
        '_notifDayStart',
        '_notifSectionOf',
        '_notifTimeAgo',
        '_notifEntryHTML',
        '_notifEntriesHTML',
      ],
      '_notifEntriesHTML',
      {
        _notifState: state,
        _escToast: esc,
        _escAttr: esc,
        _notifIcons: { success: 'S', error: 'E', warning: 'W', info: 'I' },
        _notifGlyphs: { close: 'x' },
        _NOTIF_KIND_LABEL: { success: 'Done', error: 'Problem', warning: 'Warning', info: 'Info' },
      },
    )();
    const host = document.createElement('div');
    host.innerHTML = html;
    return host;
  }

  it('puts what is new first, under its own heading, with how many', () => {
    const host = feed([note('1', 'Fresh', 'info', 1, false), note('2', 'Seen', 'info', 30, true)]);
    const heads = Array.from(host.querySelectorAll('.notif-section')).map((h) => h.textContent);
    expect(heads[0]).toBe('New1');
    expect(host.querySelectorAll('.notif-entry--new')).toHaveLength(1);
    expect(host.querySelector('.notif-entry--new .notif-entry-msg')!.textContent).toBe('Fresh');
  });

  it('folds three like notes into one that says how many more', () => {
    const run = ['A', 'B', 'C', 'D'].map((name, i) =>
      note(String(i), `Downloaded "${name}" from Tidal`, 'success', i + 1, false),
    );
    const host = feed(run);
    expect(host.querySelectorAll('.notif-entry')).toHaveLength(1);
    expect(host.querySelector('.notif-entry-more')!.textContent).toBe('and 3 more like this');
    // two alike are just two notes
    expect(feed(run.slice(0, 2)).querySelectorAll('.notif-entry')).toHaveLength(2);
  });

  it('gives a note with a path its path line and a way to copy it', () => {
    const host = feed([note('1', 'Could not read /m/a b/01 x.flac: denied', 'error', 1, false)]);
    expect(host.querySelector('.notif-entry-path')!.textContent).toBe('/m/a b/01 x.flac');
    expect(host.querySelector('.notif-entry-link')!.textContent).toBe('Copy path');
  });

  it('says so when there is nothing, and when a filter leaves nothing', () => {
    expect(feed([]).textContent).toContain('all caught up');
    expect(feed([note('1', 'x', 'info', 1, true)], 'error').textContent).toContain(
      'Nothing with this filter',
    );
  });
});

describe('the filters', () => {
  function chips(history: Array<{ type: string }>, filter = 'all') {
    const host = document.createElement('div');
    host.innerHTML = lift<() => string>(['_notifFilterChipsHTML'], '_notifFilterChipsHTML', {
      _notifState: { history, filter },
      _NOTIF_FILTERS: [
        ['all', 'All'],
        ['error', 'Problems'],
        ['warning', 'Warnings'],
        ['success', 'Done'],
        ['info', 'Info'],
      ],
    })();
    return Array.from(host.querySelectorAll('.notif-filter-chip')).map((c) => c.textContent);
  }

  it('are named, counted, and leave out a kind with nothing in it', () => {
    expect(chips([{ type: 'error' }, { type: 'error' }, { type: 'success' }])).toEqual([
      'All3',
      'Problems2',
      'Done1',
    ]);
  });

  it('keep the one you are on even when it empties', () => {
    expect(chips([{ type: 'success' }], 'warning')).toEqual(['All1', 'Warnings0', 'Done1']);
  });
});

describe('every class the panel emits is styled', () => {
  it('has a rule for each', () => {
    for (const cls of [
      '.notif-section',
      '.notif-entry--new',
      '.notif-entry-path',
      '.notif-entry-x',
      '.notif-entry-more',
      '.notif-icon-btn',
      '.notif-active--folded',
      '.notif-active-more',
      '.notif-health-facts',
      '.notif-history-searchbox',
      '.toast-compact-timer',
    ]) {
      expect(CSS, `${cls} is emitted but never styled`).toContain(cls);
    }
  });
});
