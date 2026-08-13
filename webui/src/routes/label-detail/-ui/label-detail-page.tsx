import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { ShellPageId } from '@/platform/shell/route-manifest';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { LabelFilter, LabelRelease, LabelSort } from '../-label-detail.types';

import { setLabelBacklog, setLabelWatched } from '../-label-detail.api';
import {
  emptyStateText,
  filterCounts,
  releaseKey,
  releaseListKey,
  visibleReleases,
} from '../-label-detail.helpers';
import { openLabelRelease } from '../-label-detail.open-release';
import { useLabelCatalog } from '../-label-detail.use-catalog';
import { useLabelCovers } from '../-label-detail.use-covers';
import { LabelHero } from './label-hero';
import { LabelReleaseCard } from './label-release-card';
import { LabelToolbar } from './label-toolbar';

export function LabelDetailPage({ labelId, labelName }: { labelId: string; labelName: string }) {
  useReactPageShell('label-detail');

  const catalog = useLabelCatalog(labelId, labelName);
  const { resolved, observe } = useLabelCovers(labelId);

  const [filter, setFilter] = useState<LabelFilter>('all');
  const [sort, setSort] = useState<LabelSort>('newest');
  const [watchBusy, setWatchBusy] = useState(false);

  // Filter and sort are per-label, exactly as loadLabelDetailData reset them.
  useEffect(() => {
    setFilter('all');
    setSort('newest');
  }, [labelId]);

  const rows = useMemo(
    () => visibleReleases(catalog.releases, catalog.owned, filter, sort),
    [catalog.releases, catalog.owned, filter, sort],
  );
  const counts = useMemo(
    () => filterCounts(catalog.releases, catalog.owned),
    [catalog.releases, catalog.owned],
  );

  const toggleWatch = useCallback(async () => {
    if (watchBusy) return;
    setWatchBusy(true);
    const next = !catalog.watch.watching;
    const ok = await setLabelWatched(labelId, catalog.name, next);
    setWatchBusy(false);
    if (!ok) {
      window.showToast?.('Could not update watchlist', 'error');
      return;
    }
    // Only the watching flag changes here. The backlog value belongs to the
    // server — inventing a local one on unfollow would disagree with whatever
    // /remove actually did to the stored row.
    catalog.setWatch({ watching: next, backlog: catalog.watch.backlog });
    window.updateWatchlistButtonCount?.();
  }, [catalog, labelId, watchBusy]);

  const changeBacklog = useCallback(
    async (backlog: boolean) => {
      if (catalog.watch.backlog === backlog) return;
      // Optimistic, and reverted on refusal — the vanilla's behaviour, because
      // the toggle should feel instant on a call that is usually a formality.
      catalog.setWatch({ ...catalog.watch, backlog });
      const ok = await setLabelBacklog(labelId, backlog);
      if (!ok) catalog.setWatch({ ...catalog.watch, backlog: !backlog });
    },
    [catalog, labelId],
  );

  const openArtist = useCallback((release: LabelRelease) => {
    if (!release.artist_id) return;
    // 'musicbrainz' is not a guess: the catalog IS MusicBrainz, so that is the
    // source the artist id is meaningful on.
    window.SoulSyncWebShellBridge?.navigateToArtistDetail(
      release.artist_id,
      release.artist ?? '',
      'musicbrainz',
    );
  }, []);

  const { loadMore } = catalog;
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || typeof IntersectionObserver === 'undefined') return;
    // 400px: start the next page while the user is still reading this one.
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
      },
      { rootMargin: '400px' },
    );
    observer.observe(element);
    return () => observer.disconnect();
    // loadMore only, NOT the whole catalog object: that is a new object every
    // render, so depending on it rebuilds the observer constantly.
  }, [loadMore]);

  const firstPageIn = !catalog.loading || catalog.releases.length > 0;
  const showEmpty = firstPageIn && !catalog.error && rows.length === 0;

  return (
    // NOT className="page": .page is display:none until the shell adds .active,
    // and the shell only does that for legacy pages it owns. A React page
    // renders inside #webui-react-root and must style itself — copying the
    // vanilla's class list wholesale hid this entire page.
    <div className="label-detail-page">
      <div className="page-shell label-detail-container">
        <button
          className="label-detail-back"
          type="button"
          id="label-detail-back-btn"
          onClick={() => {
            // NOT history.back(): the vanilla's own comment says raw
            // history.back() is unreliable through the SPA router, which is why
            // navigateToLabelDetail records where you came from.
            const returnTo = window._labelDetailReturnTo || 'search';
            void window.SoulSyncWebRouter?.navigateToPage(returnTo as ShellPageId);
          }}
        >
          ← Back
        </button>

        <LabelHero
          name={catalog.name}
          total={catalog.total}
          artistCount={catalog.artistCount}
          watch={catalog.watch}
          ready={firstPageIn && !catalog.error}
          busy={watchBusy}
          onToggleWatch={() => void toggleWatch()}
          onSetBacklog={(backlog) => void changeBacklog(backlog)}
        />

        {firstPageIn && !catalog.error ? (
          <LabelToolbar
            filter={filter}
            sort={sort}
            counts={counts}
            onFilter={setFilter}
            onSort={setSort}
          />
        ) : null}

        {catalog.loading && catalog.releases.length === 0 ? (
          <div className="label-detail-status" id="label-detail-loading">
            Loading label catalog…
          </div>
        ) : null}

        {catalog.error ? (
          <div className="label-detail-status" id="label-detail-loading">
            Could not load this label’s catalog.
          </div>
        ) : null}

        {showEmpty ? (
          <div className="label-detail-status" id="label-detail-empty">
            {emptyStateText(catalog.releases.length, filter)}
          </div>
        ) : null}

        <div className="label-release-grid" id="label-detail-grid">
          {rows.map((release, index) => (
            <LabelReleaseCard
              key={releaseListKey(release, index)}
              release={release}
              owned={catalog.owned}
              checked={catalog.checked}
              resolvedCover={resolved[releaseKey(release)] ?? ''}
              onVisible={observe}
              onOpen={(rel) => void openLabelRelease(rel)}
              onOpenArtist={openArtist}
            />
          ))}
        </div>

        <div className="label-detail-sentinel" id="label-detail-sentinel" ref={sentinelRef} />

        {catalog.loading && catalog.releases.length > 0 ? (
          <div className="label-detail-status" id="label-detail-more">
            Loading more…
          </div>
        ) : null}
      </div>
    </div>
  );
}
