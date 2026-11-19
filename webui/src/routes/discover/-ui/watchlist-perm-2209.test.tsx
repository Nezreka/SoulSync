/**
 * #2209: a member whose profile has the watchlist part off gets a 403 from
 * every watchlist write, so discover shows them no watch buttons at all.
 * admins, and the moment before the map lands, keep every button.
 */

import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ADULT, clearPerms, stubPerms } from '@/test/perms';

import type { ArtMapNode } from '../-discover.artist-map';
import type { RecommendedArtist } from '../-discover.recommended';
import type { DiscoverHeroArtist } from '../-discover.types';
import type { YourArtist } from '../-discover.your-artists';
import type { ArtistPool } from '../-discover.your-artists-actions';

import { artMap } from '../-discover.artist-map';
import { artMapArtistCard, miniStat } from '../-discover.artist-map.panel';
import { ArtistInfoModal } from './artist-info-modal';
import { ArtMapContextMenuView } from './artist-map-chrome';
import { ArtMapPanel } from './artist-map-panel';
import { ArtWebDiscoveryCard } from './artist-web-panel';
import { DiscoverHero } from './discover-hero';
import { RecommendedModal } from './recommended-modal';
import { RecommendedShelf } from './recommended-shelf';
import { YourArtistCard } from './your-artists-shelf';

vi.mock('./backdrop-video', () => ({
  BackdropVideo: () => <div className="dsc-backdrop-video" />,
}));

afterEach(() => {
  cleanup();
  clearPerms();
});

const heroArtist: DiscoverHeroArtist = {
  artist_id: 'sp1',
  artist_name: 'Aphex Twin',
  image_url: '/img/aphex.jpg',
  genres: ['idm'],
  popularity: 84,
};

const recArtist: RecommendedArtist = {
  artist_id: 'sp1',
  artist_name: 'Aphex Twin',
  image_url: '/img/aphex.jpg',
};

const yourArtist: YourArtist = {
  id: 7,
  artist_name: 'Aphex Twin',
  image_url: '/img/aphex.jpg',
  spotify_artist_id: 'sp1',
  source_services: ['spotify'],
  active_source: 'spotify',
};

const pool: ArtistPool = {
  id: 7,
  artist_name: 'Aphex Twin',
  image_url: '/img/a.jpg',
  active_source: 'spotify',
  active_source_id: 'sp1',
  on_watchlist: 0,
  source_services: ['spotify'],
  spotify_artist_id: 'sp1',
  _related: [],
};

const mapNode = {
  id: 1,
  name: 'Aphex Twin',
  x: 0,
  y: 0,
  radius: 40,
  opacity: 1,
  type: 'similar',
  image_url: '',
  genres: [],
  popularity: 50,
  _hue: 200,
  _island: 'Rock',
} as unknown as ArtMapNode;

/** every discover surface with a watchlist write, rendered once */
function renderAll() {
  artMap.edges = [];
  artMap._watchSet = undefined;
  return render(
    <div>
      <DiscoverHero
        artist={heroArtist}
        count={5}
        index={0}
        watchlist={null}
        watchAllPhase="idle"
        discographyHref="/artist-detail/spotify/sp1"
        onNavigate={vi.fn()}
        onJump={vi.fn()}
        onToggleWatchlist={vi.fn()}
        onWatchAll={vi.fn()}
        onViewRecommended={vi.fn()}
        onOpenBlacklist={vi.fn()}
      />
      <RecommendedShelf
        kind="recommended"
        artists={[recArtist]}
        source="spotify"
        loaded
        watchingIds={new Set()}
        images={{}}
        buildDetailPath={(id) => `/artist-detail/spotify/${id}`}
        onAddToWatchlist={vi.fn()}
      />
      <RecommendedModal
        artists={[recArtist]}
        source="spotify"
        cachedSource={null}
        watchingIds={new Set()}
        images={{}}
        buildDetailPath={(id) => `/artist-detail/spotify/${id}`}
        onClose={vi.fn()}
        onAddToWatchlist={vi.fn()}
        onAddAll={vi.fn()}
      />
      <YourArtistCard
        artist={yourArtist}
        logos={{}}
        buildDetailPath={(id) => `/artist-detail/spotify/${id}`}
        onOpenInfo={vi.fn()}
        onToggleWatchlist={vi.fn()}
      />
      <ArtistInfoModal
        pool={pool}
        info={{}}
        phase="ready"
        logos={{}}
        buildDetailPath={(id) => `/artist/${id}`}
        onClose={vi.fn()}
        onToggleWatchlist={vi.fn()}
        onExplore={vi.fn()}
        onOpenRelated={vi.fn()}
        onViewDiscography={vi.fn()}
      />
      <ArtMapPanel
        model={{
          title: 'Watchlist Map',
          island: null,
          hue: 270,
          scopeTotal: 1,
          scopeWatch: 0,
          coveragePct: 0,
          stats: [miniStat('Artists', 1, 270), miniStat('Watchlist', 0), miniStat('Genres', 1)],
          topArtists: [],
        }}
        card={artMapArtistCard(mapNode)}
        isMobile={false}
        open={false}
        onSelectArtist={vi.fn()}
        onBackToList={vi.fn()}
        onCloseSheet={vi.fn()}
        onToggleWatch={vi.fn()}
        onExplore={vi.fn()}
        onOpenDetails={vi.fn()}
        buildArtistDetailPath={(id, source) => `/artist-detail/${source}/${id}`}
      />
      <ArtMapContextMenuView
        menu={{ hasId: true, bestId: 'sp1', bestSource: 'spotify', watchLabel: 'Add to Watchlist' }}
        node={mapNode}
        clientX={10}
        clientY={10}
        onArtistInfo={vi.fn()}
        onToggleWatchlist={vi.fn()}
        onClose={vi.fn()}
        buildDetailPath={(id, source) => `/artist-detail/${source}/${id}`}
      />
      <ArtWebDiscoveryCard
        card={{
          key: 'disc:99',
          label: 'Boards of Canada',
          imageUrl: '/img/boc.jpg',
          genres: ['idm'],
          detailPath: '/artist-detail/spotify/abc',
          canPreview: true,
        }}
        previewLabel="x"
        onClose={vi.fn()}
        onTogglePreview={vi.fn()}
        onAddToWatchlist={vi.fn()}
      />
    </div>,
  );
}

/** each watch control, by the selector that finds it */
const CONTROLS: [string, (root: HTMLElement) => Element | null][] = [
  ['hero watch toggle', (r) => r.querySelector('#discover-hero-add')],
  ['hero watch all', (r) => r.querySelector('#discover-hero-watch-all')],
  ['recommended shelf eye', (r) => r.querySelector('.ya-card-reco-btn')],
  [
    'recommended modal eye',
    (r) =>
      r.querySelector(
        '.recommended-artists-grid .recommended-card-watchlist-btn:not(.ya-card-reco-btn)',
      ),
  ],
  ['recommended modal add all', (r) => r.querySelector('#recommended-add-all-btn')],
  [
    'your artists eye',
    (r) => r.querySelector('.ya-card .ya-watchlist-btn:not(.recommended-card-watchlist-btn)'),
  ],
  [
    'info modal watch button',
    (r) =>
      [...r.querySelectorAll('#ya-info-footer button')].find((b) =>
        /watchlist/i.test(b.textContent ?? ''),
      ) ?? null,
  ],
  ['map card watch', (r) => r.querySelector('#artmap-card-watch')],
  [
    'map menu watch item',
    (r) =>
      [...r.querySelectorAll('#artist-map-context button.artmap-ctx-item')].find((b) =>
        /watchlist/i.test(b.textContent ?? ''),
      ) ?? null,
  ],
  ['web add to watchlist', (r) => r.querySelector('#artweb-add-btn')],
];

describe('discover watch controls (#2209)', () => {
  it('a member with the watchlist part off sees none of them', () => {
    stubPerms({ ...ADULT, 'music.watchlist': false });
    const { container } = renderAll();
    for (const [name, find] of CONTROLS) {
      expect(find(container), name).toBeNull();
    }
    // the rest of each surface still renders
    expect(container.querySelector('#discover-hero-discography')).not.toBeNull();
    expect(container.querySelector('#artweb-preview-btn')).not.toBeNull();
  });

  it('a member with the part on keeps every one', () => {
    stubPerms({ ...ADULT, 'music.watchlist': true });
    const { container } = renderAll();
    for (const [name, find] of CONTROLS) {
      expect(find(container), name).not.toBeNull();
    }
  });

  it('an admin keeps every one even if the map says off', () => {
    stubPerms({ ...ADULT, 'music.watchlist': false }, { isAdmin: true });
    const { container } = renderAll();
    for (const [name, find] of CONTROLS) {
      expect(find(container), name).not.toBeNull();
    }
  });

  it('no map yet (no shell) keeps every one', () => {
    const { container } = renderAll();
    for (const [name, find] of CONTROLS) {
      expect(find(container), name).not.toBeNull();
    }
  });
});
