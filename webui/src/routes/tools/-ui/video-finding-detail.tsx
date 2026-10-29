/**
 * what an expanded video finding shows: posters, the episodes a show is
 * missing, every copy of a duplicate, the rename before and after.
 *
 * ported from video-repair.js (the vanilla video tools page) panel for panel,
 * same endpoints and the same wording, onto the shared .vrf-* styles. four
 * types (missing episodes, quality, broken files, metadata gaps) fetch the
 * live title on open, the same lazy load the vanilla did, so a collapsed row
 * never costs a request.
 *
 * drill-ins dispatch soulsync:video-open-detail, the one way the video side
 * opens a detail page, so Back works the same as from anywhere else.
 */

import { useEffect, useState } from 'react';

import type { RepairFinding } from '../-tools.types';

type Details = Record<string, any>;

const GAP_LABELS: Record<string, string> = {
  unmatched: 'not TMDB-matched',
  overview: 'no summary',
  genres: 'no genres',
  poster: 'no poster',
  backdrop: 'no backdrop',
};

const LAZY_TYPES = new Set(['missing_episodes', 'quality_upgrade', 'broken_file', 'metadata_gap']);

export function openVideoDetail(kind: 'movie' | 'show' | 'channel', id: number | string): void {
  document.dispatchEvent(
    new CustomEvent('soulsync:video-open-detail', {
      detail: {
        kind,
        id: kind === 'channel' ? id : Number.parseInt(String(id), 10),
        source: kind === 'channel' ? 'youtube' : 'library',
      },
    }),
  );
}

function tmdbImg(path: string | null | undefined): string {
  const p = String(path || '');
  if (!p) return '';
  if (p.startsWith('http') || p.startsWith('/api/')) return p;
  return `https://image.tmdb.org/t/p/w342${p}`;
}

function gb(bytes: unknown): string {
  return `${((Number(bytes) || 0) / 1073741824).toFixed(1)} GB`;
}

function episodeCode(
  season: number | string | null | undefined,
  episode: number | string | null | undefined,
): string {
  return `S${String(season ?? 0).padStart(2, '0')}E${String(episode ?? 0).padStart(2, '0')}`;
}

/** an image that hides itself instead of showing a broken icon. */
function Poster({
  src,
  className,
  fallback,
}: {
  src: string;
  className: string;
  fallback: string;
}) {
  const [broken, setBroken] = useState(false);
  if (!src || broken) {
    return <div className={`${className} vrf-poster--empty`}>{fallback}</div>;
  }
  return (
    <img className={className} src={src} alt="" loading="lazy" onError={() => setBroken(true)} />
  );
}

function ViewButton({ kind, id, label }: { kind: 'movie' | 'show'; id: unknown; label: string }) {
  if (id === null || id === undefined || id === '') return null;
  return (
    <button
      type="button"
      className="vrf-btn"
      onClick={() => openVideoDetail(kind, id as number | string)}
    >
      {label} →
    </button>
  );
}

function FileCard({ file }: { file: Details | null | undefined }) {
  if (!file || (!file.relative_path && !file.size_bytes)) return null;
  const bits = [
    file.resolution,
    file.video_codec,
    file.audio_codec,
    file.release_source,
    file.size_bytes ? gb(file.size_bytes) : null,
  ].filter(Boolean);
  return (
    <div className="vrf-file">
      <div className="vrf-file-head">{bits.join(' · ')}</div>
      {file.relative_path ? <div className="vrf-file-path">{file.relative_path}</div> : null}
    </div>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="repair-finding-hint">{children}</p>;
}

// ── per type ─────────────────────────────────────────────────────────────────

function CollectionDetail({ d }: { d: Details }) {
  const missing: Details[] = d.missing || [];
  const owned: Details[] = d.owned || [];
  return (
    <>
      <div className="vrf-chips">
        <span className="vrf-chip vrf-chip--miss">{d.count || missing.length} missing</span>
        <span className="vrf-chip">
          {owned.length} / {d.total || '?'} owned
        </span>
      </div>
      <div className="vrf-rail">
        {missing.map((m, index) => (
          <div className="vrf-mini vrf-mini--want" key={`m${index}`}>
            <Poster src={tmdbImg(m.poster_url)} className="vrf-mini-poster" fallback="🎬" />
            <span className="vrf-mini-chip">missing</span>
            <div className="vrf-mini-title" title={m.title || '?'}>
              {m.title || '?'}
            </div>
            {m.year ? <div className="vrf-mini-year">{m.year}</div> : null}
          </div>
        ))}
        {owned.map((m, index) => (
          <div className="vrf-mini vrf-mini--dim" key={`o${index}`}>
            <Poster
              src={m.library_id != null ? `/api/video/poster/movie/${m.library_id}` : ''}
              className="vrf-mini-poster"
              fallback="🎬"
            />
            <span className="vrf-mini-chip vrf-mini-chip--got">✓</span>
            <div className="vrf-mini-title" title={m.title || '?'}>
              {m.title || '?'}
            </div>
            {m.year ? <div className="vrf-mini-year">{m.year}</div> : null}
          </div>
        ))}
      </div>
      <Hint>
        Approving sends the missing films to the wishlist. The auto-downloader takes it from there.
      </Hint>
    </>
  );
}

function DuplicateDetail({ d }: { d: Details }) {
  if (d.kind === 'rows') {
    return (
      <>
        <div className="vrf-files">
          {(d.rows || []).map((row: Details) => (
            <div className="vrf-file" key={row.id}>
              <div className="vrf-file-head">
                Library entry #{row.id}
                {row.server_source ? ` · ${row.server_source}` : ''}
              </div>
              {row.path ? <div className="vrf-file-path">{row.path}</div> : null}
              <ViewButton kind="movie" id={row.id} label="View entry" />
            </div>
          ))}
        </div>
        <DuplicateHint />
      </>
    );
  }
  const episode = d.kind === 'episode_files';
  return (
    <>
      {episode ? (
        <div className="vrf-chips">
          <span className="vrf-chip">
            {d.show_title || 'Show'} {episodeCode(d.season_number, d.episode_number)}
          </span>
          {d.spans_drives ? <span className="vrf-chip vrf-chip--miss">on two drives</span> : null}
        </div>
      ) : d.spans_drives ? (
        <div className="vrf-chips">
          <span className="vrf-chip vrf-chip--miss">on two drives</span>
        </div>
      ) : null}
      <div className="vrf-files">
        {(d.files || []).map((file: Details, index: number) => (
          <div className="vrf-file" key={file.file_id ?? index}>
            <div className="vrf-file-head">
              {file.resolution || '?'}
              {file.video_codec ? ` · ${file.video_codec}` : ''} · {gb(file.size_bytes)}
            </div>
            {file.relative_path ? <div className="vrf-file-path">{file.relative_path}</div> : null}
          </div>
        ))}
        {episode ? (
          <ViewButton kind="show" id={d.show_id} label="View show" />
        ) : (
          <ViewButton kind="movie" id={d.movie_id} label="View movie" />
        )}
      </div>
      <DuplicateHint />
    </>
  );
}

function DuplicateHint() {
  return (
    <Hint>
      Report-only. Nothing is deleted from here. Dismiss the copies you keep on purpose; they
      won&rsquo;t come back.
    </Hint>
  );
}

function StaleWishlistDetail({ d }: { d: Details }) {
  return (
    <div className="vrf-show">
      <Poster src={tmdbImg(d.poster_url)} className="vrf-poster" fallback="🧹" />
      <div className="vrf-show-info">
        <div className="vrf-show-title">{d.title || '?'}</div>
        <div className="vrf-chips">
          <span className="vrf-chip vrf-chip--got">✓ already owned</span>
          <span className="vrf-chip">{d.kind || 'movie'}</span>
        </div>
        <p className="vrf-show-overview">
          The download engine never re-grabs owned items, so this wishlist row will sit forever.
          Approving removes the row only. Files are untouched.
        </p>
        <div className="vrf-actions">
          {d.kind === 'episode' ? (
            <ViewButton kind="show" id={d.library_id} label="View show" />
          ) : (
            <ViewButton kind="movie" id={d.library_id} label="View movie" />
          )}
        </div>
      </div>
    </div>
  );
}

function GhostDetail({
  finding,
  onFixAction,
}: {
  finding: RepairFinding;
  onFixAction?: (finding: RepairFinding, action: string) => void;
}) {
  const d: Details = finding.details || {};
  return (
    <div className="vrf-show">
      <Poster src={String(d.thumb_url || '')} className="vrf-poster" fallback="👻" />
      <div className="vrf-show-info">
        <div className="vrf-show-title">{d.title || '?'}</div>
        <div className="vrf-chips">
          <span className="vrf-chip vrf-chip--miss">file missing</span>
          {d.channel ? <span className="vrf-chip">{d.channel}</span> : null}
          {d.published_at ? (
            <span className="vrf-chip">{String(d.published_at).slice(0, 10)}</span>
          ) : null}
        </div>
        <p className="vrf-show-overview">
          SoulSync downloaded this video but the file is gone ({d.dest_path || 'no path recorded'}).
          Approving marks it deleted: the badge clears and it will NOT be downloaded again. Files
          are never touched.
        </p>
        <div className="vrf-actions">
          {finding.status === 'pending' && onFixAction ? (
            <button
              type="button"
              className="vrf-btn"
              title="Wipe the download memory so it can be downloaded again"
              onClick={() => onFixAction(finding, 'forget')}
            >
              Forget (allow re-download)
            </button>
          ) : null}
          {d.channel_id ? (
            <button
              type="button"
              className="vrf-btn"
              onClick={() => openVideoDetail('channel', String(d.channel_id))}
            >
              View channel →
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function MassMissingDetail({ d }: { d: Details }) {
  const sample: Details[] = d.sample || [];
  const more = (d.missing_count || 0) - sample.length;
  return (
    <>
      <div className="vrf-chips">
        <span className="vrf-chip vrf-chip--miss">
          {d.missing_count || 0} of {d.checked || 0} missing
        </span>
      </div>
      <p className="vrf-show-overview">
        This many files gone at once usually means a drive or share is unreachable, so nothing was
        flagged automatically. If it IS real (you deleted the files, or moved to a new drive),
        approve this: each video gets its own finding to review, where you choose Mark Deleted (no
        re-download) or Forget (download again). Nothing is marked by the approve itself, and files
        are never touched. If a drive is down, fix the mount and re-run the scan instead. This
        notice retires on its own.
      </p>
      <div className="vrf-eps">
        {sample.map((s, index) => (
          <div className="vrf-ep" key={index}>
            <div className="vrf-ep-body">
              <div className="vrf-ep-head">
                <span className="vrf-ep-title" title={s.dest_path || ''}>
                  {s.title || '?'}
                </span>
                {s.channel ? <span className="vrf-ep-date">{s.channel}</span> : null}
              </div>
            </div>
          </div>
        ))}
        {more > 0 ? (
          <div className="vrf-ep">
            <div className="vrf-ep-body">
              <div className="vrf-ep-head">
                <span className="vrf-ep-title">…and {more} more</span>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </>
  );
}

function WatchedDetail({ d }: { d: Details }) {
  return (
    <div className="vrf-show">
      <Poster
        src={d.movie_id != null ? `/api/video/poster/movie/${d.movie_id}` : ''}
        className="vrf-poster"
        fallback="🍿"
      />
      <div className="vrf-show-info">
        <div className="vrf-show-title">
          {d.title || '?'}
          {d.year ? ` (${d.year})` : ''}
        </div>
        <div className="vrf-chips">
          <span className={`vrf-chip${d.watched ? ' vrf-chip--got' : ''}`}>
            {d.watched
              ? `✓ watched ${d.play_count > 1 ? `${d.play_count}×` : 'once'}`
              : 'never watched'}
          </span>
          {d.last_viewed_at ? (
            <span className="vrf-chip">last: {String(d.last_viewed_at).slice(0, 10)}</span>
          ) : null}
          <span className="vrf-chip">{gb(d.size_bytes)}</span>
        </div>
        <p className="vrf-show-overview">
          Approving moves the file into the recycle bin (Settings → Library Organization sets where
          and for how long). Nothing is erased outright. The weekly deep scan tidies the server view
          afterwards.
        </p>
        {d.relative_path ? <p className="vrf-show-overview">{d.relative_path}</p> : null}
        <div className="vrf-actions">
          <ViewButton kind="movie" id={d.movie_id} label="View movie" />
        </div>
      </div>
    </div>
  );
}

function NamingDetail({ d }: { d: Details }) {
  return (
    <>
      <div className="vrf-chips">
        {d.scope ? <span className="vrf-chip">{d.scope}</span> : null}
        {d.size_bytes ? <span className="vrf-chip">{gb(d.size_bytes)}</span> : null}
      </div>
      <p className="vrf-show-overview">
        <strong>Now:</strong> {d.current_path || ''}
      </p>
      <p className="vrf-show-overview">
        <strong>Becomes:</strong> {d.expected_path || ''}
      </p>
      <p className="vrf-show-overview">
        Approving renames the file (sidecars and subtitles come along). The server picks the new
        name up on its next scan. Nothing is ever overwritten.
      </p>
    </>
  );
}

function subLabel(missing: Array<string | boolean | null>): string {
  return (
    (missing[0] ? String(missing[0]) : '?').toUpperCase() +
    (missing[1] ? ' HI' : '') +
    (missing[2] ? ' forced' : '')
  );
}

function SubtitlesDetail({ d }: { d: Details }) {
  const isShow = d.kind === 'show';
  const items: Details[] = d.items || [];
  const chips = (missing: Array<Array<string | boolean | null>> | undefined) =>
    (missing || []).map((m, index) => (
      <span className="vrf-chip vrf-chip--miss" key={index}>
        {subLabel(m)}
      </span>
    ));
  const shown = items.slice(0, 60);
  return (
    <>
      <div className="vrf-show">
        <Poster
          src={
            d.library_id != null
              ? `/api/video/poster/${isShow ? 'show' : 'movie'}/${d.library_id}`
              : ''
          }
          className="vrf-poster"
          fallback="💬"
        />
        <div className="vrf-show-info">
          <div className="vrf-show-title">
            {d.title || '?'} {d.year ? <span className="vrf-mini-year">{d.year}</span> : null}
          </div>
          {isShow ? null : <div className="vrf-chips">{chips(items[0]?.missing)}</div>}
          <p className="vrf-show-overview">
            No subtitle file next to the video and no embedded track in these languages. Approving
            queues them: the subtitle worker fetches them within your daily quota. Nothing is
            downloaded from here.
          </p>
          <div className="vrf-actions">
            <ViewButton
              kind={isShow ? 'show' : 'movie'}
              id={d.library_id}
              label={isShow ? 'View show' : 'View movie'}
            />
          </div>
        </div>
      </div>
      {isShow ? (
        <>
          <div className="repair-ep-list">
            {shown.map((item, index) => (
              <div className="repair-ep-row" key={index}>
                <span className="repair-ep-code">{item.label || ''}</span>
                <span className="repair-ep-title">{chips(item.missing)}</span>
              </div>
            ))}
          </div>
          {items.length > shown.length ? (
            <Hint>+{items.length - shown.length} more episodes</Hint>
          ) : null}
        </>
      ) : null}
    </>
  );
}

function SimpleEpisodeList({ d }: { d: Details }) {
  return (
    <>
      <div className="repair-ep-list">
        {(d.episodes || []).map((e: Details, index: number) => (
          <div className="repair-ep-row" key={index}>
            <span className="repair-ep-code">{episodeCode(e.season_number, e.episode_number)}</span>
            <span className="repair-ep-title">{e.title || '—'}</span>
            <span className="repair-ep-date">{e.air_date || ''}</span>
          </div>
        ))}
      </div>
      <Hint>Approving sends these to the wishlist. The auto-downloader takes it from there.</Hint>
    </>
  );
}

// ── the lazy, live-title panels ──────────────────────────────────────────────

function useLiveTitle(kind: 'movie' | 'show' | null, id: number | string | null | undefined) {
  const [state, setState] = useState<{ loading: boolean; data: Details | null }>({
    loading: Boolean(kind && id != null),
    data: null,
  });
  useEffect(() => {
    if (!kind || id === null || id === undefined) {
      setState({ loading: false, data: null });
      return;
    }
    let live = true;
    setState({ loading: true, data: null });
    fetch(`/api/video/detail/${kind}/${id}`, { headers: { Accept: 'application/json' } })
      .then((response) => (response.ok ? response.json() : null))
      .then((data: Details | null) => {
        if (live) setState({ loading: false, data: data && !data.error ? data : null });
      })
      .catch(() => {
        if (live) setState({ loading: false, data: null });
      });
    return () => {
      live = false;
    };
  }, [kind, id]);
  return state;
}

function RawDetails({ d }: { d: Details }) {
  return <pre className="repair-finding-json">{JSON.stringify(d, null, 2)}</pre>;
}

function MissingEpisodesDetail({ d }: { d: Details }) {
  const { loading, data: show } = useLiveTitle(d.show_id != null ? 'show' : null, d.show_id);
  if (loading) return <div className="repair-loading">Loading details…</div>;
  if (!show) return <SimpleEpisodeList d={d} />;

  const missing = new Set(
    (d.episodes || []).map((e: Details) => `${e.season_number}:${e.episode_number}`),
  );
  const sub = [
    show.network,
    show.status ? show.status.charAt(0).toUpperCase() + show.status.slice(1) : null,
    show.content_rating,
  ].filter(Boolean);
  const seasons = (show.seasons || [])
    .map((season: Details) => ({
      season,
      eps: (season.episodes || []).filter((e: Details) =>
        missing.has(`${season.season_number}:${e.episode_number}`),
      ),
    }))
    .filter((entry: { eps: Details[] }) => entry.eps.length > 0);

  return (
    <>
      <div className="vrf-show">
        <Poster
          src={show.has_poster ? `/api/video/poster/show/${show.id}` : ''}
          className="vrf-poster"
          fallback="📺"
        />
        <div className="vrf-show-info">
          <div className="vrf-show-title">
            {show.title}
            {show.year ? <span className="vrf-show-year"> ({show.year})</span> : null}
          </div>
          {sub.length ? <div className="vrf-show-sub">{sub.join(' · ')}</div> : null}
          <div className="vrf-chips">
            <span className="vrf-chip vrf-chip--miss">
              {d.count || (d.episodes || []).length} missing
            </span>
            {show.episode_total ? (
              <span className="vrf-chip">
                {show.episode_owned || 0} / {show.episode_total} owned
              </span>
            ) : null}
            {show.first_air_date ? (
              <span className="vrf-chip">
                {String(show.first_air_date).slice(0, 4)}
                {show.last_air_date ? `–${String(show.last_air_date).slice(0, 4)}` : ''}
              </span>
            ) : null}
          </div>
          {show.overview ? <p className="vrf-show-overview">{show.overview}</p> : null}
          <div className="vrf-actions">
            <ViewButton kind="show" id={show.id} label="View show" />
          </div>
        </div>
      </div>
      {seasons.length ? (
        seasons.map(({ season, eps }: { season: Details; eps: Details[] }) => (
          <div className="vrf-season" key={season.season_number}>
            <div className="vrf-season-head">
              <Poster
                src={
                  season.has_poster && season.id != null
                    ? `/api/video/poster/season/${season.id}`
                    : ''
                }
                className="vrf-season-poster"
                fallback={season.season_number === 0 ? '⭐' : `S${season.season_number}`}
              />
              <span className="vrf-season-name">{season.title}</span>
              <span className="vrf-season-count">
                {eps.length} missing of {season.episode_total || eps.length}
              </span>
            </div>
            <div className="vrf-eps">
              {eps.map((e) => (
                <div className="vrf-ep" key={e.episode_number}>
                  <Poster
                    src={
                      e.has_still && e.id != null ? `/api/video/poster/episode/${e.id}?w=500` : ''
                    }
                    className="vrf-ep-still"
                    fallback="▷"
                  />
                  <div className="vrf-ep-body">
                    <div className="vrf-ep-head">
                      <span className="vrf-ep-code">
                        {episodeCode(season.season_number, e.episode_number)}
                      </span>
                      <span className="vrf-ep-title">
                        {e.title || `Episode ${e.episode_number}`}
                      </span>
                      {e.owned ? (
                        <span className="vrf-chip vrf-chip--got">✓ got it since</span>
                      ) : null}
                      <span className="vrf-ep-date">{e.air_date || ''}</span>
                    </div>
                    {e.overview ? <p className="vrf-ep-overview">{e.overview}</p> : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))
      ) : (
        <Hint>
          These episodes are no longer in the library listing. Re-run the job to refresh this
          finding.
        </Hint>
      )}
      <Hint>
        Approving sends every listed episode to the wishlist. The auto-downloader takes it from
        there.
      </Hint>
    </>
  );
}

/** an episode-level finding (quality or broken). the vanilla called a panel
 *  function here that was never defined, so these rows threw on open. */
function EpisodeQualityPanel({ d, quality = true }: { d: Details; quality?: boolean }) {
  const file: Details = d.file || {};
  return (
    <>
      <div className="vrf-show">
        <Poster
          src={d.show_id != null ? `/api/video/poster/show/${d.show_id}` : ''}
          className="vrf-poster"
          fallback="📺"
        />
        <div className="vrf-show-info">
          <div className="vrf-show-title">
            {d.show_title || d.title || '?'} {episodeCode(d.season_number, d.episode_number)}
          </div>
          {d.episode_title ? <div className="vrf-show-sub">{d.episode_title}</div> : null}
          {quality ? (
            <div className="vrf-chips">
              <span className="vrf-chip vrf-chip--miss">now: {file.resolution || '?'}</span>
              <span className="vrf-chip">cutoff: {d.cutoff || '?'}</span>
            </div>
          ) : (
            <div className="vrf-chips">
              <span className="vrf-chip vrf-chip--miss">{d.reason || 'suspect'}</span>
            </div>
          )}
          <div className="vrf-actions">
            <ViewButton kind="show" id={d.show_id} label="View show" />
          </div>
        </div>
      </div>
      {quality ? <FileCard file={file} /> : null}
    </>
  );
}

function MoviePanel({ finding, d }: { finding: RepairFinding; d: Details }) {
  const { loading, data: movie } = useLiveTitle(d.movie_id != null ? 'movie' : null, d.movie_id);
  if (d.movie_id == null) {
    if (finding.finding_type === 'quality_upgrade' && d.kind === 'episode') {
      return <EpisodeQualityPanel d={d} />;
    }
    if (finding.finding_type === 'broken_file' && d.episode_id != null) {
      return (
        <>
          <EpisodeQualityPanel d={d} quality={false} />
          <BrokenBody d={d} />
        </>
      );
    }
    return <RawDetails d={d} />;
  }
  if (loading) return <div className="repair-loading">Loading details…</div>;
  if (!movie) return <RawDetails d={d} />;

  const sub = [
    movie.studio,
    movie.content_rating,
    movie.runtime_minutes ? `${movie.runtime_minutes} min` : null,
  ].filter(Boolean);
  const chips: React.ReactNode[] = [];
  let body: React.ReactNode = null;
  if (finding.finding_type === 'quality_upgrade') {
    const file: Details = d.file || {};
    chips.push(
      <span className="vrf-chip vrf-chip--miss" key="now">
        now: {file.resolution || '?'}
      </span>,
      <span className="vrf-chip" key="cut">
        cutoff: {d.cutoff || '?'}
      </span>,
    );
    body = <FileCard file={file} />;
  } else if (finding.finding_type === 'broken_file') {
    chips.push(
      <span className="vrf-chip vrf-chip--miss" key="why">
        {d.reason || 'suspect'}
      </span>,
    );
    body = <BrokenBody d={d} />;
  } else if (finding.finding_type === 'metadata_gap') {
    for (const gap of d.gaps || []) {
      chips.push(
        <span className="vrf-chip vrf-chip--miss" key={gap}>
          {GAP_LABELS[gap] || gap}
        </span>,
      );
    }
  }
  return (
    <>
      <div className="vrf-show">
        <Poster
          src={movie.has_poster ? `/api/video/poster/movie/${movie.id}` : ''}
          className="vrf-poster"
          fallback="🎬"
        />
        <div className="vrf-show-info">
          <div className="vrf-show-title">
            {movie.title}
            {movie.year ? <span className="vrf-show-year"> ({movie.year})</span> : null}
          </div>
          {sub.length ? <div className="vrf-show-sub">{sub.join(' · ')}</div> : null}
          <div className="vrf-chips">{chips}</div>
          {movie.overview ? <p className="vrf-show-overview">{movie.overview}</p> : null}
          <div className="vrf-actions">
            <ViewButton kind="movie" id={movie.id} label="View movie" />
          </div>
        </div>
      </div>
      {body}
    </>
  );
}

function BrokenBody({ d }: { d: Details }) {
  if (!d.expected_seconds) return <FileCard file={d.file} />;
  const pct = Math.min(100, Math.round((100 * (d.actual_seconds || 0)) / d.expected_seconds));
  return (
    <>
      <div className="vrf-runtime">
        <div className="vrf-runtime-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="vrf-runtime-label">
        file runs {Math.round((d.actual_seconds || 0) / 60)} of{' '}
        {Math.round(d.expected_seconds / 60)} minutes
      </div>
      <FileCard file={d.file} />
    </>
  );
}

// ── entry ────────────────────────────────────────────────────────────────────

export interface VideoFindingDetailProps {
  finding: RepairFinding;
  /** a non-default fix (the ghost's Forget) rides the same fix endpoint. */
  onFixAction?: (finding: RepairFinding, action: string) => void;
}

export function VideoFindingDetail({ finding, onFixAction }: VideoFindingDetailProps) {
  const d: Details = finding.details || {};
  const type = finding.finding_type;
  let body: React.ReactNode;
  if (type === 'missing_episodes') body = <MissingEpisodesDetail d={d} />;
  else if (LAZY_TYPES.has(type)) body = <MoviePanel finding={finding} d={d} />;
  else if (type === 'incomplete_collection') body = <CollectionDetail d={d} />;
  else if (type === 'duplicate_movie' || type === 'duplicate_episode')
    body = <DuplicateDetail d={d} />;
  else if (type === 'stale_wishlist') body = <StaleWishlistDetail d={d} />;
  else if (type === 'youtube_ghost')
    body = <GhostDetail finding={finding} onFixAction={onFixAction} />;
  else if (type === 'youtube_mass_missing') body = <MassMissingDetail d={d} />;
  else if (type === 'watched_cleanup') body = <WatchedDetail d={d} />;
  else if (type === 'naming_mismatch') body = <NamingDetail d={d} />;
  else if (type === 'missing_subtitles') body = <SubtitlesDetail d={d} />;
  else body = <RawDetails d={d} />;
  return (
    <div className="vrf-detail" data-video-finding-type={type}>
      {body}
    </div>
  );
}
