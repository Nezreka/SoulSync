/**
 * The video Download History modal — a permanent timeline of every completed
 * grab (movies + episodes + YouTube).
 *
 * A React port of webui/static/video/video-download-history.js, reusing the
 * same .vdh-* markup vocabulary so the existing CSS applies unchanged.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { VdlHistoryItem, VdlHistoryTab } from '../-vdl.types';

import {
  blockVideoHistoryRelease,
  blockVideoUploader,
  clearVideoHistory,
  deleteVideoHistory,
  fetchVideoHistory,
} from '../-vdl.api';
import {
  vdlDayKey,
  vdlDayLabel,
  vdlFmtHistorySize,
  vdlFmtWhen,
  vdlHistoryOutcome,
  vdlPad2,
} from '../-vdl.helpers';

const toast = (message: string, type: string) => window.showToast?.(message, type);

const TABS: { value: VdlHistoryTab; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'movie', label: 'Movies' },
  { value: 'show', label: 'TV' },
  { value: 'youtube', label: 'YouTube' },
];

function HistoryRow({
  item,
  open,
  onToggle,
  onRemoved,
}: {
  item: VdlHistoryItem;
  open: boolean;
  onToggle: () => void;
  onRemoved: () => void;
}) {
  const [blocked, setBlocked] = useState<'release' | 'uploader' | null>(null);
  const [busy, setBusy] = useState(false);
  const isShow = item.kind === 'show';
  const [ocLabel, ocClass] = vdlHistoryOutcome(item.outcome);
  const sxe =
    item.season_number != null && item.episode_number != null
      ? `S${vdlPad2(item.season_number)}E${vdlPad2(item.episode_number)}`
      : '';
  const sub = [
    sxe,
    item.year,
    item.quality_label,
    item.resolution,
    item.video_codec,
    vdlFmtHistorySize(item.size_bytes),
  ]
    .filter(Boolean)
    .join(' · ');

  const stop = (e: React.MouseEvent) => e.stopPropagation();

  const onRedownload = async (e: React.MouseEvent) => {
    stop(e);
    if (busy) return;
    setBusy(true);
    const r = await deleteVideoHistory(item.id);
    if (r && (r.success ?? r.ok)) {
      toast("Forgotten — it'll re-download on the next scan", 'success');
      onRemoved();
    } else {
      setBusy(false);
    }
  };

  const onBlockRelease = async (e: React.MouseEvent) => {
    stop(e);
    if (busy || blocked) return;
    setBusy(true);
    const r = await blockVideoHistoryRelease(item.id);
    if (r && (r.success ?? r.ok)) {
      toast('Release blocked — it will never be picked again', 'success');
      setBlocked('release');
    } else {
      toast(r?.error || 'Could not block that release', 'error');
    }
    setBusy(false);
  };

  const onBlockUploader = async (e: React.MouseEvent) => {
    stop(e);
    if (busy || blocked === 'uploader') return;
    setBusy(true);
    const r = await blockVideoUploader(item.id);
    if (r && (r.success ?? r.ok)) {
      toast('Uploader blocked — future searches will skip them', 'success');
      setBlocked('uploader');
    } else {
      toast(r?.error || 'Could not block that uploader', 'error');
    }
    setBusy(false);
  };

  const canBlockRelease =
    (item.outcome === 'failed' || item.outcome === 'import_failed') &&
    item.username &&
    item.filename &&
    item.source !== 'youtube';
  const canBlockUploader = Boolean(item.username) && item.source !== 'youtube';

  const dl = (label: string, val?: string | number | null) =>
    val ? (
      <div className="vdh-d">
        <span className="vdh-d-k">{label}</span>
        <span className="vdh-d-v">{String(val)}</span>
      </div>
    ) : null;

  return (
    <div className={`vdh-row${open ? ' vdh-row--open' : ''}`} data-vdh-row onClick={onToggle}>
      <div className="vdh-row-main">
        {item.poster_url ? (
          <img
            className="vdh-poster"
            src={item.poster_url}
            alt=""
            loading="lazy"
            onError={(e) => e.currentTarget.classList.add('vdh-poster--none')}
          />
        ) : (
          <span className="vdh-poster vdh-poster--none">{isShow ? '📺' : '🎬'}</span>
        )}
        <div className="vdh-row-info">
          <div className="vdh-row-title">
            {item.title || item.release_title || 'Unknown'}
            {sxe ? <span className="vdh-sxe"> {sxe}</span> : null}
          </div>
          <div className="vdh-row-sub">{sub}</div>
        </div>
        <div className="vdh-row-right">
          <span className={`vdh-oc ${ocClass}`}>{ocLabel}</span>
          <span className="vdh-when">{vdlFmtWhen(item.completed_at)}</span>
          <span className="vdh-chev" aria-hidden="true">
            ›
          </span>
        </div>
      </div>
      {open ? (
        <div className="vdh-detail" onClick={stop}>
          {dl('Release', item.release_title)}
          {dl('Source', [item.source, item.username].filter(Boolean).join(' · '))}
          {dl('Codec', [item.resolution, item.video_codec].filter(Boolean).join(' · '))}
          {dl('Size', vdlFmtHistorySize(item.size_bytes))}
          {dl('Grabbed', vdlFmtWhen(item.grabbed_at))}
          {dl('Finished', vdlFmtWhen(item.completed_at))}
          {dl('Path', item.dest_path)}
          {item.error ? (
            <div className="vdh-d vdh-d--err">
              <span className="vdh-d-k">Error</span>
              <span className="vdh-d-v">{item.error}</span>
            </div>
          ) : null}
          <div className="vdh-detail-act">
            <button
              type="button"
              className="vdh-redl"
              title="Forget this grab so it re-downloads on the next scan"
              disabled={busy}
              onClick={onRedownload}
            >
              &#8635; Re-download
            </button>
            {canBlockRelease ? (
              <button
                type="button"
                className="vdh-redl vdh-block"
                title="Never pick this exact release again"
                disabled={busy || blocked !== null}
                onClick={onBlockRelease}
              >
                {blocked === 'release' ? 'Blocked' : <>&#8856; Block release</>}
              </button>
            ) : null}
            {canBlockUploader ? (
              <button
                type="button"
                className="vdh-redl vdh-block"
                title={`Never grab from this uploader (${item.username}) again`}
                disabled={busy || blocked === 'uploader'}
                onClick={onBlockUploader}
              >
                {blocked === 'uploader' ? 'Uploader blocked' : <>&#8856; Block uploader</>}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function VdlHistoryModal({
  open,
  onClose,
  onCountChange,
}: {
  open: boolean;
  onClose: () => void;
  /** Fired when the total changes (clear / re-download) so the header badge stays honest. */
  onCountChange: (n: number | null) => void;
}) {
  const [tab, setTab] = useState<VdlHistoryTab>('all');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<VdlHistoryItem[]>([]);
  const [counts, setCounts] = useState({ movie: 0, show: 0, youtube: 0, total: 0 });
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  /* request sequencing: a newer tab/search/page request supersedes an older
   * in-flight one. The older response is discarded when it lands instead of
   * clobbering items/counts with stale data — and, unlike a boolean
   * in-flight guard, newer requests are never silently dropped while the
   * user is already looking at the new tab. */
  const reqSeqRef = useRef(0);

  const load = useCallback(
    async (nextPage: number, nextSearch: string, nextTab: VdlHistoryTab, append: boolean) => {
      const seq = ++reqSeqRef.current;
      setLoading(true);
      try {
        const data = await fetchVideoHistory(nextTab, nextSearch, nextPage);
        if (seq !== reqSeqRef.current) return; // superseded — discard stale response
        setCounts({
          movie: data.counts.movie ?? 0,
          show: data.counts.show ?? 0,
          youtube: data.counts.youtube ?? 0,
          total: data.counts.total ?? 0,
        });
        onCountChange(data.counts.total ?? 0);
        setTotalPages(data.totalPages);
        setItems((prev) => (append ? [...prev, ...data.items] : data.items));
      } finally {
        if (seq === reqSeqRef.current) setLoading(false);
      }
    },
    [onCountChange],
  );

  /* open → reset and load; close → release the scroll lock */
  useEffect(() => {
    if (!open) return;
    setPage(1);
    setSearch('');
    setSearchInput('');
    setExpanded(new Set());
    void load(1, '', tab, false);
    document.body.classList.add('vdh-locked');
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.body.classList.remove('vdh-locked');
      document.removeEventListener('keydown', onKey);
    };
    // tab intentionally not reset on open — the old modal kept it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
      void load(1, searchInput.trim(), tab, false);
    }, 250);
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput, open]);

  if (!open) return null;

  const selectTab = (t: VdlHistoryTab) => {
    if (t === tab) return;
    setTab(t);
    setPage(1);
    void load(1, search, t, false);
  };

  const loadMore = () => {
    const next = page + 1;
    setPage(next);
    void load(next, search, tab, true);
  };

  const onClear = async () => {
    const go =
      typeof window.showConfirmDialog === 'function'
        ? await window.showConfirmDialog({
            title: 'Clear download history',
            message: 'Forget the entire download history? Items still wanted may re-download.',
            confirmText: 'Clear',
            destructive: true,
          })
        : window.confirm('Clear the entire download history?');
    if (!go) return;
    const r = await clearVideoHistory();
    toast(`Cleared ${Number(r?.removed ?? 0)} from history`, 'info');
    setPage(1);
    void load(1, search, tab, false);
  };

  const toggleRow = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  /* group rows under day headers (the timeline feel) */
  const body: React.ReactNode[] = [];
  let lastDay: string | null = null;
  for (const item of items) {
    const day = vdlDayKey(item.completed_at || item.grabbed_at);
    if (day !== lastDay) {
      lastDay = day;
      body.push(
        <div className="vdh-day" key={`day-${day || 'none'}-${body.length}`}>
          {vdlDayLabel(item.completed_at || item.grabbed_at)}
        </div>,
      );
    }
    const key = String(item.id);
    body.push(
      <HistoryRow
        key={key}
        item={item}
        open={expanded.has(key)}
        onToggle={() => toggleRow(key)}
        onRemoved={() => setItems((prev) => prev.filter((it) => String(it.id) !== key))}
      />,
    );
  }

  const sub = `${counts.total} grab${counts.total === 1 ? '' : 's'} · ${counts.movie} movies · ${counts.show} episodes · ${counts.youtube} youtube`;

  return (
    <div
      className="vdh-overlay vdh-overlay--on"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="vdh-modal" role="dialog" aria-modal="true" aria-label="Download history">
        <div className="vdh-head">
          <div className="vdh-head-titles">
            <h2 className="vdh-title">Download History</h2>
            <p className="vdh-sub">{sub}</p>
          </div>
          <button type="button" className="vdh-close" aria-label="Close" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="vdh-toolbar">
          <div className="vdh-tabs" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.value}
                type="button"
                role="tab"
                aria-selected={tab === t.value}
                className={`vdh-tab${tab === t.value ? ' vdh-tab--on' : ''}`}
                onClick={() => selectTab(t.value)}
              >
                {t.label}{' '}
                <span className="vdh-tab-n">
                  {t.value === 'all'
                    ? counts.total
                    : t.value === 'movie'
                      ? counts.movie
                      : t.value === 'show'
                        ? counts.show
                        : counts.youtube}
                </span>
              </button>
            ))}
          </div>
          <div className="vdh-search">
            <svg
              className="vdh-search-ic"
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="M21 21l-4.3-4.3" />
            </svg>
            <input
              type="text"
              className="vdh-search-input"
              placeholder="Search history…"
              autoComplete="off"
              spellCheck="false"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="vdh-clear"
            title="Clear the whole download history"
            onClick={() => void onClear()}
          >
            Clear
          </button>
        </div>
        <div className={`vdh-body${loading && !items.length ? ' vdh-body--loading' : ''}`}>
          {items.length === 0 && !loading ? (
            <div className="vdh-empty">
              <div className="vdh-empty-ic">📦</div>
              <div className="vdh-empty-t">{search ? 'No matches' : 'Nothing here yet'}</div>
              <div className="vdh-empty-s">
                {search
                  ? 'Try a different search.'
                  : 'Grabs you complete will be recorded here forever.'}
              </div>
            </div>
          ) : (
            body
          )}
        </div>
        {page < totalPages ? (
          <div className="vdh-foot">
            <button type="button" className="vdh-more" onClick={loadMore} disabled={loading}>
              Load more
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
