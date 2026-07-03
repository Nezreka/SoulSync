import { useEffect, useMemo, useState } from 'react';

import { searchSources, startSourceDownload, type SourceSearchResult } from '../-library-v2.api';
import styles from './library-v2-page.module.css';

/** Lidarr-style release age: "3d", "8mo", "2.1y" — usenet retention at a glance. */
function ageText(publishDate?: string | null): string {
  if (!publishDate) return '—';
  const then = Date.parse(publishDate);
  if (Number.isNaN(then)) return '—';
  const days = Math.max(0, (Date.now() - then) / 86_400_000);
  if (days < 1) return '<1d';
  if (days < 60) return `${Math.round(days)}d`;
  if (days < 365) return `${Math.round(days / 30.4)}mo`;
  return `${(days / 365.25).toFixed(1)}y`;
}

function ageDays(publishDate?: string | null): number {
  if (!publishDate) return Number.POSITIVE_INFINITY;
  const then = Date.parse(publishDate);
  if (Number.isNaN(then)) return Number.POSITIVE_INFINITY;
  return (Date.now() - then) / 86_400_000;
}

/** Rank a quality string for sorting (lossless hi-res > lossless > high lossy > rest). */
function qualityRank(r: SourceSearchResult): number {
  const q = ((r.result_type === 'album' ? r.dominant_quality : r.quality) ?? '').toLowerCase();
  const bitDepth = r.bit_depth ?? 0;
  const lossless = q.includes('flac') || q.includes('alac') || q.includes('wav');
  if (lossless && bitDepth > 16) return 4;
  if (lossless) return 3;
  const kbps = r.bitrate ? (r.bitrate > 5000 ? r.bitrate / 1000 : r.bitrate) : 0;
  if (kbps >= 256 || q.includes('320')) return 2;
  if (q) return 1;
  return 0;
}

type SortKey = 'source' | 'title' | 'quality' | 'size' | 'age' | 'availability';

function sortValue(r: SourceSearchResult, key: SortKey): number | string {
  switch (key) {
    case 'source':
      return sourceLabel(r);
    case 'title':
      return resultTitle(r).toLowerCase();
    case 'quality':
      return qualityRank(r);
    case 'size':
      return resultSize(r) ?? 0;
    case 'age':
      return ageDays(effMeta(r).publish_date);
    case 'availability': {
      const meta = effMeta(r);
      if (meta.grabs != null) return meta.grabs;
      if (meta.seeders != null) return meta.seeders;
      return (r.free_upload_slots ?? 0) * 100 - (r.queue_length ?? 0);
    }
  }
}

function fmtBytes(n?: number | null): string {
  if (!n || n <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function baseName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function resultTitle(r: SourceSearchResult): string {
  if (r.result_type === 'album') return r.album_title || baseName(r.album_path ?? '') || '—';
  return r.title ?? baseName(r.filename);
}

function resultSize(r: SourceSearchResult): number | null | undefined {
  return r.result_type === 'album' ? r.total_size : r.size;
}

function firstTrackNumber(r: SourceSearchResult, key: 'bit_depth' | 'sample_rate' | 'bitrate'): number | null {
  for (const track of r.tracks ?? []) {
    const value = track[key];
    if (typeof value === 'number' && value > 0) return value;
  }
  return null;
}

function resultQuality(r: SourceSearchResult): string {
  const fmt = ((r.result_type === 'album' ? r.dominant_quality : r.quality) ?? '').toUpperCase();
  const bitrate = r.bitrate ?? firstTrackNumber(r, 'bitrate');
  const rawSampleRate = r.sample_rate ?? firstTrackNumber(r, 'sample_rate');
  const rawBitDepth = r.bit_depth ?? firstTrackNumber(r, 'bit_depth');
  const kbps = bitrate ? (bitrate > 5000 ? Math.round(bitrate / 1000) : bitrate) : null;
  const bitDepth = rawBitDepth ? `${rawBitDepth}-bit` : null;
  const sampleRate = rawSampleRate
    ? `${Number((rawSampleRate / 1000).toFixed(rawSampleRate % 1000 === 0 ? 0 : 1))} kHz`
    : null;
  const resolution = [bitDepth, sampleRate].filter(Boolean).join('/');
  return [fmt, resolution || null, kbps ? `${kbps} kbps` : null].filter(Boolean).join(' / ') || '—';
}

function resultKey(r: SourceSearchResult): string {
  return `${r.username}::${r.result_type === 'album' ? (r.album_path ?? r.album_title) : r.filename}`;
}

const SOURCE_LABELS: Record<string, string> = {
  usenet: 'Usenet',
  hifi: 'HiFi',
  tidal: 'Tidal',
  qobuz: 'Qobuz',
  youtube: 'YouTube',
  deezer_dl: 'Deezer',
  soundcloud: 'SoundCloud',
  amazon: 'Amazon',
  lidarr: 'Lidarr',
};

/** The download source (Soulseek / Usenet / HiFi / …) for the Source column. */
function sourceLabel(r: SourceSearchResult): string {
  const u = (r.username ?? '').toLowerCase();
  return SOURCE_LABELS[u] ?? 'Soulseek';
}

/** Source metadata (indexer/grabs) — album results carry it on their first track. */
function effMeta(r: SourceSearchResult): NonNullable<SourceSearchResult['_source_metadata']> {
  if (r._source_metadata) return r._source_metadata;
  const t0 = r.tracks?.[0] as { _source_metadata?: SourceSearchResult['_source_metadata'] } | undefined;
  return t0?._source_metadata ?? {};
}

/** The peer (Soulseek) or indexer (Usenet) detail. */
function sourceDetail(r: SourceSearchResult): string {
  const meta = effMeta(r);
  if (meta.indexer) return meta.indexer;
  const u = (r.username ?? '').toLowerCase();
  return SOURCE_LABELS[u] ? '' : (r.username ?? '');
}

/** Source-appropriate availability metric (peers don't apply to Usenet, grabs
 *  don't apply to Soulseek), so each source shows what's meaningful for it. */
function availabilityCell(r: SourceSearchResult): string {
  const u = (r.username ?? '').toLowerCase();
  const meta = effMeta(r);
  if (u === 'usenet') {
    const parts: string[] = [];
    if (meta.grabs != null) parts.push(`${meta.grabs} grabs`);
    if (meta.seeders != null) parts.push(`${meta.seeders} seeders`);
    return parts.join(' · ') || '—';
  }
  if (SOURCE_LABELS[u]) return 'instant'; // streaming sources (HiFi/Tidal/…)
  // Soulseek peer: free slots + queue length.
  const slots = r.free_upload_slots ?? 0;
  const queue = r.queue_length ?? 0;
  return queue ? `${slots} slots · ${queue} queued` : `${slots} slots`;
}

type GrabState = 'pending' | 'done' | 'error';

/** Lidarr-style interactive search: search every configured SoulSync source for a
 *  release, pick one, and send it through the download pipeline. */
export function InteractiveSearchModal({
  initialQuery,
  onClose,
}: {
  initialQuery: string;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<SourceSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [grabbed, setGrabbed] = useState<Record<string, GrabState>>({});
  const [qualityCheck, setQualityCheck] = useState(true);
  const [acoustidCheck, setAcoustidCheck] = useState(true);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'quality', dir: -1 });

  const sorted = useMemo(() => {
    const copy = [...results];
    copy.sort((a, b) => {
      const va = sortValue(a, sort.key);
      const vb = sortValue(b, sort.key);
      const cmp =
        typeof va === 'string' || typeof vb === 'string'
          ? String(va).localeCompare(String(vb))
          : va - vb;
      if (cmp !== 0) return cmp * sort.dir;
      // Stable tiebreak: better quality first, then larger size.
      return qualityRank(b) - qualityRank(a) || (resultSize(b) ?? 0) - (resultSize(a) ?? 0);
    });
    return copy;
  }, [results, sort]);

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: -1 }));
  }

  function SortTh({
    label,
    k,
    className,
  }: {
    label: string;
    k: SortKey;
    className?: string;
  }) {
    const active = sort.key === k;
    return (
      <th
        className={`${className ?? ''} ${styles.sortableTh}`}
        aria-sort={active ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}
        onClick={() => toggleSort(k)}
      >
        {label}
        {active ? <span className={styles.sortArrow}>{sort.dir === 1 ? '▲' : '▼'}</span> : null}
      </th>
    );
  }

  async function run(q: string) {
    if (!q.trim()) return;
    setLoading(true);
    setError(null);
    setResults([]);
    try {
      const all = await searchSources(q);
      setResults(all);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed');
    } finally {
      setLoading(false);
    }
  }

  // Auto-run once with the prefilled context query.
  useEffect(() => {
    void run(initialQuery);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function grab(r: SourceSearchResult) {
    const key = `${r.username}::${r.filename}`;
    setGrabbed((g) => ({ ...g, [key]: 'pending' }));
    try {
      await startSourceDownload(r, { qualityCheck, skipAcoustid: !acoustidCheck });
      setGrabbed((g) => ({ ...g, [key]: 'done' }));
    } catch {
      setGrabbed((g) => ({ ...g, [key]: 'error' }));
    }
  }

  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div
        className={`${styles.modal} ${styles.modalWide}`}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3>Interactive Search</h3>
          <button type="button" className={styles.iconAction} title="Close" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className={styles.searchBar}>
          <input
            className={styles.searchInput}
            value={query}
            placeholder="Search query…"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void run(query);
            }}
          />
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={loading}
            onClick={() => void run(query)}
          >
            {loading ? 'Searching…' : 'Search'}
          </button>
        </div>

        <div className={styles.searchOptions}>
          <label className={styles.checkOption}>
            <input
              type="checkbox"
              checked={qualityCheck}
              onChange={(e) => setQualityCheck(e.target.checked)}
            />
            Quality check
          </label>
          <label className={styles.checkOption}>
            <input
              type="checkbox"
              checked={acoustidCheck}
              onChange={(e) => setAcoustidCheck(e.target.checked)}
            />
            AcoustID check
          </label>
          <span className={styles.optionHint}>applied to grabs from this window</span>
        </div>

        {error ? <div className={styles.searchError}>{error}</div> : null}

        <div className={styles.resultsWrap}>
          {loading ? (
            <div className={styles.inlineLoading}>Searching all configured sources…</div>
          ) : results.length === 0 ? (
            <div className={styles.inlineLoading}>
              {error ? 'Search failed.' : 'No results — refine the query and search again.'}
            </div>
          ) : (
            <table className={styles.trackTable}>
              <thead>
                <tr>
                  <SortTh label="Source" k="source" className={styles.isSource} />
                  <SortTh label="Title" k="title" />
                  <th className={styles.isArtist}>Artist</th>
                  <SortTh label="Quality" k="quality" className={styles.isQuality} />
                  <SortTh label="Size" k="size" className={styles.colNum} />
                  <SortTh label="Age" k="age" className={styles.colNum} />
                  <SortTh label="Availability" k="availability" className={styles.isAvail} />
                  <th className={styles.isGrab}></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => {
                  const key = resultKey(r);
                  const state = grabbed[key];
                  const isAlbum = r.result_type === 'album';
                  return (
                    <tr key={`${key}-${i}`}>
                      <td>
                        <span className={styles.sourceBadge}>{sourceLabel(r)}</span>
                        {sourceDetail(r) ? (
                          <span className={styles.sourceDetail}>{sourceDetail(r)}</span>
                        ) : null}
                      </td>
                      <td title={r.filename}>
                        <span className={styles.isTitle}>{resultTitle(r)}</span>
                        {isAlbum ? (
                          <span className={styles.albumResultBadge}>
                            album · {r.track_count ?? r.tracks?.length ?? '?'} tracks
                          </span>
                        ) : null}
                      </td>
                      <td>{r.artist ?? '—'}</td>
                      <td className={styles.qualityText}>{resultQuality(r)}</td>
                      <td className={styles.colNum}>{fmtBytes(resultSize(r))}</td>
                      <td className={styles.colNum} title={effMeta(r).publish_date ?? undefined}>
                        {ageText(effMeta(r).publish_date)}
                      </td>
                      <td className={styles.isAvailCell}>{availabilityCell(r)}</td>
                      <td>
                        <button
                          type="button"
                          className={styles.toolButton}
                          disabled={state === 'pending' || state === 'done'}
                          onClick={() => void grab(r)}
                        >
                          {state === 'done'
                            ? 'Grabbed ✓'
                            : state === 'pending'
                              ? '…'
                              : state === 'error'
                                ? 'Retry'
                                : 'Download'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <div className={styles.modalFootNote}>
          Downloads run through the normal SoulSync pipeline (staging → processing → tagging). Use
          “Refresh &amp; Scan” afterwards to pull new files into the v2 library.
        </div>
      </div>
    </div>
  );
}
