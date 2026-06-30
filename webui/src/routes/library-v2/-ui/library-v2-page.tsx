import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate as useRouterNavigate } from '@tanstack/react-router';
import { type ReactNode, useState } from 'react';

import { useReactPageShell } from '@/platform/shell/route-controllers';

import {
  autoGrabBest,
  fetchLibraryV2ImportStatus,
  LIBRARY_V2_QUERY_KEY,
  libraryV2AlbumQueryOptions,
  libraryV2ArtistQueryOptions,
  libraryV2ArtistsQueryOptions,
  libraryV2EnabledQueryOptions,
  refreshLibraryV2,
  setLibraryV2Monitored,
  startLibraryV2Import,
} from '../-library-v2.api';
import type {
  LibraryV2AlbumSummary,
  LibraryV2ArtistSummary,
  LibraryV2Track,
} from '../-library-v2.types';
import { Route } from '../route';
import { InteractiveSearchModal } from './interactive-search';
import { QualityProfileModal } from './quality-profile-modal';
import styles from './library-v2-page.module.css';

interface QpTarget {
  entity: 'artists' | 'albums';
  id: number;
  currentProfileId: number;
  title: string;
}

function trackProgress(present: number, total: number): string {
  return `${present}/${total}`;
}

/** Only "Interactive Search" opens the manual results window. */
const INTERACTIVE_RE = /^Interactive Search\b/;
/** "Search" / "Search Monitored" / "Grab Release" auto-search + grab the best. */
const AUTO_GRAB_RE = /^(Search Monitored|Search|Grab Release)\b/;

/** Build a source-search query from an artist name + an action label like
 *  "Interactive Search: Title (Album)". */
function buildSearchQuery(artistName: string, action: string): string {
  const idx = action.indexOf(': ');
  if (idx === -1) return artistName; // artist-level search
  const rest = action
    .slice(idx + 2)
    .replace(/\s*\([^)]*\)\s*$/, '') // drop trailing "(album)" context
    .replace(/\s*-\s*missing\s*$/i, '')
    .trim();
  return `${artistName} ${rest}`.trim();
}

/** Real quality string: format + bitrate (+ bit-depth/sample-rate when scanned). */
function qualityText(file: LibraryV2Track['file']): string {
  if (!file) return '-';
  const fmt = (file.format ?? '').toUpperCase();
  const kbps = file.bitrate ? (file.bitrate > 5000 ? Math.round(file.bitrate / 1000) : file.bitrate) : null;
  return [fmt, kbps ? `${kbps} kbps` : null].filter(Boolean).join(' / ') || '-';
}

const BOOKMARK_PATH = 'M5 3.5A1.5 1.5 0 0 1 6.5 2h11A1.5 1.5 0 0 1 19 3.5V22l-7-4.2L5 22V3.5z';

const ICON_PATHS = {
  back: 'M15 18l-6-6 6-6M9 12h12',
  refresh: 'M21 12a9 9 0 0 1-15.3 6.4M3 12A9 9 0 0 1 18.3 5.6M18 3v5h-5M6 21v-5h5',
  search: 'M11 19a8 8 0 1 1 5.7-2.3L21 21',
  interactive: 'M4 5h16M4 12h10M4 19h7M17 14l4 4-4 4',
  organize: 'M4 7h16M7 7v12M17 7v12M4 19h16',
  retag: 'M20 10l-8.5 8.5a2 2 0 0 1-2.8 0L4 13.8V4h9.8L20 10zM8 8h.01',
  tracks: 'M9 18V5l10-2v13M9 18a3 3 0 1 1-2-2.8M19 16a3 3 0 1 1-2-2.8',
  history: 'M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 7v5l3 2',
  import: 'M12 3v12M8 11l4 4 4-4M4 21h16',
  monitor: BOOKMARK_PATH,
  edit: 'M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4zM13 7l4 4',
  delete: 'M4 7h16M9 7V4h6v3M8 7l1 13h6l1-13',
  expand: 'M8 3H3v5M16 3h5v5M8 21H3v-5M21 16v5h-5',
  collapse: 'M9 3v6H3M15 3v6h6M9 21v-6H3M15 21v-6h6',
  download: 'M12 3v12M8 11l4 4 4-4M5 21h14',
  profile: 'M5 6h14M5 12h14M5 18h9',
  folder: 'M3 6h7l2 2h9v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z',
  close: 'M6 6l12 12M18 6L6 18',
} as const;

type IconName = keyof typeof ICON_PATHS;

function SvgIcon({ name, filled }: { name: IconName; filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d={ICON_PATHS[name]}
        fill={filled ? 'currentColor' : 'none'}
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function detailedQualityText(file: LibraryV2Track['file']): string {
  if (!file) return qualityText(file);
  const fmt = (file.format ?? '').toUpperCase();
  const kbps = file.bitrate ? (file.bitrate > 5000 ? Math.round(file.bitrate / 1000) : file.bitrate) : null;
  const bitDepth = file.bit_depth ? `${file.bit_depth}-bit` : null;
  const sampleRate = file.sample_rate
    ? `${Number((file.sample_rate / 1000).toFixed(file.sample_rate % 1000 === 0 ? 0 : 1))} kHz`
    : null;
  const resolution = [bitDepth, sampleRate].filter(Boolean).join('/');
  return [fmt, resolution || null, kbps ? `${kbps} kbps` : null].filter(Boolean).join(' / ') || qualityText(file);
}

// --- shared building blocks --------------------------------------------------

function useNavigate() {
  return useRouterNavigate({ from: Route.fullPath });
}

/** Cover/poster image with a graceful placeholder when no artwork resolves.
 *  ``thumb`` requests the small resized variant for fast list rendering. */
function Artwork({
  src,
  alt,
  className,
  thumb,
}: {
  src: string;
  alt: string;
  className: string;
  thumb?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const url = src ? (thumb ? `${src}?size=thumb` : src) : '';
  if (!url || failed) {
    return (
      <div className={`${className} ${styles.artPlaceholder}`} aria-label={alt}>
        ♪
      </div>
    );
  }
  return (
    <img className={className} src={url} alt={alt} loading="lazy" onError={() => setFailed(true)} />
  );
}

function useMonitorMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (v: { entity: 'artists' | 'albums' | 'tracks'; id: number; monitored: boolean }) =>
      setLibraryV2Monitored(v.entity, v.id, v.monitored),
    onSettled: () => queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY }),
  });
}

/** Lidarr-style monitor toggle (filled bookmark = monitored). */
function MonitorToggle({
  entity,
  id,
  monitored,
}: {
  entity: 'artists' | 'albums' | 'tracks';
  id: number;
  monitored: boolean;
}) {
  const mutation = useMonitorMutation();
  return (
    <button
      type="button"
      className={`${styles.monitorBtn} ${monitored ? styles.monitorOn : ''}`}
      title={monitored ? 'Monitored — click to stop' : 'Not monitored — click to monitor'}
      disabled={mutation.isPending}
      onClick={(e) => {
        e.stopPropagation();
        mutation.mutate({ entity, id, monitored: !monitored });
      }}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d={BOOKMARK_PATH} strokeLinejoin="round" />
      </svg>
    </button>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  title,
  busy,
  disabled,
  tone = 'default',
}: {
  icon: IconName;
  label: ReactNode;
  onClick: () => void;
  title?: string;
  busy?: boolean;
  disabled?: boolean;
  tone?: 'default' | 'danger';
}) {
  return (
    <button
      type="button"
      className={`${styles.toolButton} ${tone === 'danger' ? styles.toolDanger : ''}`}
      disabled={busy || disabled}
      title={title}
      onClick={onClick}
    >
      <SvgIcon name={busy ? 'refresh' : icon} />
      <span>{label}</span>
    </button>
  );
}

function IconActionButton({
  icon,
  title,
  onClick,
  disabled,
  tone = 'default',
}: {
  icon: IconName;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: 'default' | 'danger';
}) {
  return (
    <button
      type="button"
      className={`${styles.iconAction} ${tone === 'danger' ? styles.toolDanger : ''}`}
      title={title}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <SvgIcon name={icon} />
    </button>
  );
}

function PlaceholderModal({ action, onClose }: { action: string | null; onClose: () => void }) {
  if (!action) return null;
  return (
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div className={styles.modal} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <h3>{action}</h3>
          <IconActionButton icon="close" title="Close" onClick={onClose} />
        </div>
        <p>This control is now placed in the Library UI. The backend action can be wired next.</p>
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnPrimary} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// --- page root ---------------------------------------------------------------

export function LibraryV2Page() {
  useReactPageShell('library-v2');
  const search = Route.useSearch();
  const enabledQuery = useQuery(libraryV2EnabledQueryOptions());

  if (enabledQuery.data === false) {
    return (
      <div className={styles.page}>
        <div className={styles.emptyState}>
          <h2>Library v2 is disabled</h2>
          <p>
            Enable <code>features.library_v2</code> in Settings to try the experimental library
            manager.
          </p>
        </div>
      </div>
    );
  }

  if (search.artist) return <ArtistDetailView artistId={search.artist} />;
  return <ArtistIndexView />;
}

// --- artist overview ---------------------------------------------------------

function ArtistIndexView() {
  const search = Route.useSearch();
  const navigate = useNavigate();
  const artistsQuery = useQuery(
    libraryV2ArtistsQueryOptions({
      q: search.q,
      sort: search.sort,
      page: search.page,
      monitored: search.monitored,
    }),
  );

  const artists = artistsQuery.data?.artists ?? [];
  const pagination = artistsQuery.data?.pagination;
  const isEmpty = !artistsQuery.isLoading && artists.length === 0 && !search.q;

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Library</h1>
          <p className={styles.subtitle}>
            {pagination ? `${pagination.total_count} artists` : 'Experimental library manager'}
          </p>
        </div>
        <div className={styles.headerActions}>
          <ImportButton hasArtists={artists.length > 0} />
        </div>
      </header>

      <div className={styles.toolbar}>
        <input
          className={styles.searchInput}
          type="text"
          placeholder="Filter artists…"
          defaultValue={search.q}
          onChange={(e) => {
            const value = e.target.value;
            void navigate({ search: (prev) => ({ ...prev, q: value, page: 1 }) });
          }}
        />
        <select
          className={styles.select}
          value={search.monitored}
          onChange={(e) =>
            void navigate({
              search: (p) => ({ ...p, monitored: e.target.value as typeof p.monitored, page: 1 }),
            })
          }
        >
          <option value="all">All</option>
          <option value="monitored">Monitored</option>
          <option value="unmonitored">Unmonitored</option>
        </select>
        <select
          className={styles.select}
          value={search.sort}
          onChange={(e) =>
            void navigate({
              search: (p) => ({ ...p, sort: e.target.value as typeof p.sort, page: 1 }),
            })
          }
        >
          <option value="name">Name</option>
          <option value="added">Recently added</option>
          <option value="albums">Album count</option>
          <option value="tracks">Track count</option>
        </select>
        <div className={styles.viewToggle}>
          <button
            type="button"
            className={search.view === 'cards' ? styles.viewActive : ''}
            onClick={() => void navigate({ search: (p) => ({ ...p, view: 'cards' }) })}
          >
            Cards
          </button>
          <button
            type="button"
            className={search.view === 'table' ? styles.viewActive : ''}
            onClick={() => void navigate({ search: (p) => ({ ...p, view: 'table' }) })}
          >
            Table
          </button>
        </div>
      </div>

      {artistsQuery.isLoading ? (
        <div className={styles.loading}>Loading…</div>
      ) : isEmpty ? (
        <div className={styles.emptyState}>
          <h2>Your v2 library is empty</h2>
          <p>Import your existing library to populate the new manager.</p>
          <ImportButton hasArtists={false} prominent />
        </div>
      ) : search.view === 'table' ? (
        <ArtistTable artists={artists} />
      ) : (
        <ArtistCards artists={artists} />
      )}

      {pagination && pagination.total_pages > 1 ? (
        <div className={styles.pagination}>
          <button
            type="button"
            disabled={!pagination.has_prev}
            onClick={() => void navigate({ search: (p) => ({ ...p, page: p.page - 1 }) })}
          >
            ←
          </button>
          <span>
            Page {pagination.page} of {pagination.total_pages}
          </span>
          <button
            type="button"
            disabled={!pagination.has_next}
            onClick={() => void navigate({ search: (p) => ({ ...p, page: p.page + 1 }) })}
          >
            →
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ArtistCards({ artists }: { artists: LibraryV2ArtistSummary[] }) {
  const navigate = useNavigate();
  return (
    <div className={styles.cardGrid}>
      {artists.map((artist) => (
        <button
          key={artist.id}
          type="button"
          className={styles.artistCard}
          onClick={() => void navigate({ search: (p) => ({ ...p, artist: artist.id }) })}
        >
          <div className={styles.artistThumbWrap}>
            <Artwork src={artist.image_url ?? ''} alt={artist.name} className={styles.artistThumb} thumb />
            <span className={styles.cardMonitor}>
              <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
            </span>
          </div>
          <div className={styles.artistInfo}>
            <span className={styles.artistName}>{artist.name}</span>
            <span className={styles.artistMeta}>
              {artist.album_count} albums · {artist.single_count} singles
            </span>
            <span className={styles.artistMeta}>
              {trackProgress(artist.tracks_present, artist.track_count)} tracks
              {artist.tracks_missing > 0 ? (
                <span className={styles.missingBadge}>{artist.tracks_missing} missing</span>
              ) : null}
            </span>
          </div>
        </button>
      ))}
    </div>
  );
}

function ArtistTable({ artists }: { artists: LibraryV2ArtistSummary[] }) {
  const navigate = useNavigate();
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th className={styles.colMonitor}>Mon.</th>
          <th>Artist</th>
          <th className={styles.colNum}>Albums</th>
          <th className={styles.colNum}>Singles</th>
          <th className={styles.colNum}>Tracks</th>
          <th className={styles.colNum}>Missing</th>
        </tr>
      </thead>
      <tbody>
        {artists.map((artist) => (
          <tr
            key={artist.id}
            className={styles.tableRow}
            onClick={() => void navigate({ search: (p) => ({ ...p, artist: artist.id }) })}
          >
            <td>
              <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
            </td>
            <td>
              <span className={styles.cellArtist}>
                <Artwork src={artist.image_url ?? ''} alt={artist.name} className={styles.rowThumb} thumb />
                <span>{artist.name}</span>
              </span>
            </td>
            <td className={styles.colNum}>{artist.album_count}</td>
            <td className={styles.colNum}>{artist.single_count}</td>
            <td className={styles.colNum}>
              {trackProgress(artist.tracks_present, artist.track_count)}
            </td>
            <td className={styles.colNum}>{artist.tracks_missing > 0 ? artist.tracks_missing : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- artist detail (Lidarr-style: expandable album/single tables) ------------

function ArtistDetailView({ artistId }: { artistId: number }) {
  const navigate = useNavigate();
  const artistQuery = useQuery(libraryV2ArtistQueryOptions(artistId));
  const artist = artistQuery.data;
  const [refreshing, setRefreshing] = useState(false);
  const [modalAction, setModalAction] = useState<string | null>(null);
  const [grabBanner, setGrabBanner] = useState<{ tone: 'busy' | 'ok' | 'err'; text: string } | null>(
    null,
  );
  const [qpTarget, setQpTarget] = useState<QpTarget | null>(null);
  const queryClient = useQueryClient();
  const artistName = artist?.name ?? '';

  async function refresh() {
    setRefreshing(true);
    try {
      await refreshLibraryV2('artists', artistId);
      await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
    } finally {
      setRefreshing(false);
    }
  }

  /** Route a toolbar/row action: Interactive Search opens the window; plain
   *  Search / Grab auto-searches and downloads the best result; the rest are
   *  not-yet-wired placeholders. */
  function handleAction(action: string) {
    if (action === 'Quality Profile' && artist) {
      setQpTarget({
        entity: 'artists',
        id: artistId,
        currentProfileId: artist.quality_profile?.id ?? 1,
        title: artist.name,
      });
      return;
    }
    if (INTERACTIVE_RE.test(action)) {
      setModalAction(action);
      return;
    }
    if (AUTO_GRAB_RE.test(action)) {
      const query = buildSearchQuery(artistName, action);
      setGrabBanner({ tone: 'busy', text: `Searching "${query}"…` });
      void autoGrabBest(query)
        .then((best) => {
          if (!best) {
            setGrabBanner({ tone: 'err', text: `No results for "${query}".` });
          } else {
            const t = best.result_type === 'album' ? best.album_title : best.title;
            setGrabBanner({ tone: 'ok', text: `Grabbing "${t}" from ${best.username}.` });
          }
        })
        .catch((e) => setGrabBanner({ tone: 'err', text: e instanceof Error ? e.message : 'Search failed' }));
      return;
    }
    setModalAction(action);
  }

  return (
    <div className={styles.page}>
      <BackLink onClick={() => void navigate({ search: (p) => ({ ...p, artist: undefined }) })}>
        ← All artists
      </BackLink>
      {artistQuery.isLoading || !artist ? (
        <div className={styles.loading}>Loading…</div>
      ) : (
        <>
          <div className={styles.pageToolbar}>
            <div className={styles.toolbarGroup}>
              <ActionButton
                icon="refresh"
                label={refreshing ? 'Refreshing...' : 'Refresh & Scan'}
                title="Refresh information and scan disk"
                busy={refreshing}
                onClick={() => void refresh()}
              />
              <ActionButton
                icon="search"
                label="Search Monitored"
                title="Search monitored missing tracks"
                disabled={!artist.monitored || (artist.albums.length === 0 && artist.singles.length === 0)}
                onClick={() => handleAction('Search Monitored')}
              />
              <ActionButton
                icon="interactive"
                label="Interactive Search"
                title="Search all SoulSync sources manually"
                disabled={!artist.monitored || (artist.albums.length === 0 && artist.singles.length === 0)}
                onClick={() => handleAction('Interactive Search')}
              />
            </div>
            <div className={styles.toolbarGroup}>
              <ActionButton icon="organize" label="Preview Rename" onClick={() => handleAction('Preview Rename')} />
              <ActionButton icon="retag" label="Preview Retag" onClick={() => handleAction('Preview Retag')} />
              <ActionButton icon="tracks" label="Manage Tracks" onClick={() => handleAction('Manage Tracks')} />
              <ActionButton icon="history" label="History" onClick={() => handleAction('History')} />
              <ActionButton icon="import" label="Manual Import" onClick={() => handleAction('Manual Import')} />
            </div>
            <div className={styles.toolbarGroup}>
              <ActionButton icon="monitor" label="Monitoring" onClick={() => handleAction('Artist Monitoring')} />
              <ActionButton icon="profile" label="Quality Profile" onClick={() => handleAction('Quality Profile')} />
              <ActionButton icon="edit" label="Edit" onClick={() => handleAction('Edit Artist')} />
              <ActionButton icon="delete" label="Delete" tone="danger" onClick={() => handleAction('Delete Artist')} />
            </div>
          </div>

          {grabBanner ? (
            <div className={`${styles.grabBanner} ${styles[`grab_${grabBanner.tone}`]}`}>
              <span>{grabBanner.text}</span>
              <button type="button" className={styles.grabBannerClose} onClick={() => setGrabBanner(null)}>
                ✕
              </button>
            </div>
          ) : null}

          <header className={styles.detailHeader}>
            <Artwork src={artist.image_url ?? ''} alt={artist.name} className={styles.detailThumb} />
            <div className={styles.detailMeta}>
              <div className={styles.detailTitleRow}>
                <MonitorToggle entity="artists" id={artist.id} monitored={artist.monitored} />
                <h1 className={styles.title}>{artist.name}</h1>
              </div>
              <p className={styles.subtitle}>
                {artist.album_count} albums · {artist.single_count} singles
                {artist.monitored ? ' · Monitored (watchlist)' : ''}
              </p>
              <div className={styles.detailLabels}>
                <span className={styles.detailLabel}>
                  <SvgIcon name="profile" />
                  {artist.quality_profile?.name ?? 'No quality profile'}
                </span>
                <span className={styles.detailLabel}>
                  <SvgIcon name={artist.monitored ? 'monitor' : 'close'} />
                  {artist.monitored ? 'Monitored' : 'Unmonitored'}
                </span>
                <span className={styles.detailLabel}>
                  <SvgIcon name="tracks" />
                  {artist.albums.length + artist.singles.length} releases
                </span>
              </div>
              {artist.genres.length > 0 ? (
                <p className={styles.genres}>{artist.genres.join(', ')}</p>
              ) : null}
            </div>
          </header>

          <AlbumGroup
            title="Albums"
            albums={artist.albums}
            onAction={handleAction}
            onQualityProfile={setQpTarget}
          />
          <AlbumGroup
            title="Singles"
            albums={artist.singles}
            onAction={handleAction}
            onQualityProfile={setQpTarget}
          />
          {modalAction && INTERACTIVE_RE.test(modalAction) ? (
            <InteractiveSearchModal
              initialQuery={buildSearchQuery(artist.name, modalAction)}
              onClose={() => setModalAction(null)}
            />
          ) : (
            <PlaceholderModal action={modalAction} onClose={() => setModalAction(null)} />
          )}
          {qpTarget ? (
            <QualityProfileModal
              entity={qpTarget.entity}
              id={qpTarget.id}
              currentProfileId={qpTarget.currentProfileId}
              title={qpTarget.title}
              onClose={() => setQpTarget(null)}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

/** Lidarr-style album list: each album is a block whose header expands to reveal
 *  its track table — contained in the block (no fragile nested-table colspans). */
function AlbumGroup({
  title,
  albums,
  onAction,
  onQualityProfile,
}: {
  title: string;
  albums: LibraryV2AlbumSummary[];
  onAction: (action: string) => void;
  onQualityProfile: (target: QpTarget) => void;
}) {
  if (albums.length === 0) return null;
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>
        {title} <span className={styles.sectionCount}>{albums.length}</span>
      </h2>
      <div className={styles.albumList}>
        {albums.map((album) => (
          <AlbumBlock
            key={album.id}
            album={album}
            onAction={onAction}
            onQualityProfile={onQualityProfile}
          />
        ))}
      </div>
    </section>
  );
}

function AlbumBlock({
  album,
  onAction,
  onQualityProfile,
}: {
  album: LibraryV2AlbumSummary;
  onAction: (action: string) => void;
  onQualityProfile: (target: QpTarget) => void;
}) {
  const [open, setOpen] = useState(false);
  const complete = album.tracks_missing === 0 && album.track_count > 0;
  const pct = album.track_count ? Math.round((100 * album.tracks_present) / album.track_count) : 0;
  return (
    <div className={`${styles.albumBlock} ${open ? styles.albumBlockOpen : ''}`}>
      <div className={styles.albumHead} onClick={() => setOpen(!open)}>
        <span className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`}>›</span>
        <MonitorToggle entity="albums" id={album.id} monitored={album.monitored} />
        <Artwork src={album.image_url ?? ''} alt={album.title} className={styles.albumHeadThumb} thumb />
        <div className={styles.albumHeadMeta}>
          <span className={styles.albumHeadTitle}>{album.title}</span>
          <span className={styles.albumHeadSub}>
            {[album.album_type, album.release_date || (album.year ? String(album.year) : null)]
              .filter(Boolean)
              .join(' · ')}
          </span>
        </div>
        <div className={styles.albumProgress}>
          <div className={styles.progressBar}>
            <div
              className={styles.progressFill}
              data-complete={complete ? 'true' : 'false'}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className={styles.progressLabel}>
            {trackProgress(album.tracks_present, album.track_count)}
          </span>
        </div>
        <span className={complete ? styles.statusOk : styles.statusWarn}>
          {complete ? 'complete' : `${album.tracks_missing} missing`}
        </span>
        <span className={styles.albumActions}>
          <IconActionButton icon="search" title="Search Monitored" onClick={() => onAction(`Search Monitored: ${album.title}`)} />
          <IconActionButton icon="interactive" title="Interactive Search" onClick={() => onAction(`Interactive Search: ${album.title}`)} />
          <IconActionButton
            icon="profile"
            title="Quality Profile"
            onClick={() =>
              onQualityProfile({
                entity: 'albums',
                id: album.id,
                currentProfileId: album.quality_profile_id,
                title: album.title,
              })
            }
          />
          <IconActionButton icon="edit" title="Edit Album" onClick={() => onAction(`Edit Album: ${album.title}`)} />
        </span>
      </div>
      {open ? <AlbumTrackTable albumId={album.id} onAction={onAction} /> : null}
    </div>
  );
}

function AlbumTrackTable({
  albumId,
  onAction,
}: {
  albumId: number;
  onAction: (action: string) => void;
}) {
  const albumQuery = useQuery(libraryV2AlbumQueryOptions(albumId));
  const album = albumQuery.data;
  if (albumQuery.isLoading || !album) {
    return <div className={styles.inlineLoading}>Loading tracks…</div>;
  }
  return (
    <div className={styles.trackTableWrap}>
      <table className={styles.trackTable}>
        <thead>
          <tr>
            <th className={styles.colMonitor}></th>
            <th className={styles.colNum}>#</th>
            <th>Title</th>
            <th>Artists</th>
            <th>Quality</th>
            <th>File</th>
            <th>Metadata</th>
            <th className={styles.colActions}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {album.tracks.map((track, i) => (
            <TrackRow key={track.id ?? `missing-${i}`} track={track} albumTitle={album.title} onAction={onAction} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TrackRow({
  track,
  albumTitle,
  onAction,
}: {
  track: LibraryV2Track;
  albumTitle: string;
  onAction: (action: string) => void;
}) {
  const missing = track.file_status === 'missing';
  const label = track.title ?? `Track ${track.track_number ?? '?'} - missing`;
  return (
    <tr className={missing ? styles.missingRow : styles.staticRow}>
      <td>
        {track.id ? (
          <MonitorToggle entity="tracks" id={track.id} monitored={track.monitored} />
        ) : null}
      </td>
      <td className={styles.colNum}>{track.track_number ?? '—'}</td>
      <td>{track.title ?? <span className={styles.muted}>{label}</span>}</td>
      <td>{track.artists.map((a) => a.name).join(', ')}</td>
      <td className={styles.qualityText}>
        {detailedQualityText(track.file)}
        {track.file && track.meets_profile === false ? (
          <span className={styles.qBelow} title="Below the album's quality profile">
            below profile
          </span>
        ) : track.file && track.upgrade_candidate ? (
          <span className={styles.qUpgrade} title="A higher-quality version may be available">
            upgrade ↑
          </span>
        ) : null}
      </td>
      <td>
        <FileStatusBadge status={track.file_status} />
      </td>
      <td>
        {track.id ? (
          track.metadata_gaps.length === 0 ? (
            <span className={styles.statusOk}>complete</span>
          ) : (
            <span className={styles.statusWarn} title={track.metadata_gaps.join(', ')}>
              {track.metadata_gaps.length} missing
            </span>
          )
        ) : (
          <span className={styles.muted}>—</span>
        )}
      </td>
      <td className={styles.trackActions}>
        <IconActionButton
          icon="search"
          title="Search"
          disabled={!track.id}
          onClick={() => onAction(`Search: ${label} (${albumTitle})`)}
        />
        <IconActionButton
          icon="interactive"
          title="Interactive Search"
          disabled={!track.id}
          onClick={() => onAction(`Interactive Search: ${label} (${albumTitle})`)}
        />
        <IconActionButton
          icon="download"
          title="Grab / Download"
          disabled={!track.id}
          onClick={() => onAction(`Grab Release: ${label} (${albumTitle})`)}
        />
        <IconActionButton
          icon="retag"
          title="Preview Retag"
          disabled={!track.id || missing}
          onClick={() => onAction(`Preview Retag: ${label}`)}
        />
        <IconActionButton
          icon="tracks"
          title="Manage Track"
          disabled={!track.id}
          onClick={() => onAction(`Manage Track: ${label}`)}
        />
      </td>
    </tr>
  );
}

function FileStatusBadge({ status }: { status: LibraryV2Track['file_status'] }) {
  if (status === 'present') return <span className={styles.statusPresent}>present</span>;
  if (status === 'duplicate_single') return <span className={styles.statusDup}>also on album</span>;
  return <span className={styles.statusMissing}>missing</span>;
}

function BackLink({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" className={styles.backLink} onClick={onClick}>
      {children}
    </button>
  );
}

function ImportButton({ hasArtists, prominent }: { hasArtists: boolean; prominent?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function runImport() {
    setBusy(true);
    setMessage('Importing…');
    try {
      await startLibraryV2Import(false);
      for (let i = 0; i < 600; i += 1) {
        const state = await fetchLibraryV2ImportStatus();
        if (!state.running) {
          setMessage(state.error ? `Failed: ${state.error}` : 'Imported — refreshing…');
          if (!state.error) window.location.reload();
          break;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className={styles.importWrap}>
      <button
        type="button"
        className={prominent ? styles.btnPrimary : styles.btnGhost}
        disabled={busy}
        onClick={() => void runImport()}
      >
        {busy ? 'Importing…' : hasArtists ? 'Re-import library' : 'Import library'}
      </button>
      {message ? <span className={styles.importMsg}>{message}</span> : null}
    </span>
  );
}
