import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { ArtistVideosSection } from './artist-videos-section';

function ndjson(lines: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
        else controller.close();
      },
    }),
    { status: 200 },
  );
}

beforeEach(() => {
  window.open = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ArtistVideosSection', () => {
  it('streams YouTube videos for the artist and presents a featured card', async () => {
    let body: unknown;
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', async ({ request }) => {
        body = await request.json();
        return ndjson([
          '{"type":"videos","data":[{"video_id":"v1","title":"Aphex Twin - Windowlicker (Official Video)","channel":"Aphex Twin","duration":215,"view_count":1500000,"thumbnail":"/v1.jpg","url":"https://youtube.com/watch?v=v1"}]}\n',
        ]);
      }),
    );

    render(<ArtistVideosSection artistName="Aphex Twin" />);

    await screen.findByText('Aphex Twin - Windowlicker (Official Video)');
    expect(body).toEqual({ query: 'Aphex Twin' });
    expect(document.querySelector('.artist-video-spotlight.featured')).not.toBeNull();
    expect(screen.getAllByText('3:35')).toHaveLength(2);
    expect(document.getElementById('artist-videos-count')?.textContent).toBe('1 video');
  });

  it('hides itself when YouTube returns no videos', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        ndjson(['{"type":"videos","data":[]}\n']),
      ),
    );

    render(<ArtistVideosSection artistName="Aphex Twin" />);

    await waitFor(() => expect(document.getElementById('artist-videos-section')).toBeNull());
  });

  it('opens the selected video on YouTube', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        ndjson([
          '{"type":"videos","data":[{"video_id":"v1","title":"Aphex Twin - Clip","channel":"Warp","url":"https://youtube.com/watch?v=v1"}]}\n',
        ]),
      ),
    );

    render(<ArtistVideosSection artistName="Aphex Twin" />);
    await screen.findByText('Aphex Twin - Clip');

    fireEvent.click(screen.getAllByRole('button', { name: /Watch Aphex Twin - Clip/ })[0]);
    expect(window.open).toHaveBeenCalledWith(
      'https://youtube.com/watch?v=v1',
      '_blank',
      'noopener,noreferrer',
    );
  });

  it('hides itself when the video search endpoint fails', async () => {
    server.use(
      http.post('/api/enhanced-search/source/youtube_videos', () =>
        HttpResponse.json({}, { status: 500 }),
      ),
    );

    render(<ArtistVideosSection artistName="Aphex Twin" />);

    await waitFor(() => expect(document.getElementById('artist-videos-section')).toBeNull());
  });
});
