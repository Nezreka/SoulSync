/** the three views of the library: posters, a table of titles, a table of files. */

import clsx from 'clsx';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import type { Facets, FileRow, Side, Sort, TitleRow } from './types';

import {
  channelsLabel,
  codecName,
  formatDate,
  formatRuntime,
  formatSize,
  languageCodes,
  shortPath,
} from './format';
import { openTitle } from './manager-page';
import s from './video-manager.module.css';

const POSTER_W = 342;

export function posterUrl(kind: 'movie' | 'show', id: number, width = POSTER_W) {
  return `/api/video/poster/${kind}/${id}?w=${width}`;
}

function Poster({ row, small = false }: { row: TitleRow; small?: boolean }) {
  const [failed, setFailed] = useState(false);
  if (!row.has_poster || failed) {
    return (
      <div className={clsx(s.posterEmpty, small && s.posterSmall)} aria-hidden="true">
        {row.title.slice(0, 1).toUpperCase()}
      </div>
    );
  }
  return (
    <img
      className={clsx(s.posterImg, small && s.posterSmall)}
      src={posterUrl(row.kind, row.id, small ? 92 : POSTER_W)}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

/** resolution as a quiet badge; the top tier gets the accent */
export function ResBadge({ label }: { label: string }) {
  if (!label) return null;
  return <span className={clsx(s.res, label === '4K' && s.resTop)}>{label}</span>;
}

function Check({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (on: boolean) => void;
}) {
  return (
    <label className={s.check} onClick={(e) => e.stopPropagation()}>
      <input
        type="checkbox"
        checked={checked}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span aria-hidden="true" />
    </label>
  );
}

/** what a title needs, as small flags beside its name */
function Flags({ row }: { row: TitleRow }) {
  const flags: [string, string][] = [];
  if (!row.matched) flags.push(['Unmatched', 'SoulSync could not identify this']);
  if (row.below_cutoff) flags.push(['Below quality', `Its profile asks for ${row.cutoff}`]);
  if (row.extra_copies) flags.push([`+${row.extra_copies}`, 'Extra copies on disk']);
  if (!row.monitored) flags.push(['Off', 'Not monitored']);
  if (row.locked_fields.length)
    flags.push(['Locked', 'You set its details: scans and metadata refreshes leave them alone']);
  if (!flags.length) return null;
  return (
    <span className={s.flags}>
      {flags.map(([text, title]) => (
        <span key={text} className={s.flag} title={title}>
          {text}
        </span>
      ))}
    </span>
  );
}

/* ── posters ──────────────────────────────────────────────────────────────── */

export function PosterGrid({
  rows,
  side,
  selected,
  selecting,
  loading,
  onToggle,
  onEdit,
}: {
  rows: TitleRow[];
  side: Side;
  selected: Set<number>;
  selecting: boolean;
  loading: boolean;
  onToggle: (id: number, on?: boolean) => void;
  onEdit: (row: TitleRow) => void;
}) {
  return (
    <div className={clsx(s.grid, loading && s.dim)} data-selecting={selecting || undefined}>
      {rows.map((row) => {
        const picked = selected.has(row.id);
        const owned = row.episodes_owned ?? 0;
        const total = row.episodes_total ?? 0;
        return (
          <article key={row.id} className={clsx(s.card, picked && s.cardPicked)}>
            <a
              className={s.cardArt}
              aria-label={row.title}
              href={`/video-detail/library/${row.kind}/${row.id}`}
              onClick={(e) => {
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                e.preventDefault();
                if (selecting) onToggle(row.id);
                else openTitle(row.kind, row.id);
              }}
            >
              <Poster row={row} />
              {row.resolution ? (
                <span className={s.cardRes}>
                  <ResBadge label={row.resolution} />
                </span>
              ) : !row.owned ? (
                <span className={s.cardRes}>
                  <span className={s.res}>Wanted</span>
                </span>
              ) : null}
            </a>
            <span className={s.cardCheck}>
              <Check
                checked={picked}
                label={`Select ${row.title}`}
                onChange={(on) => onToggle(row.id, on)}
              />
            </span>
            <button
              type="button"
              className={s.cardEdit}
              aria-label={`Edit ${row.title}`}
              title="Edit"
              onClick={() => onEdit(row)}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.9}
                aria-hidden="true"
              >
                <path
                  d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
            <div className={s.cardBody}>
              <h3 className={s.cardTitle} title={row.title}>
                {row.title}
              </h3>
              <p className={s.cardMeta}>
                {[
                  row.year,
                  side === 'shows' ? `${owned}/${total} episodes` : null,
                  formatSize(row.size_bytes),
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              {side === 'shows' && total ? (
                <div className={s.progress} aria-hidden="true">
                  <span style={{ width: `${Math.min(100, Math.round((100 * owned) / total))}%` }} />
                </div>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

/* ── the table of titles ──────────────────────────────────────────────────── */

export type ColumnId =
  | 'year'
  | 'quality'
  | 'video'
  | 'audio'
  | 'subtitles'
  | 'size'
  | 'files'
  | 'episodes'
  | 'missing'
  | 'drive'
  | 'profile'
  | 'added'
  | 'rating'
  | 'runtime'
  | 'genres'
  | 'status';

interface Column {
  id: ColumnId;
  label: string;
  /** the server sort it maps to, when it has one */
  sort?: string;
  align?: 'right';
  sides?: Side[];
  render: (row: TitleRow, facets: Facets | null) => ReactNode;
}

function MixBar({ row }: { row: TitleRow }) {
  const total = row.file_count || 1;
  const order = ['4K', '1080p', '720p', 'SD'];
  const parts = order.filter((label) => row.resolution_mix[label]);
  if (parts.length <= 1) return <ResBadge label={row.resolution} />;
  return (
    <span className={s.mix} title={parts.map((p) => `${row.resolution_mix[p]} × ${p}`).join(', ')}>
      <span className={s.mixBar} aria-hidden="true">
        {parts.map((label) => (
          <span
            key={label}
            data-res={label}
            style={{ width: `${(100 * row.resolution_mix[label]) / total}%` }}
          />
        ))}
      </span>
      <span className={s.mixText}>{parts.join(' · ')}</span>
    </span>
  );
}

const COLUMNS: Column[] = [
  { id: 'year', label: 'Year', sort: 'year', render: (r) => r.year ?? '' },
  {
    id: 'episodes',
    label: 'Episodes',
    sort: 'episodes',
    sides: ['shows'],
    render: (r) => {
      const owned = r.episodes_owned ?? 0;
      const total = r.episodes_total ?? 0;
      return (
        <span className={s.episodes}>
          <span className={s.progress} aria-hidden="true">
            <span style={{ width: total ? `${Math.min(100, (100 * owned) / total)}%` : 0 }} />
          </span>
          <span>
            {owned} / {total}
          </span>
        </span>
      );
    },
  },
  {
    id: 'missing',
    label: 'Missing',
    sort: 'missing',
    align: 'right',
    sides: ['shows'],
    render: (r) => (r.episodes_missing ? <span className={s.warn}>{r.episodes_missing}</span> : ''),
  },
  {
    id: 'quality',
    label: 'Quality',
    sort: 'resolution',
    render: (r) => (r.kind === 'show' ? <MixBar row={r} /> : <ResBadge label={r.resolution} />),
  },
  {
    id: 'video',
    label: 'Video',
    // the codec and the picture; the sound has a column of its own
    render: (r) =>
      [r.video_codecs.map(codecName).slice(0, 2).join(' / '), r.dynamic_range[0]]
        .filter(Boolean)
        .join(' · '),
  },
  {
    id: 'audio',
    label: 'Audio',
    render: (r) =>
      [languageCodes(r.audio_languages), r.atmos ? 'Atmos' : channelsLabel(r.audio_channels)]
        .filter(Boolean)
        .join(' · '),
  },
  {
    id: 'subtitles',
    label: 'Subtitles',
    render: (r) =>
      r.subtitle_languages.length ? (
        languageCodes(r.subtitle_languages, 4)
      ) : r.owned && r.unprobed_files < r.file_count ? (
        <span className={s.muted}>None</span>
      ) : (
        ''
      ),
  },
  {
    id: 'size',
    label: 'Size',
    sort: 'size',
    align: 'right',
    render: (r) => formatSize(r.size_bytes),
  },
  { id: 'files', label: 'Files', sort: 'files', align: 'right', render: (r) => r.file_count || '' },
  {
    id: 'drive',
    label: 'Drive',
    render: (r) => (r.drive ? <span title={r.path ?? undefined}>{r.drive}</span> : ''),
  },
  {
    id: 'profile',
    label: 'Profile',
    render: (r, facets) =>
      r.quality_profile_id
        ? (facets?.quality_profiles.find((p) => p.id === r.quality_profile_id)?.name ?? 'Custom')
        : 'Default',
  },
  {
    id: 'rating',
    label: 'Rating',
    sort: 'rating',
    align: 'right',
    render: (r) => (r.rating ? r.rating.toFixed(1) : ''),
  },
  {
    id: 'runtime',
    label: 'Runtime',
    sort: 'runtime',
    align: 'right',
    render: (r) => formatRuntime((r.runtime_minutes || 0) * 60),
  },
  { id: 'genres', label: 'Genres', render: (r) => r.genres.slice(0, 3).join(', ') },
  { id: 'status', label: 'Status', render: (r) => r.status ?? '' },
  { id: 'added', label: 'Added', sort: 'added', render: (r) => formatDate(r.added_at) },
];

export function defaultColumns(side: Side): ColumnId[] {
  return side === 'shows'
    ? ['episodes', 'missing', 'quality', 'size', 'drive']
    : ['year', 'quality', 'video', 'audio', 'subtitles', 'size', 'drive'];
}

function SortHead({
  label,
  sortKey,
  sort,
  onSort,
  align,
}: {
  label: string;
  sortKey?: string;
  sort: Sort;
  onSort: (by: string) => void;
  align?: 'right';
}) {
  if (!sortKey) return <th className={clsx(align === 'right' && s.right)}>{label}</th>;
  const on = sort.by === sortKey;
  return (
    <th
      className={clsx(align === 'right' && s.right)}
      aria-sort={on ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type="button"
        className={clsx(s.sortBtn, on && s.sortOn)}
        onClick={() => onSort(sortKey)}
      >
        {label}
        <svg viewBox="0 0 12 12" aria-hidden="true" data-dir={on ? sort.dir : undefined}>
          <path d="M3 5l3-3 3 3M3 7l3 3 3-3" fill="none" stroke="currentColor" strokeWidth={1.4} />
        </svg>
      </button>
    </th>
  );
}

function ColumnPicker({
  side,
  value,
  onChange,
}: {
  side: Side;
  value: ColumnId[];
  onChange: (next: ColumnId[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  const available = COLUMNS.filter((c) => !c.sides || c.sides.includes(side));
  return (
    <div className={s.picker} ref={ref}>
      <button
        type="button"
        className={s.iconBtn}
        aria-label="Choose columns"
        aria-expanded={open}
        title="Columns"
        onClick={() => setOpen((o) => !o)}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.9}
          aria-hidden="true"
        >
          <path d="M5 4v16M12 4v16M19 4v16" strokeLinecap="round" />
        </svg>
      </button>
      {open ? (
        <div className={s.pickerMenu} role="menu">
          <p className={s.pickerTitle}>Columns</p>
          {available.map((column) => {
            const on = value.includes(column.id);
            return (
              <label key={column.id} className={s.pickerItem}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() =>
                    onChange(
                      on
                        ? value.filter((id) => id !== column.id)
                        : available
                            .filter((c) => c.id === column.id || value.includes(c.id))
                            .map((c) => c.id),
                    )
                  }
                />
                {column.label}
              </label>
            );
          })}
          <button
            type="button"
            className={s.linkBtn}
            onClick={() => onChange(defaultColumns(side))}
          >
            Reset
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function TitleTable({
  rows,
  side,
  facets,
  columns,
  onColumns,
  sort,
  onSort,
  selected,
  onToggle,
  onTogglePage,
  loading,
  onEdit,
}: {
  rows: TitleRow[];
  side: Side;
  facets: Facets | null;
  columns: ColumnId[];
  onColumns: (next: ColumnId[]) => void;
  sort: Sort;
  onSort: (by: string) => void;
  selected: Set<number>;
  onToggle: (id: number, on?: boolean) => void;
  onTogglePage: (on: boolean) => void;
  loading: boolean;
  onEdit: (row: TitleRow) => void;
}) {
  const shown = COLUMNS.filter(
    (c) => columns.includes(c.id) && (!c.sides || c.sides.includes(side)),
  );
  const allPicked = rows.length > 0 && rows.every((r) => selected.has(r.id));
  return (
    <div className={clsx(s.tableWrap, loading && s.dim)}>
      <table className={s.table}>
        <thead>
          <tr>
            <th className={s.checkCell}>
              <Check checked={allPicked} label="Select this page" onChange={onTogglePage} />
            </th>
            <SortHead label="Title" sortKey="title" sort={sort} onSort={onSort} />
            {shown.map((c) => (
              <SortHead
                key={c.id}
                label={c.label}
                sortKey={c.sort}
                sort={sort}
                onSort={onSort}
                align={c.align}
              />
            ))}
            <th className={s.endCell}>
              <ColumnPicker side={side} value={columns} onChange={onColumns} />
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const picked = selected.has(row.id);
            return (
              <tr key={row.id} className={clsx(picked && s.rowPicked, !row.owned && s.rowWanted)}>
                <td className={s.checkCell}>
                  <Check
                    checked={picked}
                    label={`Select ${row.title}`}
                    onChange={(on) => onToggle(row.id, on)}
                  />
                </td>
                <td className={s.titleCell}>
                  <a
                    className={s.titleLink}
                    href={`/video-detail/library/${row.kind}/${row.id}`}
                    onClick={(e) => {
                      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)
                        return;
                      e.preventDefault();
                      openTitle(row.kind, row.id);
                    }}
                  >
                    <Poster row={row} small />
                    <span className={s.titleText}>
                      <span className={s.titleName}>{row.title}</span>
                      <Flags row={row} />
                    </span>
                  </a>
                </td>
                {shown.map((c) => (
                  <td key={c.id} className={clsx(c.align === 'right' && s.right)}>
                    {c.render(row, facets)}
                  </td>
                ))}
                <td className={s.endCell}>
                  <button
                    type="button"
                    className={s.iconBtn}
                    aria-label={`Edit ${row.title}`}
                    title="Edit"
                    onClick={() => onEdit(row)}
                  >
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={1.9}
                      aria-hidden="true"
                    >
                      <path
                        d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ── the table of files ───────────────────────────────────────────────────── */

export function FilesTable({
  rows,
  side,
  sort,
  onSort,
  selected,
  onToggle,
  onTogglePage,
  loading,
}: {
  rows: FileRow[];
  side: Side;
  sort: Sort;
  onSort: (by: string) => void;
  selected: Set<number>;
  onToggle: (id: number, on?: boolean) => void;
  onTogglePage: (on: boolean) => void;
  loading: boolean;
}) {
  const allPicked = rows.length > 0 && rows.every((r) => selected.has(r.id));
  return (
    <div className={clsx(s.tableWrap, loading && s.dim)}>
      <table className={clsx(s.table, s.filesTable)}>
        <thead>
          <tr>
            <th className={s.checkCell}>
              <Check checked={allPicked} label="Select this page" onChange={onTogglePage} />
            </th>
            <SortHead
              label={side === 'shows' ? 'Episode' : 'Movie'}
              sortKey="title"
              sort={sort}
              onSort={onSort}
            />
            <SortHead label="File" sortKey="path" sort={sort} onSort={onSort} />
            <SortHead label="Quality" sortKey="resolution" sort={sort} onSort={onSort} />
            <SortHead label="Video" sortKey="codec" sort={sort} onSort={onSort} />
            <th>Audio</th>
            <th>Subtitles</th>
            <SortHead label="Size" sortKey="size" sort={sort} onSort={onSort} align="right" />
            <SortHead label="Added" sortKey="added" sort={sort} onSort={onSort} />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const picked = selected.has(row.id);
            const episode =
              row.season_number != null && row.episode_number != null
                ? `S${String(row.season_number).padStart(2, '0')}E${String(row.episode_number).padStart(2, '0')}`
                : '';
            return (
              <tr key={row.id} className={clsx(picked && s.rowPicked)}>
                <td className={s.checkCell}>
                  <Check
                    checked={picked}
                    label={`Select ${row.filename}`}
                    onChange={(on) => onToggle(row.id, on)}
                  />
                </td>
                <td className={s.fileTitle}>
                  <a
                    className={s.fileTitleLink}
                    href={`/video-detail/library/${row.kind}/${row.title_id}`}
                    onClick={(e) => {
                      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)
                        return;
                      e.preventDefault();
                      openTitle(row.kind, row.title_id);
                    }}
                  >
                    <span className={s.titleName}>{row.title}</span>
                    <span className={s.muted}>
                      {episode
                        ? [episode, row.episode_title].filter(Boolean).join(' · ')
                        : row.year}
                    </span>
                  </a>
                </td>
                <td className={s.filePath} title={row.path}>
                  <span className={s.fileName}>{row.filename}</span>
                  <span className={s.muted}>
                    {shortPath(row.path.slice(0, -row.filename.length), 2)}
                  </span>
                </td>
                <td>
                  <ResBadge label={row.resolution} />
                  {row.dynamic_range ? <span className={s.hdr}>{row.dynamic_range}</span> : null}
                </td>
                <td>
                  {[codecName(row.video_codec), row.release_source].filter(Boolean).join(' · ')}
                </td>
                <td>
                  {row.probed ? (
                    [
                      languageCodes(row.audio_languages),
                      codecName(row.audio_codec),
                      row.atmos ? 'Atmos' : channelsLabel(row.audio_channels),
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  ) : (
                    <span className={s.muted}>Not read yet</span>
                  )}
                </td>
                <td>
                  {row.subtitle_languages.length ? (
                    languageCodes(row.subtitle_languages, 4)
                  ) : row.probed ? (
                    <span className={s.muted}>None</span>
                  ) : (
                    ''
                  )}
                </td>
                <td className={s.right}>{formatSize(row.size_bytes)}</td>
                <td>{formatDate(row.added_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
