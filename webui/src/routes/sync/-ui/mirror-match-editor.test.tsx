/**
 * #1958: the editor for a mirror that isn't on the server yet. The right side
 * is the library track each song will sync as; a pick goes to the pre-sync
 * match endpoint, never to a server playlist.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MirrorMatchEditor, presyncFooterText } from './mirror-match-editor';

const MIRROR = { id: 62, name: 'Without You Mix', source: 'spotify' };

const TRACKS = [
  {
    mirror_track_id: 5115,
    match_status: 'matched',
    source_track: {
      position: 1,
      name: 'Without You',
      artist: 'Cosmic Quest',
      source_track_id: 'sp1',
    },
    server_track: {
      id: '572122',
      title: 'Without You (Radio Edit)',
      artist: 'Cosmic Quest',
      duration: 184000,
    },
  },
  {
    mirror_track_id: 5116,
    match_status: 'missing',
    source_track: { position: 2, name: 'Rare Cut', artist: 'Nobody', source_track_id: 'sp2' },
    server_track: null,
  },
];

let calls: { url: string; method: string; body: unknown }[] = [];
let unchecked = 0;
let toasts: string[] = [];

beforeEach(() => {
  calls = [];
  toasts = [];
  unchecked = 0;
  window.showToast = (m: string) => {
    toasts.push(m);
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      calls.push({
        url,
        method,
        body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : null,
      });
      if (url.endsWith('/check-library')) {
        unchecked = 0;
        return new Response(JSON.stringify({ success: true, remaining: 0 }));
      }
      if (url.endsWith('/presync')) {
        return new Response(
          JSON.stringify({ success: true, source_track_count: 2, unchecked, tracks: TRACKS }),
        );
      }
      if (url.startsWith('/api/library/search-tracks')) {
        return new Response(
          JSON.stringify({
            success: true,
            tracks: [
              { id: 'srv-ext', title: 'Without You (Extended Mix)', artist_name: 'Cosmic Quest' },
            ],
          }),
        );
      }
      return new Response(JSON.stringify({ success: true, message: 'Match saved' }));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('MirrorMatchEditor', () => {
  it('lists each song next to the library track it will sync as', async () => {
    render(<MirrorMatchEditor mirrored={MIRROR} onBack={() => {}} />);
    await screen.findByText('Without You (Radio Edit)');
    expect(screen.getByText('Not in your library, the sync downloads it')).toBeTruthy();
    expect(screen.getByText(/Not on your server yet/)).toBeTruthy();
    // nothing to remove from a server playlist that doesn't exist
    expect(document.querySelector('.server-track-remove-btn')).toBeNull();
  });

  it('checks songs the background check has not reached before showing them', async () => {
    unchecked = 3;
    render(<MirrorMatchEditor mirrored={MIRROR} onBack={() => {}} />);
    await screen.findByText('Without You (Radio Edit)');
    expect(calls.some((c) => c.url === '/api/mirrored-playlists/62/check-library')).toBe(true);
  });

  it('a swap is saved as the song match, not a server edit', async () => {
    render(<MirrorMatchEditor mirrored={MIRROR} onBack={() => {}} />);
    await screen.findByText('Without You (Radio Edit)');
    fireEvent.click(document.querySelector('.server-track-swap-btn') as Element);
    expect(await screen.findByText('Pick a version from your library')).toBeTruthy();
    fireEvent.click(await screen.findByText('Without You (Extended Mix)'));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/presync/match'))).toBe(true));
    const write = calls.find((c) => c.url.endsWith('/presync/match'));
    expect(write?.body).toEqual({
      mirror_track_id: 5115,
      track_id: 'srv-ext',
      track_title: 'Without You (Extended Mix)',
    });
    expect(calls.some((c) => c.url.startsWith('/api/server/playlist'))).toBe(false);
  });

  it('not this one sends the song to download', async () => {
    render(<MirrorMatchEditor mirrored={MIRROR} onBack={() => {}} />);
    await screen.findByText('Without You (Radio Edit)');
    fireEvent.click(document.querySelector('.server-track-unmatch-btn') as Element);
    await waitFor(() => expect(screen.queryByText('Without You (Radio Edit)')).toBeNull());
    expect(calls.find((c) => c.url.endsWith('/presync/unmatch'))?.body).toEqual({
      mirror_track_id: 5115,
      track_id: '572122',
    });
  });

  it('the footer counts what is owned and what will download', () => {
    expect(presyncFooterText({ matched: 1, missing: 1 })).toBe(
      '1/2 already in your library · 1 to download on sync',
    );
  });
});
