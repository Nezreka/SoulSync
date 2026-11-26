/**
 * the info sheet: asks the server about one item, draws its sections as
 * headed label/value lists, copies a copyable value, says loading and a
 * refusal in plain words, and closes on escape. the right-click menu offers
 * it for anything with an id.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const calls: string[] = [];
let reply: () => Promise<unknown> = async () => ({});

vi.mock('@/app/api-client', () => ({
  apiClient: {
    get: (url: string) => {
      calls.push(url);
      return { json: () => reply() };
    },
  },
  readJson: async (p: { json: () => Promise<unknown> }) => p.json(),
  refusalText: async (e: unknown, fallback: string) =>
    e instanceof Error && e.message ? e.message : fallback,
}));

const MOVIE = {
  success: true,
  item_type: 'movie',
  item_id: '1',
  title: 'Alien',
  subtitle: '1979 · R · 1h 57m',
  artwork: null,
  sections: [
    { id: 'details', title: 'Details', rows: [{ label: 'Runtime', value: '1h 57m' }] },
    {
      id: 'ids',
      title: 'IDs',
      rows: [{ label: 'IMDb', value: 'tt0078748', mono: true, copy: true }],
    },
    {
      id: 'file',
      title: 'File · 4K HDR10',
      group: '4K HDR10',
      rows: [{ label: 'Size', value: '55.9 GB' }],
    },
    {
      id: 'file',
      title: 'File · 1080p',
      group: '1080p',
      rows: [{ label: 'Size', value: '7.5 GB' }],
    },
  ],
};

async function mount(node: React.ReactNode) {
  const { ItemInfoHost } = await import('./item-info');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      {node}
      <ItemInfoHost />
    </QueryClientProvider>,
  );
}

describe('item info sheet', () => {
  beforeEach(() => {
    calls.length = 0;
    reply = async () => MOVIE;
  });
  afterEach(async () => {
    const { closeItemInfo } = await import('./item-info');
    act(() => closeItemInfo());
  });

  it('opens from the i, asks the server and draws headed lists', async () => {
    const { InfoButton } = await import('./item-info');
    await mount(<InfoButton type="movie" id={1} title="Alien" />);
    fireEvent.click(screen.getByRole('button', { name: 'Info about Alien' }));
    expect(await screen.findByText('Details')).toBeTruthy();
    expect(calls).toEqual(['player/info/movie/1']);
    expect(screen.getByRole('dialog', { name: 'Alien' })).toBeTruthy();
    expect(screen.getByText('1979 · R · 1h 57m')).toBeTruthy();
    expect(screen.getByText('Runtime').tagName).toBe('DT');
    expect(screen.getByText('1h 57m').closest('dd')).toBeTruthy();
    // one group per version, each headed by its quality
    expect(screen.getByRole('heading', { name: 'File · 4K HDR10' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'File · 1080p' })).toBeTruthy();
    expect(screen.getByText('tt0078748').closest('dd')?.getAttribute('data-mono')).toBe('true');
  });

  it('copies a copyable value and says so', async () => {
    const write = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: write },
      configurable: true,
    });
    const { openItemInfo } = await import('./item-info');
    await mount(null);
    act(() => openItemInfo({ type: 'movie', id: '1', title: 'Alien' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Copy IMDb' }));
    await waitFor(() => expect(write).toHaveBeenCalledWith('tt0078748'));
    expect(await screen.findByRole('button', { name: 'IMDb copied' })).toBeTruthy();
    // only copyable rows get a button
    expect(screen.queryByRole('button', { name: 'Copy Runtime' })).toBeNull();
  });

  it('says loading, then a refusal in plain words, and closes on escape', async () => {
    let fail: (e: Error) => void = () => {};
    reply = () =>
      new Promise((_, reject) => {
        fail = reject;
      });
    const { openItemInfo } = await import('./item-info');
    await mount(null);
    act(() => openItemInfo({ type: 'track', id: '9', title: 'One More Time' }));
    expect(screen.getByRole('status').textContent).toBe('Reading the details…');
    expect(screen.getByRole('dialog', { name: 'One More Time' })).toBeTruthy();
    await act(async () => fail(new Error("That track isn't in your library.")));
    expect((await screen.findByRole('alert')).textContent).toContain(
      "That track isn't in your library.",
    );
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('encodes an id that is a url', async () => {
    const { openItemInfo } = await import('./item-info');
    await mount(null);
    act(() => openItemInfo({ type: 'podcast_episode', id: '6:https://ep/42' }));
    await screen.findByText('Details');
    expect(calls).toEqual(['player/info/podcast_episode/6%3Ahttps%3A%2F%2Fep%2F42']);
  });
});

describe('the right-click menu offers info', () => {
  it('for an owned item with an id, a podcast episode card and a youtube card', async () => {
    const { infoFor, inferContextTargetFromCard } = await import('./context-menu');
    const at = { clientX: 0, clientY: 0 };
    expect(infoFor({ x: 0, y: 0, kind: 'track', id: '3', title: 'x' })).toEqual({
      type: 'track',
      id: '3',
    });
    expect(infoFor({ x: 0, y: 0, kind: 'track', id: '3', title: 'x', owned: false })).toBeNull();
    expect(infoFor({ x: 0, y: 0, kind: 'show', id: '2', title: 'x' })).toBeNull();
    const ep = inferContextTargetFromCard(at, {
      title: 'Ep',
      href: '/player/podcasts/6',
      source: { itemType: 'podcast_episode', itemId: '6:guid' },
    });
    expect(infoFor(ep)).toEqual({ type: 'podcast_episode', id: '6:guid' });
    const show = inferContextTargetFromCard(at, { title: 'Show', href: '/player/podcasts/6' });
    expect(infoFor(show)).toBeNull();
    const yt = inferContextTargetFromCard(at, {
      key: 'yt:dQw4w9WgXcQ',
      title: 'Never',
      href: '/player/youtube/UC1',
    });
    expect(infoFor(yt)).toEqual({ type: 'youtube', id: 'dQw4w9WgXcQ' });
    const clip = inferContextTargetFromCard(at, {
      title: 'Clip',
      source: { itemType: 'home', itemId: '5' },
    });
    expect(infoFor(clip)).toEqual({ type: 'home', id: '5' });
  });
});
