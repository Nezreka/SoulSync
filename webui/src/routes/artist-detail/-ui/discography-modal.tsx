import { useEffect, useState } from 'react';

import { profileAsksFirst } from '@/platform/shell/download-rights';

import type {
  DiscogAlbumUpdate,
  DiscogEntry,
  DiscogFilters,
  DiscogModalData,
  DiscogRelease,
  DiscogTotals,
  FailedRelease,
  FutureReleases,
} from '../-artist-detail.discography-modal';
import type { Discography } from '../-artist-detail.types';

import {
  buildDiscographyPayload,
  defaultFutureReleases,
  DISCOG_DEFAULT_FILTERS,
  discogCardView,
  discogCardVisible,
  discogFooter,
  discogItemStatus,
  loadDiscographyForModal,
  streamDiscographyDownload,
  watchArtistWithSettings,
} from '../-artist-detail.discography-modal';
import { releaseSectionType } from '../-artist-detail.open-release';
import { BodyPortal } from './portal';

/**
 * The Download Discography modal (openDiscographyModal, library.js:580):
 * type + content filters (#877), completion-aware cards with unowned releases
 * pre-checked, and the per-album NDJSON progress view with honest statuses
 * (#830). Deluxe-first ordering and per-entry gap-fill sources (#1067) live in
 * buildDiscographyPayload.
 */

/** The boolean keys of the Future releases settings (everything except the
 * tri-state auto_download_pref). */
type FutureBoolKey = {
  [K in keyof FutureReleases]: FutureReleases[K] extends boolean ? K : never;
}[keyof FutureReleases];

/** One compact pill-toggle row for the Future releases section — the same
 * toggle pills the watchlist page uses, instead of a tall checkbox stack. */
function FuturePillRow({
  label,
  options,
  future,
  onToggle,
}: {
  label: string;
  options: ReadonlyArray<readonly [FutureBoolKey, string]>;
  future: FutureReleases;
  onToggle: (key: FutureBoolKey) => void;
}): React.ReactNode {
  return (
    <div className="discog-future-row">
      <span className="discog-future-label">{label}</span>
      <div className="discog-future-pills">
        {options.map(([key, pillLabel]) => (
          <button
            type="button"
            key={key}
            className={`watchlist-filter-btn${future[key] ? ' active' : ''}`}
            aria-pressed={future[key]}
            onClick={() => onToggle(key)}
          >
            {pillLabel}
          </button>
        ))}
      </div>
    </div>
  );
}

type ProgressState = Record<
  string,
  { status: 'waiting' | 'active' | 'done' | 'skipped' | 'error'; text: string }
>;

export function DiscographyModal({
  libraryArtistId,
  artistName,
  artistImage,
  discography,
  onClose,
  watchlistIdentity = null,
}: {
  libraryArtistId: unknown;
  artistName: string;
  artistImage: string;
  /** what the page is showing: the modal lists exactly these */
  discography: Discography;
  onClose: () => void;
  /** canonical identity for the watchlist add; null hides the combined button */
  watchlistIdentity?: { id: unknown; name: string } | null;
}) {
  const [data, setData] = useState<DiscogModalData | null>(null);
  const [filters, setFilters] = useState<DiscogFilters>(DISCOG_DEFAULT_FILTERS);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<'pick' | 'progress'>('pick');
  const [progress, setProgress] = useState<ProgressState>({});
  const [totals, setTotals] = useState<DiscogTotals | null>(null);
  /** one-time init from the download filters; independent after that */
  const [future, setFuture] = useState<FutureReleases>(() =>
    defaultFutureReleases(DISCOG_DEFAULT_FILTERS),
  );
  const [watchNote, setWatchNote] = useState<string | null>(null);
  /** cards shown in the progress phase; a retry swaps in just the failures */
  const [activeCards, setActiveCards] = useState<
    { release: DiscogRelease; view: ReturnType<typeof discogCardView> }[]
  >([]);

  useEffect(() => {
    let cancelled = false;
    window.showToast?.('Loading discography...', 'info');
    void loadDiscographyForModal(libraryArtistId, artistName, discography).then((result) => {
      if (cancelled) return;
      if (!result) {
        window.showToast?.(
          'No discography found. Try searching this artist from the Search page instead.',
          'error',
        );
        onClose();
        return;
      }
      setData(result);
      // Unowned releases come pre-checked. The library path has no completion
      // cache (that belonged to the OLD search page), so everything starts on.
      const next = new Set<string>();
      for (const release of result.releases) {
        if (discogCardView(release, {}).checkedByDefault) next.add(String(release.id));
      }
      setChecked(next);
    });
    return () => {
      cancelled = true;
    };
    // One load per mounted modal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!data) return null;

  const cards = data.releases.map((release) => ({
    release,
    view: discogCardView(release, {}),
    visible: discogCardVisible(discogCardView(release, {}), release._type, filters),
  }));
  const visibleChecked = cards.filter((c) => c.visible && checked.has(String(c.release.id)));
  // a profile that can't download sends these as requests
  const asksFirst = profileAsksFirst();
  const footer = discogFooter(
    visibleChecked.map((c) => ({ tracks: c.view.tracks })),
    asksFirst,
  );

  const toggleFilter = (key: keyof DiscogFilters) =>
    setFilters((prev) => ({ ...prev, [key]: !prev[key] }));

  const selectAll = (select: boolean) => {
    const next = new Set(checked);
    for (const card of cards) {
      if (!card.visible) continue;
      if (select) next.add(String(card.release.id));
      else next.delete(String(card.release.id));
    }
    setChecked(next);
  };

  const runStream = async (
    entries: DiscogEntry[],
    progressCards: { release: DiscogRelease; view: ReturnType<typeof discogCardView> }[],
    onDone: (finished: DiscogTotals) => void,
  ) => {
    // The download payload is built from VISIBLE checked cards (#877).
    setActiveCards(progressCards);
    setPhase('progress');
    const initial: ProgressState = {};
    for (const c of progressCards) {
      initial[String(c.release.id)] = { status: 'active', text: 'Waiting...' };
    }
    setProgress(initial);
    setTotals(null);
    setWatchNote(null);

    try {
      await streamDiscographyDownload(
        data.artist.id,
        buildDiscographyPayload(entries, data.artist),
        (update: DiscogAlbumUpdate) => {
          const id = String(update.album_id);
          setProgress((prev) => ({
            ...prev,
            [id]:
              update.status === 'done'
                ? {
                    status: (update.tracks_added || 0) > 0 ? 'done' : 'skipped',
                    text: discogItemStatus(update),
                  }
                : update.status === 'error'
                  ? { status: 'error', text: update.message || 'Error' }
                  : {
                      status: 'active',
                      text: `Processing ${update.tracks_total ?? '?'} tracks...`,
                    },
          }));
        },
        (finished) => {
          onDone(finished);
          // a requester hears who it went to
          if (asksFirst && finished.total_added > 0) window.announceWishlistRequest?.();
        },
      );
    } catch (error) {
      window.showToast?.(`Discography download failed: ${(error as Error).message}`, 'error');
    }
  };

  const start = async (withWatch: boolean) => {
    if (visibleChecked.length === 0 || !data) return;
    // request limit used up: core.js says so instead of adds the server drops
    if (asksFirst && window.checkMusicRequestQuota && !(await window.checkMusicRequestQuota())) {
      return;
    }
    const entries = visibleChecked.map((c) => ({
      id: c.release.id,
      name: c.view.albumName,
      tracks: c.view.tracks,
      gapSource: c.release._gap_source || null,
      albumType: releaseSectionType(c.release),
    }));
    // The watchlist half runs AFTER the stream so a watch failure can never
    // roll back queued downloads; each half reports its own outcome.
    const settings = withWatch && watchlistIdentity ? future : null;
    await runStream(entries, visibleChecked, (finished) => {
      setTotals(finished);
      if (!settings || !watchlistIdentity) return;
      void watchArtistWithSettings(watchlistIdentity.id, watchlistIdentity.name, settings)
        .then(({ message }) => {
          setWatchNote(`👁 Watching ${watchlistIdentity.name}${message ? ` — ${message}` : ''}`);
          if (typeof window.updateWatchlistCount === 'function') window.updateWatchlistCount();
        })
        .catch((error: Error) => {
          setWatchNote(`👁 Watchlist add failed: ${error.message}`);
        });
    });
  };

  /** Re-run just the releases that failed resolution — they never reached the
   * wishlist (no tracks to add), so without this they would vanish silently. */
  const retryFailed = async () => {
    const failed = totals?.failed_releases ?? [];
    if (!data || failed.length === 0) return;
    const entries: DiscogEntry[] = failed.map((f) => {
      const t = (f.album_type || '').toLowerCase();
      const albumType =
        t === 'ep' || t === 'single' || t === 'compilation' || t === 'album' ? t : undefined;
      return {
        id: f.album_id,
        name: f.name,
        tracks: 0,
        gapSource: f.source,
        ...(albumType ? { albumType } : {}),
      };
    });
    const cardsForRetry = failed.map((f) => {
      const release = {
        id: f.album_id,
        name: f.name,
        _type: 'album',
        _gap_source: f.source ?? undefined,
      } as DiscogRelease;
      return { release, view: discogCardView(release, {}) };
    });
    await runStream(entries, cardsForRetry, (finished) => {
      setTotals(finished);
    });
  };

  // BodyPortal is load-bearing: this mounts from inside the hero, whose
  // backdrop-filter makes it the containing block for position:fixed —
  // rendered in place, the overlay is clamped to the hero box and cut off.
  return (
    <BodyPortal>
      <div className="discog-modal-overlay visible" id="discog-modal-overlay">
        <div className="discog-modal">
          <div
            className="discog-modal-hero"
            style={artistImage ? { backgroundImage: `url('${artistImage}')` } : undefined}
          >
            <div className="discog-modal-hero-overlay" />
            <div className="discog-modal-hero-content">
              <h2 className="discog-modal-title">
                {asksFirst ? 'Request Discography' : 'Download Discography'}
              </h2>
              <p className="discog-modal-artist">{artistName}</p>
            </div>
            <button className="discog-modal-close" type="button" onClick={onClose}>
              ×
            </button>
          </div>

          {phase === 'pick' ? (
            <div className="discog-filter-bar">
              <div className="discog-filters">
                {(
                  [
                    ['album', 'Albums'],
                    ['ep', 'EPs'],
                    ['single', 'Singles'],
                    ['live', 'Live'],
                    ['compilations', 'Compilations'],
                    ['featured', 'Featured'],
                  ] as [keyof DiscogFilters, string][]
                ).map(([key, label]) => (
                  <button
                    className={`discog-filter${filters[key] ? ' active' : ''}`}
                    type="button"
                    key={key}
                    onClick={() => toggleFilter(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="discog-select-actions">
                <button className="discog-select-btn" type="button" onClick={() => selectAll(true)}>
                  Select All
                </button>
                <button
                  className="discog-select-btn"
                  type="button"
                  onClick={() => selectAll(false)}
                >
                  Deselect All
                </button>
              </div>
            </div>
          ) : null}

          {phase === 'pick' ? (
            <div className="discog-grid" id="discog-grid">
              {cards.map((card, index) => (
                <DiscogCard
                  key={`${card.release._type}-${String(card.release.id)}-${index}`}
                  release={card.release}
                  view={card.view}
                  visible={card.visible}
                  index={index}
                  checked={checked.has(String(card.release.id))}
                  onToggle={() => {
                    const id = String(card.release.id);
                    const next = new Set(checked);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    setChecked(next);
                  }}
                />
              ))}
            </div>
          ) : (
            <div className="discog-progress" id="discog-progress">
              {activeCards.map((card) => {
                const state = progress[String(card.release.id)];
                return (
                  <div
                    className={`discog-progress-item${state ? ` ${state.status}` : ''}`}
                    id={`discog-prog-${String(card.release.id)}`}
                    key={String(card.release.id)}
                  >
                    <div className="discog-prog-art">
                      {card.release.image_url ? <img src={card.release.image_url} alt="" /> : '🎵'}
                    </div>
                    <div className="discog-prog-info">
                      <div className="discog-prog-title">{card.view.albumName}</div>
                      <div className="discog-prog-status">{state?.text ?? 'Waiting...'}</div>
                    </div>
                    <div className="discog-prog-icon">
                      {state?.status === 'done' ? (
                        <span className="discog-check">✓</span>
                      ) : state?.status === 'skipped' ? (
                        <span className="discog-skip">—</span>
                      ) : state?.status === 'error' ? (
                        <span className="discog-error">✗</span>
                      ) : (
                        <div className="discog-spinner" />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {phase === 'pick' && watchlistIdentity ? (
            <div className="discog-future" id="discog-future">
              <div className="discog-future-head">
                <span className="discog-future-icon">👁</span>
                <div>
                  <div className="discog-future-title">Future releases</div>
                  <div className="discog-future-sub">
                    What the watchlist grabs later — applies to Wishlist + Watchlist
                  </div>
                </div>
              </div>
              <div className="discog-future-rows">
                <FuturePillRow
                  label="Release types"
                  options={[
                    ['include_albums', 'Albums'],
                    ['include_eps', 'EPs'],
                    ['include_singles', 'Singles'],
                  ]}
                  future={future}
                  onToggle={(key) => setFuture((prev) => ({ ...prev, [key]: !prev[key] }))}
                />
                <FuturePillRow
                  label="Include"
                  options={[
                    ['include_live', 'Live'],
                    ['include_remixes', 'Remixes'],
                    ['include_acoustic', 'Acoustic'],
                    ['include_compilations', 'Compilations'],
                    ['include_instrumentals', 'Instrumentals'],
                  ]}
                  future={future}
                  onToggle={(key) => setFuture((prev) => ({ ...prev, [key]: !prev[key] }))}
                />
                <div className="discog-future-row">
                  <span className="discog-future-label">New releases</span>
                  <select
                    className="discog-future-select"
                    aria-label="New releases from this artist"
                    value={future.auto_download_pref ?? ''}
                    onChange={(event) =>
                      setFuture((prev) => ({
                        ...prev,
                        auto_download_pref:
                          event.target.value === '' ? null : (event.target.value as 'on' | 'off'),
                      }))
                    }
                  >
                    <option value="">Follow global setting</option>
                    <option value="on">Download automatically</option>
                    <option value="off">Follow only — I&apos;ll pick</option>
                  </select>
                </div>
              </div>
            </div>
          ) : null}

          <div className="discog-footer" id="discog-footer">
            <div className="discog-footer-info" id="discog-footer-info">
              {phase === 'pick' ? (
                footer.info
              ) : totals ? (
                <>
                  <div>
                    Done — {totals.total_added} tracks added, {totals.total_skipped} skipped
                  </div>
                  {watchNote ? <div className="discog-watch-note">{watchNote}</div> : null}
                  {totals.failed_releases.length > 0 ? (
                    <div className="discog-failed">
                      <span>
                        {totals.failed_releases.length} release
                        {totals.failed_releases.length !== 1 ? 's' : ''} couldn&apos;t be resolved (
                        {totals.failed_releases
                          .slice(0, 3)
                          .map((f) => f.name)
                          .join(', ')}
                        {totals.failed_releases.length > 3 ? ', …' : ''})
                      </span>{' '}
                      <button
                        className="discog-retry-btn"
                        type="button"
                        onClick={() => void retryFailed()}
                      >
                        Retry
                      </button>
                    </div>
                  ) : null}
                </>
              ) : (
                'Processing... this may take a moment'
              )}
            </div>
            <div className="discog-footer-actions">
              {phase === 'pick' ? (
                <>
                  <button className="discog-cancel-btn" type="button" onClick={onClose}>
                    Cancel
                  </button>
                  <button
                    className="discog-submit-btn"
                    id="discog-submit-btn"
                    type="button"
                    disabled={footer.disabled}
                    onClick={() => void start(false)}
                  >
                    <span className="discog-submit-icon">⬇</span>
                    <span id="discog-submit-text">{footer.submitText}</span>
                  </button>
                  {watchlistIdentity ? (
                    <button
                      className="discog-submit-btn discog-both-btn"
                      id="discog-both-btn"
                      type="button"
                      disabled={footer.disabled}
                      title="Queue the selected releases and add the artist to the watchlist"
                      onClick={() => void start(true)}
                    >
                      <span className="discog-submit-icon">👁</span>
                      <span id="discog-both-text">{footer.bothText}</span>
                    </button>
                  ) : null}
                </>
              ) : (
                <>
                  <button className="discog-cancel-btn" type="button" onClick={onClose}>
                    Close
                  </button>
                  {/* Static home: always mounted for download-capable profiles so
                      Close never shifts when results arrive — enabled once
                      processing finishes with wishlist additions. */}
                  {!asksFirst ? (
                    <button
                      className="discog-submit-btn"
                      type="button"
                      disabled={!(totals && totals.total_added > 0)}
                      title={
                        totals
                          ? totals.total_added > 0
                            ? 'Process the wishlist now'
                            : 'Nothing was added to the wishlist'
                          : 'Available when processing finishes'
                      }
                      onClick={() => {
                        onClose();
                        void fetch('/api/wishlist/process', { method: 'POST' });
                        window.showToast?.('Wishlist processing started', 'success');
                      }}
                    >
                      <span className="discog-submit-icon">🚀</span>
                      <span>Process Wishlist Now</span>
                    </button>
                  ) : null}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </BodyPortal>
  );
}

function DiscogCard({
  release,
  view,
  visible,
  index,
  checked,
  onToggle,
}: {
  release: DiscogRelease;
  view: ReturnType<typeof discogCardView>;
  visible: boolean;
  index: number;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <label
      className={`discog-card ${view.statusClass}`.trim()}
      data-type={release._type}
      data-is-live={String(view.isLive)}
      data-is-compilation={String(view.isCompilation)}
      data-is-featured={String(view.isFeatured)}
      style={{
        animationDelay: `${index * 0.03}s`,
        display: visible ? undefined : 'none',
      }}
    >
      <input
        type="checkbox"
        className="discog-card-cb"
        data-album-id={String(release.id)}
        data-album-name={view.albumName}
        data-tracks={String(view.tracks)}
        data-gap-source={release._gap_source || ''}
        checked={checked}
        onChange={onToggle}
      />
      <div className="discog-card-art">
        {release.image_url ? (
          <img src={release.image_url} alt="" loading="lazy" />
        ) : (
          <div className="discog-card-art-placeholder">🎵</div>
        )}
        {view.statusIcon ? <span className="discog-card-status">{view.statusIcon}</span> : null}
      </div>
      <div className="discog-card-info">
        <div className="discog-card-title">
          {view.albumName}
          {release.explicit === true ? <span className="explicit-badge"> E</span> : null}
        </div>
        <div className="discog-card-meta">
          {view.year}
          {view.year && view.tracks ? ' · ' : ''}
          {view.tracks ? `${view.tracks} tracks` : ''}
          {release._gap_source ? (
            <>
              {' · '}
              <span className="discog-gap-src">{release._gap_source}</span>
            </>
          ) : null}
        </div>
      </div>
      <div className="discog-card-check" />
    </label>
  );
}
