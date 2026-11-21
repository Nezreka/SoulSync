/**
 * #2026: the Sources tab read only the music chain, so usenet carrying every
 * video download looked unused. Downloads > Sources (tabs/downloads/
 * sources-view.tsx) says where else each source is used: a source out of the
 * music order names the video and audiobook orders it is in.
 */

import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';
import { SourcesView } from '@/features/settings/tabs/downloads/sources-view';
import { createSourcesModel } from '@/features/settings/tabs/sources-model';

afterEach(() => {
  vi.unstubAllGlobals();
});

/** a settings store holding this audiobook chain (a settings key since step
 *  8c); none at all is a store that never loaded */
function bookStore(books: string[]) {
  const store = createSettingsStore({ delay: 0 });
  if (books.length)
    store.hydrate({
      audiobooks: {
        download_source: { mode: books.length > 1 ? 'hybrid' : books[0], hybrid_order: books },
      },
    });
  return store;
}

/** the "Not used for music" lines, by source */
async function unused(music: string[], video: string[] | null, books: string[]) {
  const store = bookStore(books);
  const model = createSourcesModel({ store });
  model.load({ download_source: { mode: 'hybrid', hybrid_order: music } });
  if (video) {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          json: () => Promise.resolve({ download_mode: 'hybrid', hybrid_order: video }),
        } as Response),
      ),
    );
    await act(() => model.loadVideoChain());
  }
  const { container } = render(
    <SourcesView store={store} model={model} opened={0} active={false} />,
  );
  await act(() => model.loadChain());
  const lines: Record<string, string> = {};
  for (const row of container.querySelectorAll<HTMLElement>('[data-pool] > li[data-id]'))
    lines[row.dataset.id!] = row.textContent!;
  const order = [...container.querySelectorAll<HTMLElement>('ul[aria-label] > li[data-id]')].map(
    (row) => row.dataset.id,
  );
  return { lines, order };
}

it('a source used only by the video order says so', async () => {
  const { lines, order } = await unused(['deezer_dl', 'soulseek'], ['usenet'], []);
  expect(order).toEqual(['deezer_dl', 'soulseek']);
  expect(lines.usenet).toContain('Video #1');
  expect(lines.torrent).toContain('Not tested yet');
});

it('names every order a source is in', async () => {
  const { lines } = await unused(['soulseek'], ['usenet', 'torrent'], ['torrent', 'usenet']);
  expect(lines.torrent).toContain('Video #2, Audiobooks #1');
  expect(lines.usenet).toContain('Video #1, Audiobooks #2');
});

it('still renders from the music order before the video order is read', async () => {
  const { lines, order } = await unused(['soulseek', 'youtube'], null, []);
  expect(order).toEqual(['soulseek', 'youtube']);
  expect(lines.usenet).toContain('Not tested yet');
});
