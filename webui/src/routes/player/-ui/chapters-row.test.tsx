/**
 * the "Chapters" row on a movie's or an episode's page: asks the server for
 * the version that plays, shows each chapter's picture, name, start and
 * length, plays from the one picked, and isn't there at all for a file
 * without chapters.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const calls: { url: string; params: unknown }[] = [];
let reply: unknown = {};

vi.mock('@/app/api-client', () => ({
  apiClient: {
    get: (url: string, opts?: { searchParams?: unknown }) => {
      calls.push({ url, params: opts?.searchParams });
      return { json: async () => reply };
    },
  },
  readJson: async (p: { json: () => Promise<unknown> }) => p.json(),
  refusalText: async (_e: unknown, fallback: string) => fallback,
}));

const CHAPTERS = [
  { start: 0, end: 300, title: 'Opening', thumb: '/api/player/chapter-thumb/movie/7/0?part=3&v=9' },
  {
    start: 300,
    end: 2400,
    title: 'The job',
    thumb: '/api/player/chapter-thumb/movie/7/1?part=3&v=9',
  },
  { start: 2400, end: null, title: 'Credits', thumb: null },
];

async function mount(onPlayAt = vi.fn()) {
  const { ChaptersRow } = await import('./chapters-row');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <ChaptersRow itemType="movie" itemId="7" part={3} duration={2700} onPlayAt={onPlayAt} />
    </QueryClientProvider>,
  );
  return { ...view, onPlayAt };
}

describe('chapters row', () => {
  beforeEach(() => {
    calls.length = 0;
  });

  it('shows every chapter with its picture and plays from the one picked', async () => {
    reply = { success: true, chapters: CHAPTERS, file_id: 3 };
    const { onPlayAt, container } = await mount();
    await waitFor(() => expect(screen.getByRole('region', { name: 'Chapters' })).toBeTruthy());
    expect(calls[0]).toEqual({ url: 'player/chapters/movie/7', params: { part: '3' } });
    const cards = screen.getAllByRole('button', { name: /^Play from/ });
    expect(cards).toHaveLength(3);
    expect(cards[1].getAttribute('aria-label')).toBe('Play from The job, 5:00');
    expect(cards[1].textContent).toContain('35m');
    // the last chapter runs to the end of the file
    expect(cards[2].textContent).toContain('5m');
    expect(Array.from(container.querySelectorAll('img')).map((i) => i.getAttribute('src'))).toEqual(
      [CHAPTERS[0].thumb, CHAPTERS[1].thumb],
    );
    fireEvent.click(cards[1]);
    expect(onPlayAt).toHaveBeenCalledWith(300);
  });

  it('is not there for a file without chapters', async () => {
    reply = { success: true, chapters: [], file_id: 3 };
    const { container } = await mount();
    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container.textContent).toBe('');
    expect(screen.queryByRole('region', { name: 'Chapters' })).toBeNull();
  });
});
