import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The video Settings server-connection block, EXECUTED.
 *
 * Issue #1213: on a fresh install the Jellyfin/Plex fields emptied themselves.
 * Typing a URL and clicking into the key field fires `change` -> save -> re-read,
 * and the re-read wrote the response straight back over the inputs. Two ways that
 * ate what the user typed: the API answered with the inherited (empty) config
 * because a half-filled override isn't usable, and the write-back clobbered the
 * field the user had already moved into.
 *
 * The API half is covered in tests/test_video_api.py. This runs the real browser
 * half of it — real source, real DOM, stubbed fetch.
 */

/** The connection block: from its header marker to the Jellyfin user picker. */
function connSource(): string {
  const source = readFileSync(resolve(process.cwd(), 'static/video/video-settings.js'), 'utf8');
  const from = source.indexOf('// ── Server Connection');
  const to = source.indexOf('// ── Jellyfin user picker');
  expect(from, 'the server-connection block is gone from video-settings.js').toBeGreaterThan(-1);
  expect(to).toBeGreaterThan(from);
  return source.slice(from, to);
}

interface Conn {
  loadConn: () => Promise<void>;
  saveConn: (silent?: boolean) => Promise<void>;
}

/**
 * Evaluate the block on its own. Everything it needs from the rest of the IIFE
 * is injected, so a new dependency on a video-settings.js helper stops this test
 * with a ReferenceError instead of passing quietly.
 */
function loadConnBlock(fetchImpl: typeof fetch, toast = vi.fn()): Conn {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const factory = new Function(
    'document',
    'fetch',
    'CONN_URL',
    'SERVER_URL',
    'toast',
    'load',
    'loadJellyfinUsers',
    `${connSource()}
     return { loadConn, saveConn };`,
  );
  return factory(
    document,
    fetchImpl,
    '/api/video/server-config',
    '/api/video/server',
    toast,
    vi.fn(),
    vi.fn(),
  ) as Conn;
}

const DOM = `
  <div data-video-server-config="plex">
    <input data-video-conn="plex-url">
    <input data-video-conn="plex-token" type="password">
    <div data-video-conn-note="plex"></div>
  </div>
  <div data-video-server-config="jellyfin">
    <input data-video-conn="jellyfin-url">
    <input data-video-conn="jellyfin-key" type="password">
    <div data-video-conn-note="jellyfin"></div>
  </div>`;

function field(name: string): HTMLInputElement {
  return document.querySelector(`[data-video-conn="${name}"]`) as HTMLInputElement;
}
function noteText(server: string): string {
  return (document.querySelector(`[data-video-conn-note="${server}"]`) as HTMLElement).textContent ?? '';
}
function respond(body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body))) as unknown as typeof fetch;
}

beforeEach(() => {
  document.body.innerHTML = DOM;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('video settings — server connection', () => {
  it('keeps a half-filled connection on screen', async () => {
    // What the API now returns after the URL alone was saved.
    const { loadConn } = loadConnBlock(
      respond({
        plex: { base_url: '', token: '', has_token: false, inherited: false },
        jellyfin: { base_url: 'http://jf:8096', api_key: '', has_key: false, inherited: false },
      }),
    );
    await loadConn();
    expect(field('jellyfin-url').value).toBe('http://jf:8096');
    expect(noteText('jellyfin')).toBe('Add the API key to finish this connection.');
    expect(noteText('plex')).toBe('Not connected — add a server URL and token.');
  });

  it('says what is missing when only the key was entered', async () => {
    const { loadConn } = loadConnBlock(
      respond({
        plex: { base_url: '', token: '••••••••••••', has_token: true, inherited: false },
        jellyfin: {},
      }),
    );
    await loadConn();
    expect(field('plex-token').value).toBe('••••••••••••');
    expect(noteText('plex')).toBe('Add the server URL to finish this connection.');
  });

  it('does not overwrite the field being typed in', async () => {
    // The race behind the report: the save fired on blur into the key field, so
    // the refresh landed while the user was already typing the key.
    const { loadConn } = loadConnBlock(
      respond({
        plex: {},
        jellyfin: { base_url: 'http://jf:8096', api_key: '', has_key: false, inherited: false },
      }),
    );
    const key = field('jellyfin-key');
    key.focus();
    key.value = 'half-typ';
    await loadConn();
    expect(key.value).toBe('half-typ');
    expect(field('jellyfin-url').value).toBe('http://jf:8096'); // unfocused fields still refresh
  });

  it('still labels an inherited music connection', async () => {
    const { loadConn } = loadConnBlock(
      respond({
        plex: { base_url: 'http://p', token: '••••••••••••', has_token: true, inherited: true },
        jellyfin: {},
      }),
    );
    await loadConn();
    expect(noteText('plex')).toBe(
      'Inherited from your Music Plex connection — edit to use a different server for video.',
    );
  });

  it('posts both servers and re-reads, without echoing the mask back as a new secret', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(
        JSON.stringify({
          plex: {},
          jellyfin: { base_url: 'http://jf:8096', api_key: '••••••••••••', has_key: true, inherited: false },
        }),
      );
    }) as unknown as typeof fetch;
    const { saveConn } = loadConnBlock(fetchImpl);
    field('jellyfin-url').value = 'http://jf:8096';
    field('jellyfin-key').value = '••••••••••••';   // untouched masked secret
    await saveConn(true);

    const posted = JSON.parse(String(calls[0].init?.body));
    expect(calls[0].url).toBe('/api/video/server-config');
    expect(posted.jellyfin).toEqual({ base_url: 'http://jf:8096', api_key: '••••••••••••' });
    expect(posted.plex).toEqual({ base_url: '', token: '' });
    expect(calls[1].url).toBe('/api/video/server-config');  // the re-read
    expect(noteText('jellyfin')).toBe('Custom video connection.');
  });
});
