import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The media-server pickers, EXECUTED — not just grepped for.
 *
 * These eight functions were moved out of beatport-ui.js (3648-3931) into
 * settings.js because the sync port deletes that file and they drive the
 * Settings page's Plex / Jellyfin / Navidrome selects. Every other guard around
 * that move is source-level: it proves the TEXT is in the new file. None of
 * them proves the code still WORKS there.
 *
 * That gap is the whole lesson of this port — the compare editor, the slider
 * box and the disambiguation modal all had green tests while being visibly
 * broken. So this runs the real thing: real source, real DOM, stubbed fetch.
 *
 * It also proves the property that made the move safe in the first place — the
 * block is SELF-CONTAINED. It is evaluated in isolation from the rest of
 * settings.js, with nothing in scope but `document`, `fetch` and `showToast`.
 * If someone later adds a dependency on a settings.js helper, this stops
 * running and says so.
 */

/** The moved block: from its header marker to the end of the file. */
function pickerSource(): string {
  const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
  const marker = '// MEDIA-SERVER LIBRARY PICKERS';
  const at = source.indexOf(marker);
  expect(at, 'the media-server picker block is gone from settings.js').toBeGreaterThan(-1);
  return source.slice(at);
}

interface Pickers {
  loadPlexMusicLibraries: () => Promise<void>;
  selectPlexLibrary: () => Promise<void>;
  loadJellyfinUsers: () => Promise<void>;
  loadNavidromeMusicFolders: () => Promise<void>;
}

/**
 * Evaluate the block and hand back its functions. `new Function` rather than an
 * import: this is a classic script whose functions become globals, and the
 * point is to run it the way the browser does.
 */
function loadPickers(showToast: (...args: unknown[]) => void): Pickers {
  // Running the real classic script is the whole point here; same approach as
  // -adl.helpers.differential.test.ts.
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const factory = new Function(
    'document',
    'fetch',
    'showToast',
    'alert',
    `${pickerSource()}
     return { loadPlexMusicLibraries, selectPlexLibrary, loadJellyfinUsers,
              loadNavidromeMusicFolders };`,
  );
  return factory(document, globalThis.fetch, showToast, () => {}) as Pickers;
}

function html(markup: string) {
  document.body.innerHTML = markup;
}

const PLEX_DOM = `
  <div id="plex-library-selector-container" style="display: none">
    <select id="plex-music-library"></select>
  </div>`;

beforeEach(() => {
  document.body.innerHTML = '';
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the re-homed media-server pickers', () => {
  it('is self-contained — it evaluates with only document/fetch/showToast', () => {
    // The property the move depended on. A new dependency on a settings.js
    // helper would make `new Function` throw a ReferenceError the moment it
    // runs, which is exactly the signal wanted.
    expect(() => loadPickers(vi.fn())).not.toThrow();
  });

  it('populates the Plex select and reveals its container', async () => {
    html(PLEX_DOM);
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              success: true,
              // NOT the first option, deliberately. A select defaults to its
              // first option, so pre-selecting 'Podcasts' here would pass even
              // with the matching logic deleted — which is exactly what the
              // first version of this test did, and a mutation caught it.
              selected: 'Music',
              libraries: [{ title: 'Podcasts' }, { title: 'Music' }],
            }),
          ),
      ),
    );
    const { loadPlexMusicLibraries } = loadPickers(vi.fn());
    await loadPlexMusicLibraries();

    const select = document.getElementById('plex-music-library') as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(['Podcasts', 'Music']);
    // The saved pref pre-selects, which is what makes the page show the user's
    // actual library rather than the first one alphabetically.
    expect(select.value).toBe('Music');
    expect(
      (document.getElementById('plex-library-selector-container') as HTMLElement).style.display,
    ).toBe('block');
  });

  it('prefers `value` over `title`, for the All Libraries sentinel', async () => {
    html(PLEX_DOM);
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              success: true,
              selected: '__all__',
              // Sentinel second, for the same reason as above: first-option
              // default would otherwise pass a broken matcher.
              libraries: [{ title: 'Music' }, { title: 'All Libraries', value: '__all__' }],
            }),
          ),
      ),
    );
    const { loadPlexMusicLibraries } = loadPickers(vi.fn());
    await loadPlexMusicLibraries();

    const select = document.getElementById('plex-music-library') as HTMLSelectElement;
    expect(select.value).toBe('__all__');
    expect(select.options[1].textContent).toBe('All Libraries');
  });

  it('hides the container when the server reports nothing', async () => {
    html(PLEX_DOM.replace('display: none', 'display: block'));
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ success: true, libraries: [] }))),
    );
    const { loadPlexMusicLibraries } = loadPickers(vi.fn());
    await loadPlexMusicLibraries();
    expect(
      (document.getElementById('plex-library-selector-container') as HTMLElement).style.display,
    ).toBe('none');
  });

  it('hides the container when the fetch throws, rather than leaving a dead select', async () => {
    html(PLEX_DOM.replace('display: none', 'display: block'));
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('plex down');
      }),
    );
    const { loadPlexMusicLibraries } = loadPickers(vi.fn());
    await loadPlexMusicLibraries();
    expect(
      (document.getElementById('plex-library-selector-container') as HTMLElement).style.display,
    ).toBe('none');
  });

  it('POSTs the chosen library — the onchange handler index.html 4392 fires', async () => {
    html(`
      <select id="plex-music-library">
        <option value="Music">Music</option>
        <option value="Vinyl Rips" selected>Vinyl Rips</option>
      </select>`);
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ success: true })));
    vi.stubGlobal('fetch', fetchMock);

    const { selectPlexLibrary } = loadPickers(vi.fn());
    await selectPlexLibrary();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/plex/select-music-library');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ library_name: 'Vinyl Rips' });
  });

  it('reports a failed switch through showToast, never alert', async () => {
    // The block arrived from beatport-ui.js using `alert()` for its six error
    // paths while the Navidrome path beside it already used showToast — so it
    // was internally inconsistent as well as against the rule. `alert` is
    // injected as a spy here: if any path still reaches for it, this fails
    // rather than silently blocking the browser on a modal dialog.
    html('<select id="plex-music-library"><option value="Music" selected>Music</option></select>');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ success: false, error: 'no such library' }))),
    );
    const showToast = vi.fn();
    const alertSpy = vi.fn();
    // As above, but with `alert` bound to a spy so a surviving call is
    // observable rather than silently blocking on a modal.
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const factory = new Function(
      'document',
      'fetch',
      'showToast',
      'alert',
      `${pickerSource()}\n return { selectPlexLibrary };`,
    );
    const { selectPlexLibrary } = factory(
      document,
      globalThis.fetch,
      showToast,
      alertSpy,
    ) as Pickers;
    await selectPlexLibrary();

    expect(alertSpy).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith(
      'Failed to switch library: no such library',
      'error',
      'set-media',
    );
  });

  it('has no alert/confirm/prompt left anywhere in the block', () => {
    // Source-level backstop for the five paths the behavioural test above does
    // not drive. Comments are stripped first, since the header note discusses
    // the very calls it is checking for.
    const code = pickerSource().replace(/^\s*\/\/.*$/gm, '');
    expect(code).not.toMatch(/\b(?:window\.)?(?:alert|confirm|prompt)\s*\(/);
  });

  it('sends nothing when no library is selected', async () => {
    html('<select id="plex-music-library"></select>');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { selectPlexLibrary } = loadPickers(vi.fn());
    await selectPlexLibrary();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('drives the Jellyfin and Navidrome selects too', async () => {
    // Same shape, different endpoints — worth asserting because all three were
    // moved together and a partial move would look fine on the Plex path.
    html(`
      <div id="jellyfin-user-selector-container" style="display:none">
        <select id="jellyfin-user"></select>
      </div>
      <div id="navidrome-folder-selector-container" style="display:none">
        <select id="navidrome-music-folder"></select>
      </div>`);
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(url);
        return new Response(
          JSON.stringify({
            success: true,
            users: [{ name: 'boulder', id: 'u1' }],
            folders: [{ name: 'Music', id: 'f1' }],
          }),
        );
      }),
    );
    const { loadJellyfinUsers, loadNavidromeMusicFolders } = loadPickers(vi.fn());
    await loadJellyfinUsers();
    await loadNavidromeMusicFolders();

    expect(urls).toContain('/api/jellyfin/users');
    expect(urls).toContain('/api/navidrome/music-folders');
  });
});
