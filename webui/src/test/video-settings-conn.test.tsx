import { act, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  VideoServerConnection as Card,
  VideoServerFields,
} from '@/features/settings/tabs/connections-video-server';
import { videoServer } from '@/features/settings/tabs/video-server';

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
 * The API half is covered in tests/test_video_api.py. This runs the browser half
 * (React since SETTINGS_PORT.md video pass V3b: connections-video-server.tsx,
 * video-server.ts) with the same cases and a stubbed fetch.
 */

/** the card and the fields its sheet holds (settings-bic phase 3) */
function VideoServerConnection() {
  return (
    <>
      <Card />
      <VideoServerFields />
    </>
  );
}

let calls: Array<{ url: string; init?: RequestInit }>;
function respond(body: unknown) {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify(url === '/api/video/server-config' ? body : {}));
    }),
  );
}

function field(name: string): HTMLInputElement {
  return document.querySelector(`[data-video-conn="${name}"]`) as HTMLInputElement;
}
function noteText(server: string): string {
  return (
    (document.querySelector(`[data-video-conn-note="${server}"]`) as HTMLElement).textContent ?? ''
  );
}
/** loadConn, as the page showing ran it */
async function loadConn() {
  render(<VideoServerConnection />);
  act(() => {
    document.dispatchEvent(
      new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
    );
  });
  await waitFor(() =>
    expect(calls.filter((c) => c.url === '/api/video/server-config').length).toBeGreaterThan(0),
  );
  await waitFor(() => expect(noteText('plex')).not.toBe(''));
}

beforeEach(() => {
  videoServer.reset();
  (window as Window & { showToast?: unknown }).showToast = vi.fn();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('video settings — server connection', () => {
  it('keeps a half-filled connection on screen', async () => {
    // What the API now returns after the URL alone was saved.
    respond({
      plex: { base_url: '', token: '', has_token: false, inherited: false },
      jellyfin: { base_url: 'http://jf:8096', api_key: '', has_key: false, inherited: false },
    });
    await loadConn();
    expect(field('jellyfin-url').value).toBe('http://jf:8096');
    expect(noteText('jellyfin')).toBe('Add the API key to finish this connection.');
    expect(noteText('plex')).toBe('Not connected: add a server URL and token.');
  });

  it('says what is missing when only the key was entered', async () => {
    respond({
      plex: { base_url: '', token: '••••••••••••', has_token: true, inherited: false },
      jellyfin: {},
    });
    await loadConn();
    expect(field('plex-token').value).toBe('••••••••••••');
    expect(noteText('plex')).toBe('Add the server URL to finish this connection.');
  });

  it('does not overwrite the field being typed in', async () => {
    // The race behind the report: the save fired on blur into the key field, so
    // the refresh landed while the user was already typing the key.
    respond({
      plex: {},
      jellyfin: { base_url: 'http://jf:8096', api_key: '', has_key: false, inherited: false },
    });
    render(<VideoServerConnection />);
    const key = field('jellyfin-key');
    key.focus();
    act(() => videoServer.type('jellyfin-key', 'half-typ'));
    act(() => {
      document.dispatchEvent(
        new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
      );
    });
    await waitFor(() => expect(noteText('jellyfin')).not.toBe(''));
    expect(key.value).toBe('half-typ');
    expect(field('jellyfin-url').value).toBe('http://jf:8096'); // unfocused fields still refresh
  });

  it('still labels an inherited music connection', async () => {
    respond({
      plex: { base_url: 'http://p', token: '••••••••••••', has_token: true, inherited: true },
      jellyfin: {},
    });
    await loadConn();
    expect(noteText('plex')).toBe(
      'Inherited from your music Plex connection. Change it to use a different server for video.',
    );
  });

  it('posts both servers and re-reads, without echoing the mask back as a new secret', async () => {
    respond({
      plex: {},
      jellyfin: {
        base_url: 'http://jf:8096',
        api_key: '••••••••••••',
        has_key: true,
        inherited: false,
      },
    });
    render(<VideoServerConnection />);
    act(() => {
      videoServer.type('jellyfin-url', 'http://jf:8096');
      videoServer.type('jellyfin-key', '••••••••••••'); // untouched masked secret
    });
    await act(() => videoServer.saveConn(true));

    const conn = calls.filter((c) => c.url === '/api/video/server-config');
    const body = conn[0].init?.body;
    const posted = JSON.parse(typeof body === 'string' ? body : '{}') as Record<string, unknown>;
    expect(conn[0].init?.method).toBe('POST');
    expect(posted.jellyfin).toEqual({ base_url: 'http://jf:8096', api_key: '••••••••••••' });
    expect(posted.plex).toEqual({ base_url: '', token: '' });
    expect(conn[1].init?.method).toBeUndefined(); // the re-read
    expect(noteText('jellyfin')).toBe('Custom video connection.');
  });
});
