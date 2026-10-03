import { useEffect, useMemo, useState } from 'react';

import type {
  ParsedWishlistTrack,
  WishlistArtistGroup,
  WishlistBulkAction,
  WishlistBulkResponse,
} from '../-wishlist.types';

import { trackCountLabel, upgradeTitle } from '../-wishlist.helpers';
import { openWishlistInspector } from '../../../features/downloads/inspector-modal';
import { WishlistCover } from './wishlist-cover';

/**
 * The dense LIST view — the nebula's operational twin (Boulder: "alternative
 * ways to display the wishlist? no functional change").
 *
 * Display-only by contract: every action here is one the nebula already has,
 * invoked through the SAME seams — remove via the page's mutations,
 * manual search via the candidate inspector (the orb fan's button does the
 * same), artist navigation via
 * window._navigateToArtistFromWishlist. Sorting is a local lens over the
 * groups the page already filtered; it fetches nothing and mutates nothing.
 */
export type WishlistListSort = 'failing' | 'name' | 'size';

const SORTS: { key: WishlistListSort; label: string; title: string }[] = [
  { key: 'failing', label: 'Failing first', title: 'Most stuck artists at the top' },
  { key: 'size', label: 'Most wanted', title: 'Largest artist groups first' },
  { key: 'name', label: 'A–Z', title: 'Alphabetical by artist' },
];

function sortGroups(groups: WishlistArtistGroup[], sort: WishlistListSort): WishlistArtistGroup[] {
  const copy = [...groups];
  if (sort === 'name') return copy.sort((a, b) => a.name.localeCompare(b.name));
  if (sort === 'size')
    return copy.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
  // failing: stuck counts first, then size, then name — the triage order.
  return copy.sort(
    (a, b) => b.failingCount - a.failingCount || b.total - a.total || a.name.localeCompare(b.name),
  );
}

function TrackRow({
  track,
  onRemoveTrack,
  onRemoveAlbum,
  selection,
  outcome,
}: {
  track: ParsedWishlistTrack;
  onRemoveTrack: (trackId: string) => void;
  /** Present only on album rows — the ✕ on the album cell removes the set. */
  onRemoveAlbum?: (albumName: string) => void;
  /** When present, renders the bulk-selection checkbox column. The boolean is
      shiftKey: shift-click selects the whole visible range, like every mail
      client and file manager. */
  selection?: { checked: boolean; onToggle: (range: boolean) => void } | null;
  /** Per-item outcome of the last bulk action — rendered in the status cell. */
  outcome?: { ok: boolean; message: string } | null;
}) {
  const statusTitle =
    outcome?.message ?? (track.failReason ? `Last error: ${track.failReason}` : undefined);
  const statusClass = outcome
    ? `wl-list-tries${outcome.ok ? ' wl-list-tries--ok' : ' wl-list-tries--fail'}`
    : `wl-list-tries${track.failing ? ' wl-list-tries--failing' : ''}`;
  return (
    <div
      className={`wl-list-track${track.failing ? ' wl-list-track--failing' : ''}${
        selection ? ' wl-list-track--select' : ''
      }`}
    >
      {selection ? (
        <input
          type="checkbox"
          className="wl-list-check"
          checked={selection.checked}
          // Click carries shiftKey for range-select; change is a noop because
          // the controlled `checked` flips in the click handler, which fires
          // first. Keyboard (Space) also fires click, with shiftKey false.
          onClick={(event) => selection.onToggle(event.shiftKey)}
          onChange={() => {}}
          aria-label={`Select ${track.track} by ${track.artist}`}
          data-testid={`wl-select-${track.id}`}
        />
      ) : null}
      <WishlistCover
        className="wl-list-cover"
        src={track.image}
        fallback={track.imageFallback}
        placeholder={<div className="wl-list-cover wl-list-cover--ph">♪</div>}
      />
      {/* Title with the album stacked beneath — ONE flexible cell, so wide
          screens read as left cluster + right cluster instead of columns
          adrift in a void (Boulder's image 8). */}
      <span className="wl-list-track-main">
        <span className="wl-list-track-name" title={track.track}>
          {track.track}
        </span>
        <span className="wl-list-track-album" title={track.album}>
          {/* Upgrades are neither missing tracks nor duplicates. Saying so on
              the row is the difference between "my wishlist is broken" and
              "my quality profile is doing its job". */}
          {track.upgrade ? (
            <span className="wl-list-upgrade" title={upgradeTitle(track)}>
              ⬆ upgrade{track.currentQuality ? ` · ${track.currentQuality}` : ''}
            </span>
          ) : null}
          {track.type === 'single' ? 'Single' : track.album}
          {track.type !== 'single' && onRemoveAlbum ? (
            <button
              type="button"
              className="wl-list-album-x"
              title={`Remove all tracks from "${track.album}"`}
              onClick={() => onRemoveAlbum(track.album)}
            >
              ✕
            </button>
          ) : null}
        </span>
      </span>
      <span className={statusClass} title={statusTitle}>
        {outcome
          ? `${outcome.ok ? '✓ ' : '✗ '}${outcome.message}`
          : track.retry > 0
            ? `${track.failing ? '⚠ ' : ''}${track.retry} tries`
            : 'queued'}
      </span>
      <span className="wl-list-last" title={track.lastTried || undefined}>
        {track.lastTried || '—'}
      </span>
      <span className="wl-list-track-actions">
        <button
          type="button"
          className="wl-list-btn"
          title="Interactive Search"
          onClick={() =>
            openWishlistInspector({
              id: track.id,
              name: track.track,
              artist: track.artist,
              album: track.album,
            })
          }
        >
          🔍
        </button>
        <button
          type="button"
          className="wl-list-btn wl-list-btn--x"
          title="Remove from wishlist"
          onClick={() => onRemoveTrack(track.id)}
        >
          ✕
        </button>
      </span>
    </div>
  );
}

export function WishlistList({
  groups,
  artistImages,
  onRemoveAlbum,
  onRemoveTrack,
  filterActive = false,
  onBulkAction,
  bulkBusy = false,
  onGrabArtist,
}: {
  /** Keyed by LOWERCASED artist name — buildArtistImageMap's contract. */
  artistImages: Map<string, string>;
  groups: WishlistArtistGroup[];
  onRemoveAlbum: (albumName: string) => void;
  onRemoveTrack: (trackId: string) => void;
  /** A live text filter auto-expands matches — collapsed search hits confuse. */
  filterActive?: boolean;
  /** When present, enables the checkbox column + bulk selection bar. */
  onBulkAction?: (action: WishlistBulkAction, trackIds: string[]) => Promise<WishlistBulkResponse>;
  /** True while a bulk action is in flight — disables the bulk buttons. */
  bulkBusy?: boolean;
  /** Queue every wanted track by the artist, right now. */
  onGrabArtist?: (group: WishlistArtistGroup) => void;
}) {
  const [sort, setSort] = useState<WishlistListSort>('failing');
  const sorted = useMemo(() => sortGroups(groups, sort), [groups, sort]);
  // Collapsed by default: the page opens as a compact artist INDEX (name,
  // count, failing badge) instead of a hundred-row scroll. null = the
  // explicit all-expanded state.
  const [openArtists, setOpenArtists] = useState<Set<string> | null>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastCheckedId, setLastCheckedId] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, { ok: boolean; message: string }>>({});
  const allOpen = openArtists === null;
  const isOpen = (name: string) => filterActive || allOpen || openArtists.has(name);
  const toggleArtist = (name: string) =>
    setOpenArtists((current) => {
      const next = new Set(current ?? sorted.map((g) => g.name));
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const allIds = useMemo(() => {
    const ids = new Set<string>();
    for (const g of groups) {
      for (const album of g.albums) for (const t of album.tracks) ids.add(t.id);
      for (const t of g.singles) ids.add(t.id);
    }
    return ids;
  }, [groups]);

  // A refresh may have removed tracks (skip) or changed rows — prune the
  // selection and stale outcomes to ids that are still on the page.
  useEffect(() => {
    setSelected((prev) => {
      if (prev.size === 0) return prev;
      const next = new Set([...prev].filter((id) => allIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
    setOutcomes((prev) => {
      const keys = Object.keys(prev).filter((id) => allIds.has(id));
      if (keys.length === Object.keys(prev).length) return prev;
      return Object.fromEntries(keys.map((id) => [id, prev[id]]));
    });
  }, [allIds]);

  const toggleSelected = (id: string, range: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (range && lastCheckedId && lastCheckedId !== id) {
        // Shift-click: select the whole visible span between the last
        // clicked row and this one, in rendered order.
        const a = visibleIds.indexOf(lastCheckedId);
        const b = visibleIds.indexOf(id);
        if (a !== -1 && b !== -1) {
          const [from, to] = a < b ? [a, b] : [b, a];
          for (let i = from; i <= to; i++) next.add(visibleIds[i]);
          return next;
        }
      }
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setLastCheckedId(id);
  };

  // Flat id order of the rows actually on screen — the shift-click range
  // walks this, so collapsed artists contribute nothing.
  const visibleIds = useMemo(() => {
    const ids: string[] = [];
    for (const g of sorted) {
      if (!(filterActive || allOpen || openArtists?.has(g.name))) continue;
      for (const album of g.albums) for (const t of album.tracks) ids.push(t.id);
      for (const t of g.singles) ids.push(t.id);
    }
    return ids;
  }, [sorted, filterActive, allOpen, openArtists]);

  const runBulk = async (action: WishlistBulkAction) => {
    if (!onBulkAction || selected.size === 0 || bulkBusy) return;
    const ids = [...selected];
    try {
      const response = await onBulkAction(action, ids);
      const next: Record<string, { ok: boolean; message: string }> = {};
      for (const r of response.results ?? []) next[r.id] = { ok: r.ok, message: r.message };
      setOutcomes(next);
    } catch {
      // Transport error — the page's mutation already toasted it.
    }
  };

  const selectionProps = (id: string) =>
    onBulkAction
      ? { checked: selected.has(id), onToggle: (range: boolean) => toggleSelected(id, range) }
      : null;

  return (
    <div className="wl-list" data-testid="wishlist-list">
      <div className="wl-list-sortbar">
        <div role="tablist" aria-label="Sort wishlist" className="wl-list-sorts">
          {SORTS.map((s) => (
            <button
              key={s.key}
              type="button"
              role="tab"
              aria-selected={sort === s.key}
              className={`wl-chip${sort === s.key ? ' active' : ''}`}
              title={s.title}
              onClick={() => setSort(s.key)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="wl-chip wl-list-expand-all"
          onClick={() => setOpenArtists(allOpen ? new Set() : null)}
        >
          {allOpen ? 'Collapse all' : 'Expand all'}
        </button>
      </div>

      {onBulkAction ? (
        <div
          className={`wl-bulkbar${selected.size > 0 ? ' has-selection' : ''}`}
          data-testid="wl-bulkbar"
        >
          <span className="wl-bulkbar-count" data-testid="wl-bulkbar-count">
            {selected.size} selected
          </span>
          <button
            type="button"
            className="wl-chip"
            disabled={selected.size === 0 || bulkBusy}
            title="Queue the selected tracks for download"
            onClick={() => void runBulk('grab')}
          >
            Grab
          </button>
          <button
            type="button"
            className="wl-chip"
            disabled={selected.size === 0 || bulkBusy}
            title="Remove the selected tracks and skip them until the ignore expires"
            onClick={() => void runBulk('skip')}
          >
            Skip
          </button>
          <button
            type="button"
            className="wl-chip"
            disabled={selected.size === 0 || bulkBusy}
            title="Clear the retry clock on the selected failing tracks"
            onClick={() => void runBulk('retry')}
          >
            Retry
          </button>
          <button
            type="button"
            className="wl-chip"
            disabled={selected.size === 0 || bulkBusy}
            title="Clear the selection"
            onClick={() => setSelected(new Set())}
          >
            Clear
          </button>
        </div>
      ) : null}

      {/* ONE flat table. Artist grouping is a slim separator row, never a
          box — at 100 tracks nested cards read as an outline document, not a
          track list (Boulder: 'looks wack'). Album context lives IN each row;
          removing a whole album is the ✕ on its album cell. */}
      {sorted.map((group) => (
        <div className="wl-list-section" key={group.name}>
          <div
            className="wl-list-sep"
            role="button"
            tabIndex={0}
            aria-expanded={isOpen(group.name)}
            title={isOpen(group.name) ? 'Collapse' : 'Expand'}
            onClick={() => toggleArtist(group.name)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                toggleArtist(group.name);
              }
            }}
          >
            <span className={`wl-list-chevron${isOpen(group.name) ? ' open' : ''}`}>▸</span>
            {artistImages.get(group.name.toLowerCase()) ? (
              <img
                className="wl-list-avatar"
                src={artistImages.get(group.name.toLowerCase())}
                alt=""
                loading="lazy"
              />
            ) : (
              <div className="wl-list-avatar wl-list-avatar--ph">♪</div>
            )}
            <button
              type="button"
              className="wl-list-artist-name"
              title="Open artist"
              onClick={(event) => {
                event.stopPropagation();
                window._navigateToArtistFromWishlist?.(group.name);
              }}
            >
              {group.name}
            </button>
            <span className="wl-list-artist-meta">
              {group.total} track{group.total === 1 ? '' : 's'}
            </span>
            {group.failingCount > 0 && (
              <span className="wl-list-failing-badge">⚠ {group.failingCount} failing</span>
            )}
            {onGrabArtist ? (
              <button
                type="button"
                className="wlp-sec-grab"
                title={`Download all ${trackCountLabel(group.total)} by ${group.name} now`}
                aria-label={`Download all tracks by ${group.name} now`}
                onClick={(event) => {
                  event.stopPropagation();
                  onGrabArtist(group);
                }}
              >
                <span aria-hidden="true">⬇ </span>Grab all
              </button>
            ) : null}
          </div>

          {isOpen(group.name) &&
            group.albums.flatMap((album) =>
              album.tracks.map((track) => (
                <TrackRow
                  key={track.id}
                  track={track}
                  onRemoveTrack={onRemoveTrack}
                  onRemoveAlbum={onRemoveAlbum}
                  selection={selectionProps(track.id)}
                  outcome={outcomes[track.id] ?? null}
                />
              )),
            )}
          {isOpen(group.name) &&
            group.singles.map((track) => (
              <TrackRow
                key={track.id}
                track={track}
                onRemoveTrack={onRemoveTrack}
                selection={selectionProps(track.id)}
                outcome={outcomes[track.id] ?? null}
              />
            ))}
        </div>
      ))}
    </div>
  );
}
