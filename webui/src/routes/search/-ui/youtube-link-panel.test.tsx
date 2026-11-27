import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import type { DownloadTarget } from '../-basic.types';
import type { YoutubeLinkResponse, YoutubeLinkRow, YoutubeLinkState } from '../-youtube-link';

import { YoutubeLinkPanel } from './youtube-link-panel';

afterEach(cleanup);

function row(id: string, title: string): YoutubeLinkRow {
  return {
    result_type: 'track',
    username: 'youtube',
    filename: `${id}||Artist - ${title}`,
    size: 0,
    bitrate: 160,
    duration: 185_000,
    quality: 'opus',
    free_upload_slots: 999,
    upload_speed: 999999,
    queue_length: 0,
    sample_rate: null,
    bit_depth: null,
    artist: 'Artist',
    title,
    album: null,
    track_number: null,
    quality_score: 0.5,
    thumbnail: `https://i.ytimg.com/vi/${id}/hq.jpg`,
    video_id: id,
    channel: 'ArtistVEVO',
  };
}

function ready(data: Partial<YoutubeLinkResponse>): YoutubeLinkState {
  return {
    status: 'ready',
    url: 'x',
    error: '',
    data: {
      kind: 'video',
      id: 'x',
      url: 'x',
      title: 'Artist - Song',
      channel: 'ArtistVEVO',
      thumbnail: null,
      results: [],
      count: 0,
      truncated: false,
      ...data,
    },
  };
}

describe('YoutubeLinkPanel', () => {
  it('shows the one video and opens the chooser for it', () => {
    const video = row('aaaaaaaaaaa', 'Song');
    const onDownload = vi.fn<(target: DownloadTarget) => void>();
    render(
      <YoutubeLinkPanel state={ready({ results: [video], count: 1 })} onDownload={onDownload} />,
    );
    expect(screen.getByText(/YouTube video/)).toBeTruthy();
    expect(screen.getByText('3:05')).toBeTruthy();
    expect(screen.queryByText('Download all')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Download Song' }));
    expect(onDownload).toHaveBeenCalledWith({ kind: 'track', track: video });
  });

  it('downloads only the picked videos of a playlist', async () => {
    const posted: string[] = [];
    server.use(
      http.post('/api/download', async ({ request }) => {
        posted.push(((await request.json()) as { filename: string }).filename);
        return HttpResponse.json({ success: true });
      }),
    );
    const rows = [
      row('aaaaaaaaaaa', 'One'),
      row('bbbbbbbbbbb', 'Two'),
      row('ccccccccccc', 'Three'),
    ];
    render(
      <YoutubeLinkPanel
        state={ready({ kind: 'playlist', title: 'Mix', results: rows, count: 3 })}
        onDownload={vi.fn()}
      />,
    );
    const pickedButton = screen.getByRole('button', { name: /picked/ }) as HTMLButtonElement;
    expect(pickedButton.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText('Pick One'));
    fireEvent.click(screen.getByLabelText('Pick Three'));
    fireEvent.click(screen.getByRole('button', { name: /Download 2 picked/ }));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted).toEqual(['aaaaaaaaaaa||Artist - One', 'ccccccccccc||Artist - Three']);
  });

  it('downloads the whole playlist and says when it was capped', async () => {
    const posted: string[] = [];
    server.use(
      http.post('/api/download', async ({ request }) => {
        posted.push(((await request.json()) as { filename: string }).filename);
        return HttpResponse.json({ success: true });
      }),
    );
    const rows = [row('aaaaaaaaaaa', 'One'), row('bbbbbbbbbbb', 'Two')];
    render(
      <YoutubeLinkPanel
        state={ready({ kind: 'playlist', results: rows, count: 2, truncated: true })}
        onDownload={vi.fn()}
      />,
    );
    expect(screen.getByText(/the playlist has more/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Download all' }));
    await waitFor(() => expect(posted).toHaveLength(2));
  });

  it("shows the server's refusal", () => {
    render(
      <YoutubeLinkPanel
        state={{ status: 'error', url: 'x', data: null, error: 'Not a YouTube link.' }}
        onDownload={vi.fn()}
      />,
    );
    expect(screen.getByRole('status').textContent).toBe('Not a YouTube link.');
  });
});
