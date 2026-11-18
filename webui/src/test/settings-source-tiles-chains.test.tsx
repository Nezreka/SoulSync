/**
 * #2026: the Sources tab read only the music chain, so usenet carrying every
 * video download sat under Available as "not in chain". The tiles are React
 * now (SETTINGS_PORT.md step 7a): this runs the real tiles on a sources model.
 */

import { act, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { createSettingsStore } from '@/features/settings/settings-store';
import { createSourcesModel } from '@/features/settings/tabs/sources-model';
import { SourceTiles } from '@/features/settings/tabs/sources-tab';

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

async function tiles(music: string[], video: string[] | null, books: string[]) {
  const model = createSourcesModel({ store: bookStore(books) });
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
  const { container } = render(<SourceTiles model={model} opened={0} active={false} />);
  const groups: Record<string, string[]> = {};
  container.querySelectorAll('.src-group').forEach((g) => {
    const label = g.querySelector('.src-group-label')!.textContent!;
    groups[label] = [...g.querySelectorAll<HTMLElement>('.src-tile')].map((t) => {
      const chips = [...t.querySelectorAll('.src-tile-chip')].map((c) => c.textContent);
      return `${t.dataset.sourceId}:${chips.join('|')}`;
    });
  });
  return groups;
}

it('puts a source used only by the video chain in use, labelled video', async () => {
  const g = await tiles(['deezer_dl', 'soulseek'], ['usenet'], []);
  expect(g['In use']).toEqual(['deezer_dl:Music #1', 'soulseek:Music #2', 'usenet:Video #1']);
  expect(g['Available']).toContain('torrent:not in a chain');
});

it('names every chain a source is in', async () => {
  const g = await tiles(['soulseek'], ['torrent', 'soulseek'], ['torrent']);
  expect(g['In use']).toEqual(['soulseek:Music #1|Video #2', 'torrent:Video #1|Audiobooks #1']);
  expect(g['Available']).toEqual(
    expect.arrayContaining(['deezer_dl:not in a chain', 'usenet:not in a chain']),
  );
});

it('still renders from the music chain before the video chain is read', async () => {
  const g = await tiles(['soulseek'], null, []);
  expect(g['In use']).toEqual(['soulseek:Music #1']);
  expect(g['Indexers']).toEqual(['prowlarr:torrent & usenet']);
});
