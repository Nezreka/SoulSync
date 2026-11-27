import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { YoutubeLinkRow } from './-youtube-link';

import {
  downloadYoutubeRows,
  isYoutubeUrl,
  parseYoutubeUrl,
  resolveYoutubeLink,
} from './-youtube-link';

const VID = 'dQw4w9WgXcQ';

describe('parseYoutubeUrl', () => {
  it.each([
    `https://www.youtube.com/watch?v=${VID}`,
    `http://youtube.com/watch?v=${VID}&t=42s`,
    `youtube.com/watch?v=${VID}`,
    `https://m.youtube.com/watch?v=${VID}`,
    `https://music.youtube.com/watch?v=${VID}&si=abc`,
    `https://youtu.be/${VID}?si=xyz`,
    `https://www.youtube.com/shorts/${VID}`,
    `https://www.youtube.com/live/${VID}`,
    `  https://www.youtube.com/watch?v=${VID}  `,
    // a watch link inside a list is the video
    `https://www.youtube.com/watch?v=${VID}&list=RD${VID}`,
  ])('reads %s as the video', (raw) => {
    expect(parseYoutubeUrl(raw)).toEqual({ kind: 'video', id: VID });
  });

  it('reads a /playlist link as the playlist', () => {
    expect(parseYoutubeUrl('https://music.youtube.com/playlist?list=PLabc_123')).toEqual({
      kind: 'playlist',
      id: 'PLabc_123',
    });
  });

  it.each([
    'daft punk around the world',
    '',
    `https://example.com/watch?v=${VID}`,
    `https://youtube.com.evil.test/watch?v=${VID}`,
    `https://user:pw@youtube.com/watch?v=${VID}`,
    `https://youtube.com:8443/watch?v=${VID}`,
    'file:///etc/passwd',
    'https://www.youtube.com/watch?v=short',
    'https://www.youtube.com/@someone',
    'https://youtu.be/',
    'https://www.youtube.com/playlist?list=',
    `https://www.youtube.com/watch?v=${VID} extra`,
  ])('refuses %s', (raw) => {
    expect(isYoutubeUrl(raw)).toBe(false);
  });

  it('refuses things that are not strings', () => {
    expect(parseYoutubeUrl(null)).toBeNull();
    expect(parseYoutubeUrl(42)).toBeNull();
  });
});

describe('resolveYoutubeLink', () => {
  it('posts the link and hands back the server answer', async () => {
    let body: unknown;
    server.use(
      http.post('/api/search/youtube-link', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ kind: 'video', results: [], count: 1 });
      }),
    );
    const out = await resolveYoutubeLink(` https://youtu.be/${VID} `);
    expect(body).toEqual({ url: `https://youtu.be/${VID}` });
    expect(out.kind).toBe('video');
  });

  it("throws with the server's words on a refusal", async () => {
    server.use(
      http.post('/api/search/youtube-link', () =>
        HttpResponse.json(
          { error: "That isn't a YouTube video or playlist link." },
          { status: 400 },
        ),
      ),
    );
    await expect(resolveYoutubeLink('https://example.com')).rejects.toThrow(/isn't a YouTube/);
  });
});

function row(id: string): YoutubeLinkRow {
  return {
    result_type: 'track',
    username: 'youtube',
    filename: `${id}||Artist - Song ${id}`,
    size: 0,
    bitrate: 160,
    duration: 200_000,
    quality: 'opus',
    free_upload_slots: 999,
    upload_speed: 999999,
    queue_length: 0,
    sample_rate: null,
    bit_depth: null,
    artist: 'Artist',
    title: `Song ${id}`,
    album: null,
    track_number: null,
    quality_score: 0.5,
    video_id: id,
  };
}

describe('downloadYoutubeRows', () => {
  afterEach(() => {
    delete (window as { showToast?: unknown }).showToast;
  });

  it('queues each picked row through /api/download and toasts once', async () => {
    const posted: string[] = [];
    server.use(
      http.post('/api/download', async ({ request }) => {
        const body = (await request.json()) as { filename: string; result_type: string };
        expect(body.result_type).toBe('track');
        posted.push(body.filename);
        return HttpResponse.json({ success: true });
      }),
    );
    const toast = vi.fn();
    window.showToast = toast;
    const started = await downloadYoutubeRows([row('aaaaaaaaaaa'), row('ccccccccccc')]);
    expect(started).toBe(2);
    expect(posted).toEqual([
      'aaaaaaaaaaa||Artist - Song aaaaaaaaaaa',
      'ccccccccccc||Artist - Song ccccccccccc',
    ]);
    expect(toast).toHaveBeenCalledTimes(1);
    expect(toast).toHaveBeenCalledWith('Started 2 of 2', 'success');
  });

  it('counts failures and blocklisted rows without stopping', async () => {
    let n = 0;
    server.use(
      http.post('/api/download', () => {
        n += 1;
        if (n === 1)
          return HttpResponse.json({ blocked: true, blocked_name: 'X' }, { status: 409 });
        if (n === 2) return HttpResponse.json({ error: 'boom' }, { status: 500 });
        return HttpResponse.json({ success: true });
      }),
    );
    const toast = vi.fn();
    window.showToast = toast;
    const started = await downloadYoutubeRows([
      row('a1a1a1a1a1a'),
      row('b2b2b2b2b2b'),
      row('c3c3c3c3c3c'),
    ]);
    expect(started).toBe(1);
    expect(toast).toHaveBeenCalledWith('Started 1 of 3, 1 blocklisted, 1 failed', 'success');
  });
});
