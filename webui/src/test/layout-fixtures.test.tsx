import { render } from '@testing-library/react';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { DiscoverHero } from '../routes/discover/-ui/discover-hero';
import { DiscoverMixCard } from '../routes/discover/-ui/mix-shelf';

/**
 * Markup for the browser layout probes in tests/layout.
 *
 * Those probes MEASURE rectangles, which jsdom cannot do, and playwright
 * transforms JSX with its own component runtime, so it cannot render a React
 * component either. This renders the real components here and writes the HTML
 * where the probes can load it. playwright.offline.config.ts regenerates it
 * before every run, so it cannot go stale.
 *
 * NOT a .html extension, deliberately. dev.py restarts the backend on any
 * .html change in the tree, and gunicorn's dev config gives in-flight requests
 * one second before it kills them, so writing an .html fixture here dropped
 * every open request on the running app once per probe run.
 */

const OUT = resolve(process.cwd(), 'tests/layout/__fixtures__');

function emit(name: string, html: string) {
  mkdirSync(OUT, { recursive: true });
  writeFileSync(resolve(OUT, name), html, 'utf8');
  expect(html.length).toBeGreaterThan(100);
}

describe('layout fixtures', () => {
  it('writes the discover hero', () => {
    const { container } = render(
      <DiscoverHero
        artist={
          {
            artist_name: 'Jacques Forestier and the Very Long Name Orchestra of Northern Europe',
            image_url: '',
            popularity: 62,
            owned_album_count: 3,
            genres: ['modern classical', 'chamber'],
          } as never
        }
        count={10}
        index={0}
        watchlist={{ watching: false, label: 'Add to Watchlist' } as never}
        watchAllPhase={'idle' as never}
        discographyHref="#"
        onNavigate={() => {}}
        onJump={() => {}}
        onToggleWatchlist={() => {}}
        onWatchAll={() => {}}
        onViewRecommended={() => {}}
        onOpenBlacklist={() => {}}
      />,
    );
    emit('discover-hero.fixture.txt', container.innerHTML);
  });

  it('writes a mix card', () => {
    const { container } = render(
      <div className="discover-mixes-grid">
        <DiscoverMixCard
          mix={
            {
              key: 'mix_a',
              title: 'Fresh Tape',
              tracks: [
                { album: { images: [{ url: '' }] }, name: 'a' },
                { album: { images: [{ url: '' }] }, name: 'b' },
              ],
            } as never
          }
          onOpen={() => {}}
          onPlay={() => {}}
        />
      </div>,
    );
    emit('discover-mix-card.fixture.txt', container.innerHTML);
  });
});
