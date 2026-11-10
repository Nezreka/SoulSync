import { waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

/**
 * #2029: named video libraries. Settings > Folders > Video lists every library
 * (the main and 4K ones with their folders set above, custom ones with their
 * own), adds, saves, makes default and removes; the Get / Manual search
 * window picks a title's library.
 */

const SETTINGS = readFileSync(resolve(process.cwd(), 'static/video/video-settings.js'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const VIEW = readFileSync(resolve(process.cwd(), 'static/video/video-download-view.js'), 'utf8');

const LIBRARIES = [
  {
    id: 1,
    kind: 'movie',
    name: 'Movies',
    path: '/movies',
    builtin: 'main',
    enabled: true,
    usable: true,
    is_default: true,
    rules: { genres: [], keywords: [] },
  },
  {
    id: 2,
    kind: 'movie',
    name: 'Movies 4K',
    path: '',
    builtin: '4k',
    enabled: false,
    usable: false,
    is_default: false,
    rules: { genres: [], keywords: [] },
  },
  {
    id: 5,
    kind: 'movie',
    name: 'Stand-Up "Specials"',
    path: '/standup',
    builtin: null,
    enabled: true,
    usable: true,
    is_default: false,
    rules: { genres: ['Comedy'], keywords: ['stand-up'] },
  },
];

function setup() {
  const doc = new DOMParser().parseFromString(HTML, 'text/html');
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    const body = (v: unknown) => new Response(JSON.stringify(v));
    if (url === '/api/video/libraries/folders' && !init?.method) {
      return body({ libraries: LIBRARIES, genres: ['Comedy', 'Documentary'] });
    }
    if (url.startsWith('/api/video/libraries/folders')) return body({ success: true });
    if (url.includes('/organization')) return body({ movie_template: '$title' });
    if (url.includes('/quality/profiles')) {
      return body({
        profiles: [
          { id: 0, name: 'Default' },
          { id: 7, name: 'UHD' },
        ],
      });
    }
    if (url.includes('/libraries/drives')) return body({ folders: [] });
    return body({ movies_path: '/movies', tv_path: '/tv' });
  });
  const confirm = vi.fn(async () => true);
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function('document', 'window', 'fetch', 'showConfirmDialog', SETTINGS)(
    doc,
    {},
    fetcher,
    confirm,
  );
  doc.dispatchEvent(new Event('DOMContentLoaded'));
  doc.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
  return { doc, fetcher, confirm };
}

const calls = (f: ReturnType<typeof vi.fn>) => f.mock.calls as unknown as [string, RequestInit?][];

describe('settings: more libraries', () => {
  it('lists every library, builtins without a folder box or remove', async () => {
    const { doc } = setup();
    await waitFor(() => expect(doc.querySelectorAll('.video-library-row').length).toBe(3));
    const [main, fourK, custom] = Array.from(doc.querySelectorAll('.video-library-row'));
    expect(main.querySelector('.video-library-path')).toBeNull();
    expect(main.querySelector('.video-library-known')!.textContent).toContain('/movies');
    expect(main.querySelector('[data-library-action="remove"]')).toBeNull();
    expect(main.querySelector('[data-library-action="default"]')).toBeNull();
    expect(main.querySelector('.video-library-state')!.textContent).toBe('Default for new movies');
    expect(fourK.querySelector('.video-library-state')!.textContent).toContain(
      'switch it on above',
    );
    expect(fourK.querySelector('[data-library-action="default"]')).toBeNull();
    // a custom one: its own folder (checked like every folder), rules, remove
    const path = custom.querySelector('.video-library-path') as HTMLInputElement;
    expect(path.value).toBe('/standup');
    expect(path.getAttribute('data-folder-check')).toBe('video');
    expect((custom.querySelector('.video-library-name') as HTMLInputElement).value).toBe(
      'Stand-Up "Specials"',
    );
    expect((custom.querySelector('.video-library-genres') as HTMLInputElement).value).toBe(
      'Comedy',
    );
    expect(custom.querySelector('[data-library-action="remove"]')).not.toBeNull();
    expect(custom.querySelector('[data-library-action="default"]')).not.toBeNull();
    expect(doc.querySelectorAll('#video-library-genre-list option').length).toBe(2);
  });

  it('adds a library with its kind, folder and rules', async () => {
    const { doc, fetcher } = setup();
    await waitFor(() => expect(doc.querySelectorAll('.video-library-row').length).toBe(3));
    (doc.getElementById('video-library-add') as HTMLButtonElement).click();
    (doc.getElementById('video-library-add') as HTMLButtonElement).click(); // one new row only
    const rows = doc.querySelectorAll('.video-library-row');
    expect(rows.length).toBe(4);
    const row = rows[3];
    (row.querySelector('.video-library-name') as HTMLInputElement).value = 'Kids TV';
    (row.querySelector('.video-library-kind-pick') as HTMLSelectElement).value = 'show';
    (row.querySelector('.video-library-path') as HTMLInputElement).value = ' /kids ';
    (row.querySelector('.video-library-genres') as HTMLInputElement).value = 'Kids, Family,';
    (row.querySelector('[data-library-action="save"]') as HTMLButtonElement).click();
    const post = calls(fetcher).find((c) => c[1]?.method === 'POST');
    expect(post![0]).toBe('/api/video/libraries/folders');
    expect(JSON.parse(post![1]!.body as string)).toEqual({
      name: 'Kids TV',
      kind: 'show',
      path: '/kids',
      enabled: true,
      rules: { genres: ['Kids', 'Family'], keywords: [] },
      quality_profile_id: 0,
      sources: [],
      torrent_category: '',
      usenet_category: '',
      indexer_ids: [],
      auto_search: true,
      upgrades: true,
    });
  });

  it('saves a builtin with only its name and rules', async () => {
    const { doc, fetcher } = setup();
    await waitFor(() => expect(doc.querySelectorAll('.video-library-row').length).toBe(3));
    const main = doc.querySelector('.video-library-row') as HTMLElement;
    (main.querySelector('.video-library-keywords') as HTMLInputElement).value = 'drama';
    // a default quality profile per library (#2029 step 2)
    const prof = main.querySelector('.video-library-profile') as HTMLSelectElement;
    expect(Array.from(prof.options).map((o) => o.textContent)).toEqual([
      'Default for new movies',
      'UHD',
    ]);
    prof.value = '7';
    (main.querySelector('[data-library-action="save"]') as HTMLButtonElement).click();
    const put = calls(fetcher).find((c) => c[1]?.method === 'PUT');
    expect(put![0]).toBe('/api/video/libraries/folders/1');
    expect(JSON.parse(put![1]!.body as string)).toEqual({
      name: 'Movies',
      rules: { genres: [], keywords: ['drama'] },
      quality_profile_id: 7,
      sources: [],
      torrent_category: '',
      usenet_category: '',
      indexer_ids: [],
      auto_search: true,
      upgrades: true,
    });
  });

  it('makes default and removes after asking', async () => {
    const { doc, fetcher, confirm } = setup();
    await waitFor(() => expect(doc.querySelectorAll('.video-library-row').length).toBe(3));
    const custom = doc.querySelectorAll('.video-library-row')[2];
    (custom.querySelector('[data-library-action="default"]') as HTMLButtonElement).click();
    expect(calls(fetcher).some((c) => c[0] === '/api/video/libraries/folders/5/default')).toBe(
      true,
    );
    (custom.querySelector('[data-library-action="remove"]') as HTMLButtonElement).click();
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ destructive: true }));
    await waitFor(() =>
      expect(calls(fetcher).some((c) => c[1]?.method === 'DELETE' && c[0].endsWith('/5'))).toBe(
        true,
      ),
    );
  });
});

describe('get window: the library picker', () => {
  const ESC = VIEW.slice(VIEW.indexOf('function esc'), VIEW.indexOf('function toast'));
  const SRC = VIEW.slice(
    VIEW.indexOf('function libraryPickerHTML'),
    VIEW.indexOf("// fetch the title's profile"),
  );
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const pick = new Function(`${ESC}; ${SRC}; return libraryPickerHTML;`)() as (
    i: unknown,
  ) => string;

  it('hides with one library and shows auto with the resolved one', () => {
    expect(pick({ libraries: [{ id: 1, name: 'Movies' }] })).toBe('');
    expect(pick(null)).toBe('');
    const html = pick({
      libraries: [
        { id: 1, name: 'Movies' },
        { id: 5, name: 'Stand-Up' },
      ],
      library: { id: 5, name: 'Stand-Up', why: 'rule: keyword stand-up' },
      library_assigned: 0,
    });
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const sel = doc.querySelector('[data-vdl-library]') as HTMLSelectElement;
    expect(Array.from(sel.options).map((o) => o.textContent)).toEqual([
      'Auto (Stand-Up)',
      'Movies',
      'Stand-Up',
    ]);
    expect(sel.value).toBe('0');
  });

  it('selects the title pick', () => {
    const html = pick({
      libraries: [
        { id: 1, name: 'Movies' },
        { id: 5, name: 'Stand-Up' },
      ],
      library: { id: 5, name: 'Stand-Up', why: 'assigned' },
      library_assigned: 5,
    });
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const sel = doc.querySelector('[data-vdl-library]') as HTMLSelectElement;
    expect(sel.value).toBe('5');
    expect(sel.options[0].textContent).toBe('Auto');
  });

  it('posts the pick to the title library endpoint', () => {
    const wired = VIEW.slice(
      VIEW.indexOf('function loadTitleProfile'),
      VIEW.indexOf('function renderTarget'),
    );
    expect(wired).toContain("closest('[data-vdl-library]')");
    expect(wired).toContain("'/api/video/downloads/quality/library'");
    expect(VIEW).toContain('profilePickerHTML(info) + libraryPickerHTML(info)');
  });
});

describe('get window: second copies (#2029 step 2)', () => {
  const ESC = VIEW.slice(VIEW.indexOf('function esc'), VIEW.indexOf('function toast'));
  const SRC = VIEW.slice(
    VIEW.indexOf('function copiesHTML'),
    VIEW.indexOf("// fetch the title's profile"),
  );
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const copies = new Function(`${ESC}; ${SRC}; return copiesHTML;`)() as (i: unknown) => string;
  const libraries = [
    { id: 1, name: 'Movies' },
    { id: 2, name: 'Movies 4K' },
    { id: 5, name: 'Stand-Up' },
  ];

  it('offers every library but the one the title files into', () => {
    const html = copies({ libraries, library: { id: 1, name: 'Movies' }, copies: [] });
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const opts = Array.from(doc.querySelectorAll('[data-vdl-copy-add] option')).map(
      (o) => o.textContent,
    );
    expect(opts).toEqual(['Also want a copy in…', 'Movies 4K', 'Stand-Up']);
  });

  it('lists the copies with their state and a remove', () => {
    const html = copies({
      libraries,
      library: { id: 1, name: 'Movies' },
      copies: [{ library_id: 2, name: 'Movies 4K', wished: true, owned: false }],
    });
    const doc = new DOMParser().parseFromString(html, 'text/html');
    expect(doc.querySelector('.vdl-copy')!.textContent).toContain('Movies 4K · wanted');
    expect(doc.querySelector('[data-vdl-copy-remove="2"]')).not.toBeNull();
    const opts = Array.from(doc.querySelectorAll('[data-vdl-copy-add] option')).map(
      (o) => o.textContent,
    );
    expect(opts).toEqual(['Also want a copy in…', 'Stand-Up']);
  });

  it('hides for a show, one library, or an old server', () => {
    expect(copies({ libraries, library: null })).toBe('');
    expect(copies({ libraries: [libraries[0]], copies: [] })).toBe('');
    expect(copies(null)).toBe('');
  });

  it('posts and deletes copies on the copies endpoint', () => {
    const wired = VIEW.slice(
      VIEW.indexOf('function loadTitleProfile'),
      VIEW.indexOf('function renderTarget'),
    );
    expect(wired).toContain("'/api/video/downloads/quality/copies'");
    expect(wired).toContain("copyCall('POST'");
    expect(wired).toContain("copyCall('DELETE'");
    expect(VIEW).toContain('libraryPickerHTML(info) + copiesHTML(info)');
  });
});

describe('settings: a library picks its sources and categories (#2029 step 3)', () => {
  it('orders the sources by the order they are picked and saves categories', async () => {
    const { doc, fetcher } = setup();
    await waitFor(() => expect(doc.querySelectorAll('.video-library-row').length).toBe(3));
    const row = () => doc.querySelectorAll('.video-library-row')[2] as HTMLElement;
    const click = (src: string) =>
      (row().querySelector(`[data-library-src="${src}"]`) as HTMLButtonElement).click();
    expect(row().querySelector('.video-library-hint')!.textContent).toBe('the global order');
    click('usenet');
    click('torrent');
    click('soulseek');
    click('torrent'); // off again
    const on = Array.from(row().querySelectorAll('.video-library-src.is-on')).map(
      (b) => b.textContent,
    );
    expect(on).toEqual(['2 Soulseek', '1 Usenet']);
    (row().querySelector('.video-library-tcat') as HTMLInputElement).value = 'standup';
    (row().querySelector('.video-library-ucat') as HTMLInputElement).value = 'standup-nzb';
    (row().querySelector('[data-library-action="save"]') as HTMLButtonElement).click();
    const put = calls(fetcher).find((c) => c[1]?.method === 'PUT');
    const body = JSON.parse(put![1]!.body as string);
    expect(body.sources).toEqual(['usenet', 'soulseek']);
    expect(body.torrent_category).toBe('standup');
    expect(body.usenet_category).toBe('standup-nzb');
  });
});

describe('settings: a library picks its indexers (#2029 step 4)', () => {
  it('lists prowlarr indexers when opened and saves the ticked ones', async () => {
    const { doc, fetcher } = setup();
    fetcher.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/api/prowlarr/indexers') {
        return new Response(
          JSON.stringify({
            success: true,
            indexers: [
              { id: 3, name: 'NZBgeek', protocol: 'usenet' },
              { id: 7, name: 'TorrentLeech', protocol: 'torrent' },
            ],
          }),
        );
      }
      if (url === '/api/video/libraries/folders' && !init?.method) {
        return new Response(JSON.stringify({ libraries: LIBRARIES, genres: [] }));
      }
      return new Response(JSON.stringify({ success: true }));
    });
    await waitFor(() => expect(doc.querySelectorAll('.video-library-row').length).toBe(3));
    const row = doc.querySelectorAll('.video-library-row')[2] as HTMLElement;
    const box = row.querySelector('.video-library-indexers') as HTMLDetailsElement;
    expect(box.querySelector('summary')!.textContent).toBe('Indexers: every one');
    box.open = true;
    box.dispatchEvent(new Event('toggle'));
    await waitFor(() => expect(row.querySelectorAll('.video-library-idx').length).toBe(2));
    (row.querySelector('.video-library-idx[value="7"]') as HTMLInputElement).checked = true;
    (row.querySelector('[data-library-action="save"]') as HTMLButtonElement).click();
    const put = calls(fetcher).find((c) => c[1]?.method === 'PUT');
    expect(JSON.parse(put![1]!.body as string).indexer_ids).toEqual([7]);
  });
});

describe('settings: automatic search and upgrades per library (#2029 step 5)', () => {
  it('saves the two switches', async () => {
    const { doc, fetcher } = setup();
    await waitFor(() => expect(doc.querySelectorAll('.video-library-row').length).toBe(3));
    const row = doc.querySelectorAll('.video-library-row')[2] as HTMLElement;
    const auto = row.querySelector('.video-library-auto') as HTMLInputElement;
    expect(auto.checked).toBe(true);
    auto.checked = false;
    (row.querySelector('[data-library-action="save"]') as HTMLButtonElement).click();
    const put = calls(fetcher).find((c) => c[1]?.method === 'PUT');
    const body = JSON.parse(put![1]!.body as string);
    expect(body.auto_search).toBe(false);
    expect(body.upgrades).toBe(true);
  });
});
