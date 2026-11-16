/**
 * #2106/#2107: how complete each artist's collection is, on the cards and in
 * the table, the sorts and the overview that read it, and the library index
 * remembering how you left it.
 *
 * The numbers are the table's own (owned / expected), worked out by the
 * server with the artist's collection settings: a release they leave out is
 * not expected, so it is never "missing". Complete is only ever said when
 * something is expected and every release's size is known.
 */

import { useQuery } from '@tanstack/react-query';
import { getRouteApi } from '@tanstack/react-router';
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

import type {
  LibraryV2ArtistSummary,
  LibraryV2Completion,
  LibraryV2Search,
  LibraryV2Sort,
} from '../-library-v2.types';

import {
  libraryV2OverviewQueryOptions,
  libraryV2OverviewUpgradesQueryOptions,
} from '../-library-v2.api';
import css from './library-collection.module.css';
import styles from './library-v2-page.module.css';

const Route = getRouteApi('/library');

/** the server's word for it, or the same rule on the row's own numbers */
export function artistCompletion(artist: LibraryV2ArtistSummary): LibraryV2Completion {
  if (artist.completion) return artist.completion;
  const total = artist.track_count ?? 0;
  const present = artist.tracks_present ?? 0;
  if (total <= 0) return 'unknown';
  if (present < total) return 'incomplete';
  return (artist.unverified_albums ?? 0) > 0 ? 'unchecked' : 'complete';
}

export function completionPercent(present: number, total: number): number | null {
  if (total <= 0) return null;
  return Math.min(100, (present * 100) / total);
}

const STATE_COPY: Record<LibraryV2Completion, string> = {
  complete: 'Complete',
  incomplete: 'Missing tracks',
  unchecked: 'Not checked',
  unknown: 'Nothing expected yet',
};

const STATE_TITLE: Record<LibraryV2Completion, string> = {
  complete: 'Every track this artist’s collection settings want is here',
  incomplete: 'Some wanted tracks are not here yet',
  unchecked:
    'Everything known is here, but a release’s size has not been looked up yet, so this may not be all of it',
  unknown: 'Nothing is expected for this artist yet',
};

type ShowMissing = (artist: Pick<LibraryV2ArtistSummary, 'id' | 'name'>) => void;

/**
 * how the meter opens the missing tracks. the list provides it, so a card on
 * its own (no router around it) still draws, just without the link
 */
export const ShowMissingContext = createContext<ShowMissing | null>(null);

export function ShowMissingProvider({ children }: { children: ReactNode }) {
  const showMissing = useShowMissing();
  return <ShowMissingContext.Provider value={showMissing}>{children}</ShowMissingContext.Provider>;
}

/** open the Wanted list on this artist's missing tracks */
export function useShowMissing(): ShowMissing {
  const navigate = Route.useNavigate();
  return (artist: Pick<LibraryV2ArtistSummary, 'id' | 'name'>) =>
    void navigate({
      search: (prev: LibraryV2Search) => ({
        ...prev,
        section: 'wanted' as const,
        wantedKind: 'missing' as const,
        wantedArtist: artist.id,
        wantedArtistName: artist.name,
        q: '',
        page: 1,
        artist: undefined,
        album: undefined,
      }),
    });
}

/**
 * The owned / expected count with a thin bar, green when complete and amber
 * when tracks are missing. The missing count opens those tracks. Quality
 * ("could be better") is its own thing and stays where it was.
 */
export function CollectionMeter({
  artist,
  variant,
}: {
  artist: LibraryV2ArtistSummary;
  variant: 'card' | 'row';
}) {
  const showMissing = useContext(ShowMissingContext);
  const state = artistCompletion(artist);
  const present = artist.tracks_present ?? 0;
  const total = artist.track_count ?? 0;
  const missing = Math.max(0, total - present);
  const percent = completionPercent(present, total);
  const counts =
    total > 0 ? (
      <span className={css.counts}>
        {variant === 'card'
          ? `${present.toLocaleString()} / ${total.toLocaleString()} tracks`
          : `${present.toLocaleString()}/${total.toLocaleString()}`}
      </span>
    ) : (
      <span className={css.counts}>{variant === 'card' ? 'No tracks expected' : '—'}</span>
    );
  return (
    <div
      className={`${css.meter} ${variant === 'card' ? css.card : css.row}`}
      data-completion={state}
      title={STATE_TITLE[state]}
    >
      <div className={css.top}>
        {counts}
        {variant === 'row' && percent !== null ? (
          <span className={css.percent}>{Math.floor(percent)}%</span>
        ) : null}
      </div>
      <span
        className={css.bar}
        role="progressbar"
        aria-label={`${artist.name}: ${present} of ${total} tracks`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent === null ? undefined : Math.floor(percent)}
      >
        <span className={css.fill} style={{ width: `${percent ?? 0}%` }} />
      </span>
      {missing > 0 && !showMissing ? (
        <span className={css.missing}>{missing.toLocaleString()} missing</span>
      ) : missing > 0 && showMissing ? (
        <button
          type="button"
          className={css.missing}
          title={`Show ${artist.name}’s missing tracks`}
          onClick={(event) => {
            event.stopPropagation();
            showMissing(artist);
          }}
        >
          {missing.toLocaleString()} missing
        </button>
      ) : variant === 'card' ? (
        <span className={css.state}>
          {state === 'complete' ? '✓ ' : ''}
          {STATE_COPY[state]}
        </span>
      ) : state === 'unchecked' ? (
        <span className={css.state}>Not checked</span>
      ) : null}
    </div>
  );
}

// --- sorting -----------------------------------------------------------------

export const ARTIST_SORT_LABELS: Record<LibraryV2Sort, string> = {
  name: 'Name',
  added: 'Recently added',
  albums: 'Album count',
  tracks: 'Track count',
  completion: 'Completion',
  missing: 'Missing tracks',
  upgrades: 'Could be better',
  incomplete_albums: 'Incomplete albums',
};

/** which way each sort runs when nobody turned it */
const NATURAL_DESC: Record<LibraryV2Sort, boolean> = {
  name: false,
  added: true,
  albums: true,
  tracks: true,
  completion: false,
  missing: true,
  upgrades: true,
  incomplete_albums: true,
};

export function sortIsDescending(sort: LibraryV2Sort, dir: string): boolean {
  if (dir === 'asc') return false;
  if (dir === 'desc') return true;
  return NATURAL_DESC[sort];
}

/** the artist sort with a button that turns it around (#2107) */
export function ArtistSortControl() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const descending = sortIsDescending(search.sort, search.dir);
  return (
    <div className={css.sort}>
      <select
        aria-label="Sort artists"
        id="library-sort"
        className={styles.select}
        value={search.sort}
        onChange={(e) =>
          void navigate({
            search: (p: LibraryV2Search) => ({
              ...p,
              sort: e.target.value as LibraryV2Sort,
              dir: '' as const,
              page: 1,
            }),
          })
        }
      >
        <optgroup label="Library">
          {(['name', 'added', 'albums', 'tracks'] as const).map((value) => (
            <option key={value} value={value}>
              {ARTIST_SORT_LABELS[value]}
            </option>
          ))}
        </optgroup>
        <optgroup label="Collection">
          {(['completion', 'missing', 'incomplete_albums', 'upgrades'] as const).map((value) => (
            <option key={value} value={value}>
              {ARTIST_SORT_LABELS[value]}
            </option>
          ))}
        </optgroup>
      </select>
      <button
        type="button"
        className={`${styles.btnGhost} ${css.dirButton}`}
        aria-label={
          descending ? 'Sorted high to low, turn around' : 'Sorted low to high, turn around'
        }
        title={descending ? 'High to low (Z to A)' : 'Low to high (A to Z)'}
        onClick={() =>
          void navigate({
            search: (p: LibraryV2Search) => ({
              ...p,
              dir: descending ? ('asc' as const) : ('desc' as const),
              page: 1,
            }),
          })
        }
      >
        <svg
          viewBox="0 0 24 24"
          width="15"
          height="15"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className={descending ? css.dirDown : ''}
        >
          <path d="M12 19V5M6 11l6-6 6 6" />
        </svg>
      </button>
    </div>
  );
}

// --- the overview ------------------------------------------------------------

const OVERVIEW_KEY = 'soulsync:library-overview-open';

function readFlag(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

function writeFlag(key: string, on: boolean) {
  try {
    window.localStorage.setItem(key, on ? '1' : '0');
  } catch {
    /* private mode: it just isn't kept */
  }
}

export function useOverviewOpen(): [boolean, (on: boolean) => void] {
  const [open, setOpen] = useState(() => readFlag(OVERVIEW_KEY));
  return [
    open,
    (on: boolean) => {
      writeFlag(OVERVIEW_KEY, on);
      setOpen(on);
    },
  ];
}

export function OverviewToggle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      className={`${styles.btnGhost} ${open ? css.toggleOn : ''}`}
      aria-expanded={open}
      aria-controls="library-overview"
      onClick={onToggle}
    >
      Overview
    </button>
  );
}

function Stat({
  label,
  value,
  onClick,
  tone,
  title,
}: {
  label: string;
  value: ReactNode;
  onClick?: () => void;
  tone?: 'good' | 'warn';
  title?: string;
}) {
  const body = (
    <>
      <span className={css.statValue}>{value}</span>
      <span className={css.statLabel}>{label}</span>
    </>
  );
  return onClick ? (
    <button type="button" className={css.stat} data-tone={tone} title={title} onClick={onClick}>
      {body}
    </button>
  ) : (
    <div className={css.stat} data-tone={tone} title={title}>
      {body}
    </div>
  );
}

const n = (value: number | undefined) => (value === undefined ? '…' : value.toLocaleString());

/** #2107: the library at a glance. each number that is a filter applies it */
export function LibraryOverview() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const overview = useQuery(libraryV2OverviewQueryOptions());
  const upgrades = useQuery(libraryV2OverviewUpgradesQueryOptions());
  const data = overview.data;
  const set = (patch: Partial<LibraryV2Search>) =>
    void navigate({ search: (prev: LibraryV2Search) => ({ ...prev, ...patch, page: 1 }) });
  if (overview.isError) {
    return (
      <div id="library-overview" className={css.overview} role="status">
        <span className={css.note}>Could not load the overview.</span>
      </div>
    );
  }
  return (
    <div id="library-overview" className={css.overview} aria-label="Library overview">
      <Stat label="Artists" value={n(data?.artists)} />
      <Stat label="Tracks owned" value={n(data?.owned_tracks)} />
      <Stat
        label="Complete"
        tone="good"
        value={n(data?.complete_artists)}
        title="Show only complete artists"
        onClick={() => set({ completion: 'complete' })}
      />
      <Stat
        label="Missing tracks"
        tone="warn"
        value={n(data?.incomplete_artists)}
        title="Show only artists with missing tracks"
        onClick={() => set({ completion: 'incomplete' })}
      />
      <Stat
        label="Not checked"
        value={n(data?.unchecked_artists)}
        title="Artists with nothing expected yet, or a release whose size was never looked up"
        onClick={() => set({ completion: 'unchecked' })}
      />
      <Stat
        label="Tracks missing"
        value={n(data?.missing_tracks)}
        title="Open the Wanted list"
        onClick={() =>
          set({
            section: 'wanted',
            wantedKind: 'missing',
            wantedArtist: undefined,
            wantedArtistName: undefined,
            q: '',
          })
        }
      />
      <Stat
        label="Could be better"
        value={upgrades.isError ? '—' : n(upgrades.data?.upgrade_artists)}
        title="Artists with tracks below their quality profile"
        onClick={() => set({ quality: search.quality === 'upgradable' ? '' : 'upgradable' })}
      />
      <Stat
        label="On Watchlist"
        value={n(data?.watchlist_artists)}
        title="Show only artists on your Watchlist"
        onClick={() => set({ monitored: 'monitored' })}
      />
    </div>
  );
}

// --- coming back to the list ---------------------------------------------------

/** the artist list's own params: what "the way you left it" means */
export const INDEX_PARAMS = [
  'view',
  'sort',
  'dir',
  'monitored',
  'letter',
  'source',
  'quality',
  'review',
  'completion',
  'missingMin',
  'missingMax',
  'pctMin',
  'pctMax',
  'credit',
] as const;

const INDEX_KEY = 'soulsync:library-index';

/** the index params that differ from a fresh list, for the next visit */
export function indexParamsOf(search: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of INDEX_PARAMS) {
    const value = search[key];
    if (value === undefined || value === '' || value === null) continue;
    if (key === 'view' && value === 'cards') continue;
    if (key === 'sort' && value === 'name') continue;
    if ((key === 'monitored' || key === 'letter') && value === 'all') continue;
    out[key] = value;
  }
  return out;
}

export function rememberIndex(
  profileId: number | null | undefined,
  search: Record<string, unknown>,
) {
  try {
    window.localStorage.setItem(
      `${INDEX_KEY}:${profileId ?? 0}`,
      JSON.stringify(indexParamsOf(search)),
    );
  } catch {
    /* private mode */
  }
}

export function recallIndex(profileId: number | null | undefined): Record<string, unknown> | null {
  try {
    const raw = window.localStorage.getItem(`${INDEX_KEY}:${profileId ?? 0}`);
    const parsed = raw ? (JSON.parse(raw) as unknown) : null;
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

// where the list was scrolled, per list (the url says which), for the trip
// to an artist and back
const scrollMemory = new Map<string, number>();

function scrollParent(el: HTMLElement | null): HTMLElement | Window {
  let node = el?.parentElement ?? null;
  while (node) {
    const overflow = getComputedStyle(node).overflowY;
    if ((overflow === 'auto' || overflow === 'scroll') && node.scrollHeight > node.clientHeight)
      return node;
    node = node.parentElement;
  }
  return window;
}

function scrollTopOf(target: HTMLElement | Window): number {
  return target === window ? window.scrollY : (target as HTMLElement).scrollTop;
}

/**
 * Keeps the list's scroll for a trip to an artist and back: saved when the
 * list goes away, put back once the same list has drawn again.
 */
export function useIndexScrollMemory(listKey: string, ready: boolean) {
  const anchor = useRef<HTMLDivElement>(null);
  const restored = useRef(false);
  // read before anything scrolls: the router's own scroll to the top on
  // arrival would otherwise be saved over it
  const saved = useRef(scrollMemory.get(listKey));
  useEffect(() => {
    const el = anchor.current;
    const target = scrollParent(el);
    const save = () => scrollMemory.set(listKey, scrollTopOf(target));
    target.addEventListener('scroll', save, { passive: true });
    return () => target.removeEventListener('scroll', save);
  }, [listKey]);
  useEffect(() => {
    if (!ready || restored.current) return;
    restored.current = true;
    const top = saved.current;
    if (!top) return;
    const target = scrollParent(anchor.current);
    window.requestAnimationFrame(() => target.scrollTo({ top }));
  }, [listKey, ready]);
  return anchor;
}
