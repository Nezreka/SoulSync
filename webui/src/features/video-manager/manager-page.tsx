/**
 * The video library, as a manager.
 *
 * The page used to be a wall of posters with five filters and six bulk
 * actions. This is the same library three ways: posters to browse, a table of
 * titles to sort and filter by what they are made of on disk, and a table of
 * the files themselves. Whatever is selected, in whichever view, the bar at
 * the bottom acts on.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { PageBar, PageFrame, PageHeader, PagePills, PageSearch } from '@/components/page-header';

import type { Facets, FileRow, Filters, Need, Side, Sort, TitleRow, ViewMode } from './types';

import { fetchFacets, fetchFiles, fetchMatchingIds, fetchTitles, ManagerError } from './api';
import { NEED_COPY, NEED_ORDER, countFilters, formatSize, noun, plural } from './format';
import { BulkBar } from './manager-bulk';
import { ActiveFilters, FilterPanel } from './manager-filters';
import { ChannelsGrid, LegacyPosters } from './manager-legacy';
import { FilesTable, PosterGrid, TitleTable, type ColumnId, defaultColumns } from './manager-views';
import { TitleSheet } from './title-sheet';
import { EMPTY_FILTERS } from './types';
import s from './video-manager.module.css';

type Tab = Side | 'channels';

const LIMITS: Record<ViewMode, number> = { posters: 60, table: 100, files: 100 };
const DEFAULT_SORT: Record<ViewMode, Sort> = {
  posters: { by: 'title', dir: 'asc' },
  table: { by: 'title', dir: 'asc' },
  files: { by: 'title', dir: 'asc' },
};

function remembered<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function remember(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode: the choice just isn't kept */
  }
}

export function openTitle(kind: 'movie' | 'show', id: number) {
  document.dispatchEvent(
    new CustomEvent('soulsync:video-open-detail', { detail: { kind, id, source: 'library' } }),
  );
}

function ViewSwitch({ value, onChange }: { value: ViewMode; onChange: (v: ViewMode) => void }) {
  const views: [ViewMode, string, string][] = [
    ['posters', 'Posters', 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z'],
    ['table', 'Titles', 'M4 6h16M4 12h16M4 18h16'],
    ['files', 'Files', 'M6 3h8l4 4v14H6zM14 3v4h4'],
  ];
  return (
    <div className={s.viewSwitch} role="group" aria-label="View">
      {views.map(([id, label, path]) => (
        <button
          key={id}
          type="button"
          className={s.viewBtn}
          aria-pressed={value === id}
          title={label}
          onClick={() => onChange(id)}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.8}
            aria-hidden="true"
          >
            <path d={path} strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}

export function ManagerPage() {
  const [tab, setTab] = useState<Tab>(() => remembered<Tab>('vmgr:tab', 'movies'));
  const [view, setViewState] = useState<ViewMode>(() =>
    remembered<ViewMode>('vmgr:view', 'posters'),
  );
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT.posters);
  const [page, setPage] = useState(1);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [titles, setTitles] = useState<TitleRow[]>([]);
  const [files, setFiles] = useState<FileRow[]>([]);
  const [totals, setTotals] = useState({ count: 0, size: 0, files: 0, pages: 1 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** the manager is the admin's; anyone else browses posters */
  const [restricted, setRestricted] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [editing, setEditing] = useState<{ kind: 'movie' | 'show'; id: number } | null>(null);
  const [columns, setColumns] = useState<Record<Side, ColumnId[]>>(() =>
    remembered('vmgr:columns', {
      movies: defaultColumns('movies'),
      shows: defaultColumns('shows'),
    }),
  );
  const [scan, setScan] = useState<{ running: boolean; label: string }>({
    running: false,
    label: '',
  });
  const [reloadKey, setReloadKey] = useState(0);
  const side: Side = tab === 'shows' ? 'shows' : 'movies';
  const requestRef = useRef(0);

  /** nothing from the list being left stays on screen while the next one
   *  loads: movie rows under a Shows heading read as the shows' own */
  const clearRows = () => {
    setTitles([]);
    setFiles([]);
    setTotals({ count: 0, size: 0, files: 0, pages: 1 });
    setLoading(true);
    setError(null);
  };
  const setView = (next: ViewMode) => {
    if (next === view) return;
    setViewState(next);
    remember('vmgr:view', next);
    setSort(DEFAULT_SORT[next]);
    setPage(1);
    setSelected(new Set());
    clearRows();
  };
  const changeTab = (next: Tab) => {
    if (next === tab) return;
    setTab(next);
    remember('vmgr:tab', next);
    setFilters(EMPTY_FILTERS);
    setPage(1);
    setSelected(new Set());
    setSort(DEFAULT_SORT[view]);
    setFacets(null);
    clearRows();
  };
  const changeFilters = useCallback((next: Filters) => {
    setFilters(next);
    setPage(1);
  }, []);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  // what there is to filter by, for this side
  useEffect(() => {
    if (tab === 'channels' || restricted) return;
    let live = true;
    fetchFacets(side)
      .then((f) => live && setFacets(f))
      .catch((e: unknown) => {
        if (live && e instanceof ManagerError && e.status === 403) setRestricted(true);
      });
    return () => {
      live = false;
    };
  }, [side, tab, restricted, reloadKey]);

  // the rows
  const searchKey = filters.search;
  useEffect(() => {
    if (tab === 'channels' || restricted) return;
    const request = ++requestRef.current;
    setLoading(true);
    const limit = LIMITS[view];
    const run = () => {
      const job =
        view === 'files'
          ? fetchFiles(side, filters, sort, page, limit).then((out) => {
              if (request !== requestRef.current) return;
              setFiles(out.items);
              setTotals({
                count: out.totals.files,
                size: out.totals.size_bytes,
                files: out.totals.files,
                pages: out.pagination.total_pages,
              });
            })
          : fetchTitles(side, filters, sort, page, limit).then((out) => {
              if (request !== requestRef.current) return;
              setTitles(out.items);
              setTotals({
                count: out.totals.titles,
                size: out.totals.size_bytes,
                files: out.totals.files,
                pages: out.pagination.total_pages,
              });
            });
      job
        .then(() => request === requestRef.current && setError(null))
        .catch((e: unknown) => {
          if (request !== requestRef.current) return;
          if (e instanceof ManagerError && e.status === 403) setRestricted(true);
          else setError(e instanceof Error ? e.message : 'Could not load the library');
        })
        .finally(() => request === requestRef.current && setLoading(false));
    };
    // typing in search waits a moment; everything else is immediate
    const timer = window.setTimeout(run, searchKey ? 220 : 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [side, tab, view, filters, sort, page, restricted, reloadKey]);

  // a scan changes everything underneath the page
  useEffect(() => {
    const onProgress = (e: Event) => {
      const d = (e as CustomEvent).detail || {};
      setScan({
        running: true,
        label: d.percent ? `Scanning ${Math.round(d.percent)}%` : 'Scanning',
      });
    };
    const onDone = () => {
      setScan({ running: false, label: '' });
      reload();
    };
    document.addEventListener('soulsync:video-scan-progress', onProgress);
    document.addEventListener('soulsync:video-scan-done', onDone);
    return () => {
      document.removeEventListener('soulsync:video-scan-progress', onProgress);
      document.removeEventListener('soulsync:video-scan-done', onDone);
    };
  }, [reload]);

  const toggle = useCallback((id: number, on?: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      const want = on ?? !next.has(id);
      if (want) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);
  const pageIds = useMemo(
    () => (view === 'files' ? files.map((f) => f.id) : titles.map((t) => t.id)),
    [view, files, titles],
  );
  const togglePage = useCallback(
    (on: boolean) => {
      setSelected((prev) => {
        const next = new Set(prev);
        for (const id of pageIds) {
          if (on) next.add(id);
          else next.delete(id);
        }
        return next;
      });
    },
    [pageIds],
  );
  const selectAllMatching = async () => {
    try {
      const out = await fetchMatchingIds(side, filters);
      setSelected(new Set(out.ids));
      if (out.truncated)
        window.showToast?.(`Selected the first ${out.ids.length.toLocaleString()}`, 'info');
    } catch (e) {
      window.showToast?.(e instanceof Error ? e.message : 'Could not select them all', 'error');
    }
  };

  const toggleNeed = (need: Need) => {
    const has = filters.needs.includes(need);
    changeFilters({
      ...filters,
      needs: has ? filters.needs.filter((n) => n !== need) : [...filters.needs, need],
    });
  };
  const changeSort = (by: string) => {
    setSort((prev) =>
      prev.by === by
        ? { by, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { by, dir: by === 'title' || by === 'path' || by === 'codec' ? 'asc' : 'desc' },
    );
    setPage(1);
  };
  const changeColumns = (next: ColumnId[]) => {
    const all = { ...columns, [side]: next };
    setColumns(all);
    remember('vmgr:columns', all);
  };

  const needsAttention = facets
    ? NEED_ORDER.reduce((sum, need) => sum + (facets.needs[need] ? 1 : 0), 0)
    : 0;
  const status = restricted ? (
    'Movies and shows from your media server'
  ) : facets && tab !== 'channels' ? (
    <>
      <b>{facets.titles.toLocaleString()}</b> {noun(side, facets.titles)}
      {facets.size_bytes ? (
        <>
          {' '}
          · <b>{formatSize(facets.size_bytes)}</b> in <b>{facets.files.toLocaleString()}</b> files
        </>
      ) : null}
      {facets.drives.length > 1 ? <> on {facets.drives.length} drives</> : null}
      {needsAttention ? null : facets.titles ? <> · nothing needs a look</> : null}
    </>
  ) : tab === 'channels' ? (
    'YouTube channels you follow'
  ) : (
    'Reading your library…'
  );

  const selecting = selected.size > 0;
  const filterCount = countFilters(filters);

  return (
    <PageFrame className={s.frame} data-video-manager="">
      <PageHeader
        title="Library"
        subtitle={status}
        actions={
          <button
            type="button"
            className="pgf-btn"
            disabled={scan.running}
            title="Read the media server again"
            onClick={() =>
              document.dispatchEvent(
                new CustomEvent('soulsync:video-scan-start', { detail: { mode: 'full' } }),
              )
            }
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.9}
              aria-hidden="true"
            >
              <path
                d="M20 12a8 8 0 1 1-2.6-5.9M20 4v5h-5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            {scan.running ? scan.label : 'Scan'}
          </button>
        }
      />

      <PageBar
        end={
          tab === 'channels' ? null : (
            <>
              {restricted ? null : <ViewSwitch value={view} onChange={setView} />}
              <PageSearch
                value={filters.search}
                onChange={(search) => changeFilters({ ...filters, search })}
                placeholder={view === 'files' ? 'Search titles and file names' : `Search ${side}`}
              />
            </>
          )
        }
      >
        <PagePills<Tab>
          ariaLabel="Library"
          value={tab}
          onChange={changeTab}
          pills={[
            { id: 'movies', label: 'Movies', count: side === 'movies' ? facets?.titles : null },
            { id: 'shows', label: 'Shows', count: side === 'shows' ? facets?.titles : null },
            { id: 'channels', label: 'Channels' },
          ]}
        />
      </PageBar>

      {tab === 'channels' ? (
        <ChannelsGrid />
      ) : restricted ? (
        <LegacyPosters side={side} search={filters.search} />
      ) : (
        <>
          <div className={s.needsRow}>
            <div className={s.needs} role="group" aria-label="Needs a look">
              {NEED_ORDER.filter((need) => facets?.needs[need] || filters.needs.includes(need)).map(
                (need) => (
                  <button
                    key={need}
                    type="button"
                    className={s.need}
                    aria-pressed={filters.needs.includes(need)}
                    title={NEED_COPY[need][side]}
                    onClick={() => toggleNeed(need)}
                  >
                    {NEED_COPY[need].label}
                    <span className={s.needCount}>
                      {(facets?.needs[need] ?? 0).toLocaleString()}
                    </span>
                  </button>
                ),
              )}
            </div>
            <div className={s.needsEnd}>
              <button
                type="button"
                className={s.filterBtn}
                aria-expanded={filtersOpen}
                onClick={() => setFiltersOpen((o) => !o)}
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.9}
                  aria-hidden="true"
                >
                  <path d="M4 6h16M7 12h10M10 18h4" strokeLinecap="round" />
                </svg>
                Filters
                {filterCount ? <span className={s.filterCount}>{filterCount}</span> : null}
              </button>
            </div>
          </div>

          {filtersOpen && facets ? (
            <FilterPanel
              side={side}
              view={view}
              facets={facets}
              filters={filters}
              onChange={changeFilters}
              onClose={() => setFiltersOpen(false)}
            />
          ) : null}
          <ActiveFilters side={side} filters={filters} facets={facets} onChange={changeFilters} />

          <div className={s.resultLine}>
            <span>
              {loading && !totals.count ? (
                'Loading…'
              ) : view === 'files' ? (
                <>
                  <b>{plural(totals.count, 'file', 'files')}</b>
                  {totals.size ? <> · {formatSize(totals.size)}</> : null}
                </>
              ) : (
                <>
                  <b>
                    {totals.count.toLocaleString()} {noun(side, totals.count)}
                  </b>
                  {totals.size ? <> · {formatSize(totals.size)}</> : null}
                </>
              )}
            </span>
            {view !== 'files' && totals.count > pageIds.length && !selecting ? (
              <button type="button" className={s.linkBtn} onClick={() => void selectAllMatching()}>
                Select all {totals.count.toLocaleString()}
              </button>
            ) : null}
          </div>

          {error ? <div className={s.empty}>{error}</div> : null}
          {!error && loading && pageIds.length === 0 ? (
            <div className={s.skeleton} aria-busy="true" aria-label="Loading">
              {Array.from({ length: 8 }, (_, i) => (
                <span key={i} />
              ))}
            </div>
          ) : null}
          {!error && !loading && totals.count === 0 ? (
            <div className={s.empty}>
              <strong>Nothing matches</strong>
              <span>
                {filterCount || filters.needs.length || filters.search
                  ? 'Try fewer filters.'
                  : 'Scan your media server to fill the library.'}
              </span>
              {filterCount || filters.needs.length || filters.search ? (
                <button
                  type="button"
                  className="pgf-btn"
                  onClick={() => changeFilters(EMPTY_FILTERS)}
                >
                  Clear filters
                </button>
              ) : null}
            </div>
          ) : null}

          {!error && view === 'posters' ? (
            <PosterGrid
              rows={titles}
              side={side}
              selected={selected}
              selecting={selecting}
              loading={loading}
              onToggle={toggle}
              onEdit={(row) => setEditing({ kind: row.kind, id: row.id })}
            />
          ) : null}
          {!error && view === 'table' ? (
            <TitleTable
              rows={titles}
              side={side}
              facets={facets}
              columns={columns[side]}
              onColumns={changeColumns}
              sort={sort}
              onSort={changeSort}
              selected={selected}
              onToggle={toggle}
              onTogglePage={togglePage}
              loading={loading}
              onEdit={(row) => setEditing({ kind: row.kind, id: row.id })}
            />
          ) : null}
          {!error && view === 'files' ? (
            <FilesTable
              rows={files}
              side={side}
              sort={sort}
              onSort={changeSort}
              selected={selected}
              onToggle={toggle}
              onTogglePage={togglePage}
              loading={loading}
            />
          ) : null}

          {totals.pages > 1 ? (
            <nav className={s.pager} aria-label="Pages">
              <button
                type="button"
                className="pgf-btn"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                Previous
              </button>
              <span>
                Page <b>{page}</b> of {totals.pages.toLocaleString()}
              </span>
              <button
                type="button"
                className="pgf-btn"
                disabled={page >= totals.pages}
                onClick={() => setPage(page + 1)}
              >
                Next
              </button>
            </nav>
          ) : null}

          {selecting ? (
            <BulkBar
              side={side}
              view={view}
              ids={[...selected]}
              facets={facets}
              onClear={() => setSelected(new Set())}
              onDone={() => {
                setSelected(new Set());
                reload();
              }}
            />
          ) : null}
        </>
      )}

      {editing ? (
        <TitleSheet
          kind={editing.kind}
          id={editing.id}
          facets={facets}
          onClose={() => setEditing(null)}
          onChanged={reload}
        />
      ) : null}
    </PageFrame>
  );
}
