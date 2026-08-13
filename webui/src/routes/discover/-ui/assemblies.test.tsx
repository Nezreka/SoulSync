import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import type { ArtistMapController } from '../-discover.use-artist-map';

import { ArtistMapAssembly } from './artist-map-assembly';
import { ArtistWebHub } from './artist-map-hub';
import { ArtistWebAssembly } from './artist-web-assembly';
import { ArtMapExplorePrompt, EXPLORE_PROMPT_DEBOUNCE_MS } from './artmap-explore-prompt';

/**
 * The pieces born during the live smoke-test rounds. The heavy halves live in
 * the orchestrator hooks (their own suites); what THESE tests pin is the
 * assembly contracts a page depends on: closed means null, the hub's three
 * lens cards route their lens, and the explorer prompt only ever hands over a
 * PICKED artist — never raw text.
 */

const noop = () => {};

describe('the assemblies render NOTHING while closed', () => {
  it('ArtistMapAssembly with kind null mounts no overlay', () => {
    const map = {
      kind: null,
      title: '',
      stats: '',
      loading: null,
      sidebarGenres: [],
      selectedGenre: '',
      focusVersion: 0,
      openWatchlist: async () => {},
      openGenre: async () => {},
      openExplorer: async () => {},
      switchGenre: async () => {},
      close: noop,
      islandNav: noop,
      focusIsland: noop,
      zoom: noop,
      fitToView: noop,
      poolFor: () => ({}) as never,
      makeHost: () => ({}) as never,
    } as unknown as ArtistMapController;
    const { container } = render(
      <ArtistMapAssembly map={map} onOpenInfo={noop} buildDetailPath={() => '#'} onToast={noop} />,
    );
    // Closed = the page's sections own the screen; a lingering overlay div
    // would sit invisibly over them and eat the first click.
    expect(container.querySelector('#artist-map-container')).toBeNull();
  });

  it('ArtistWebAssembly with request null mounts no overlay', () => {
    const { container } = render(
      <ArtistWebAssembly
        request={null}
        onClose={noop}
        onExploreInMap={noop}
        buildDetailPath={() => '#'}
        onToast={noop}
      />,
    );
    expect(container.querySelector('#artist-web-container')).toBeNull();
  });
});

describe('ArtistWebHub', () => {
  it('routes each of the three cards to its lens', () => {
    const opened: string[] = [];
    render(<ArtistWebHub onOpenLens={(lens) => opened.push(lens)} />);
    for (const label of ['Taste Map', 'Communities', 'Discovery Web']) {
      screen.getByText(label).closest('button')!.click();
    }
    expect(opened).toEqual(['genre', 'community', 'discovery']);
  });
});

describe('ArtMapExplorePrompt', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/discover/build-playlist/search-artists', ({ request }) => {
        const q = new URL(request.url).searchParams.get('query');
        return HttpResponse.json({
          success: true,
          artists:
            q === 'nothing'
              ? []
              : [
                  { name: 'Resolved Artist', image_url: '' },
                  { name: 'Second Artist', image_url: '' },
                ],
        });
      }),
    );
  });
  afterEach(() => {
    server.resetHandlers();
  });

  function type(value: string) {
    // fireEvent, not a hand-rolled native event: React's value tracker
    // swallows an input event whose .value it already saw.
    const input = document.getElementById('artmap-explore-input') as HTMLInputElement;
    fireEvent.input(input, { target: { value } });
  }

  it('searches after the debounce and picking a result hands over the RESOLVED name', async () => {
    const picked: string[] = [];
    render(<ArtMapExplorePrompt onPick={(name) => picked.push(name)} onClose={noop} />);
    type('resolved');
    // Real timers: the 350ms debounce (EXPLORE_PROMPT_DEBOUNCE_MS) elapses
    // inside the waitFor. The boundary itself is pinned by the constant test
    // below rather than a racy just-under assertion.
    await waitFor(() => expect(screen.getByText('Resolved Artist')).toBeTruthy(), {
      timeout: 3000,
    });
    screen.getByText('Resolved Artist').closest('button')!.click();
    expect(picked).toEqual(['Resolved Artist']);
  });

  it('debounces at the vanilla interval (9817)', () => {
    expect(EXPLORE_PROMPT_DEBOUNCE_MS).toBe(350);
  });

  it('Enter picks the TOP match, never the raw text (9838)', async () => {
    const picked: string[] = [];
    render(<ArtMapExplorePrompt onPick={(name) => picked.push(name)} onClose={noop} />);
    type('resol');
    await waitFor(() => expect(screen.getByText('Resolved Artist')).toBeTruthy(), {
      timeout: 3000,
    });
    const input = document.getElementById('artmap-explore-input') as HTMLInputElement;
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(picked).toEqual(['Resolved Artist']); // not 'resol'
  });

  it('empty answers say so, and Cancel closes without picking', async () => {
    const picked: string[] = [];
    let closed = 0;
    render(
      <ArtMapExplorePrompt
        onPick={(name) => picked.push(name)}
        onClose={() => {
          closed += 1;
        }}
      />,
    );
    type('nothing');
    await waitFor(() => expect(screen.getByText('No artists found')).toBeTruthy(), {
      timeout: 3000,
    });
    screen.getByText('Cancel').click();
    expect(closed).toBe(1);
    expect(picked).toEqual([]);
  });
});
