import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import type { DetailEpisode, VideoFile } from '../-player.types';

import { remaining, runtime, shortDate } from '../-lib/format';
import { backdropUrl, fraction, posterUrl, proxiedImage } from '../-lib/rails';
import { rankVersions } from '../-lib/video';
import { progressLookupQuery, showDetailQuery, showFilesQuery } from '../-player.api';
import { Art, Badge, Hero, PlayButton, Tabs, VerdictBadge, styles as s } from '../-ui/kit';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { CastRow, VersionPicker } from '../-ui/video-parts';

export const Route = createFileRoute('/player/tv/$showId')({
  validateSearch: (search: Record<string, unknown>): { season?: number } => {
    const n = Number(search.season);
    return Number.isInteger(n) && n >= 0 ? { season: n } : {};
  },
  component: ShowPage,
});

function EpisodeRow({
  episode,
  files,
  position,
  duration,
}: {
  episode: DetailEpisode;
  files: VideoFile[];
  position?: number;
  duration?: number | null;
}) {
  const [open, setOpen] = useState(false);
  const owned = episode.owned !== false && files.length > 0;
  const best = rankVersions(files)[0];
  const seconds =
    best?.runtime_seconds ?? (episode.runtime_minutes ? episode.runtime_minutes * 60 : null);
  const done = position !== undefined ? fraction(position, duration ?? seconds) : undefined;
  return (
    <div>
      <div
        className={s.row}
        data-media="true"
        data-missing={owned ? undefined : 'true'}
        role="button"
        tabIndex={0}
        aria-expanded={files.length > 1 ? open : undefined}
        onClick={() => files.length > 1 && setOpen((v) => !v)}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && files.length > 1) {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
        data-nav
      >
        <div className={s.rowArt}>
          <Art
            src={episode.has_still ? posterUrl('episode', episode.id, 400) : null}
            alt={`Episode ${episode.episode_number}`}
          />
          {done !== undefined ? (
            <div className={s.rowBar}>
              <div className={s.progressFill} style={{ width: `${done * 100}%` }} />
            </div>
          ) : null}
        </div>
        <div className={s.rowMain}>
          <span className={s.rowTitle}>
            {episode.episode_number}. {episode.title || `Episode ${episode.episode_number}`}
          </span>
          <span className={s.rowSub}>
            {[shortDate(episode.air_date), runtime(seconds), owned ? '' : 'not in your library']
              .filter(Boolean)
              .join(' · ')}
            {position !== undefined && done !== undefined && done < 1
              ? ` · ${remaining(position, duration ?? seconds)}`
              : ''}
          </span>
          {episode.overview ? <span className={s.rowText}>{episode.overview}</span> : null}
        </div>
        <div className={s.rowEnd}>
          {files.length > 1 ? <Badge>{files.length} versions</Badge> : null}
          {best?.playable ? (
            <VerdictBadge verdict={best.playable.verdict} reasons={best.playable.reasons} />
          ) : null}
        </div>
      </div>
      {open ? (
        <div style={{ padding: '0 0.75rem 1rem' }}>
          <VersionPicker files={files} />
        </div>
      ) : null}
    </div>
  );
}

function ShowPage() {
  const sections = useSections();
  const id = Number(Route.useParams().showId);
  const { season: wanted } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/tv/$showId' });
  const ok = sections.tv && Number.isFinite(id);
  const detail = useQuery({ ...showDetailQuery(id), enabled: ok, retry: false });

  const seasons = useMemo(
    () =>
      (detail.data?.seasons ?? [])
        .filter((x) => (x.episode_owned ?? x.episodes.length) > 0 || x.episodes.length > 0)
        .sort((a, b) =>
          a.season_number === 0
            ? 1
            : b.season_number === 0
              ? -1
              : a.season_number - b.season_number,
        ),
    [detail.data],
  );
  // the first season you own anything of, unless the url says otherwise
  const firstOwned = seasons.find((x) => (x.episode_owned ?? 0) > 0) ?? seasons[0];
  const current = seasons.find((x) => x.season_number === wanted) ?? firstOwned;

  const files = useQuery({
    ...showFilesQuery(id, current?.season_number ?? 1),
    enabled: ok && !!current,
    retry: false,
  });
  const episodeIds = (current?.episodes ?? []).map((e) => String(e.id));
  const progress = useQuery({
    ...progressLookupQuery('episode', episodeIds),
    enabled: ok && episodeIds.length > 0,
  });

  const filesByEpisode = useMemo(() => {
    const map = new Map<number, VideoFile[]>();
    for (const season of files.data?.seasons ?? [])
      for (const ep of season.episodes) map.set(ep.id, ep.files ?? []);
    return map;
  }, [files.data]);

  if (!sections.tv) return <NotPartOfProfile what="TV shows" />;
  if (detail.isLoading)
    return <div className={s.skeleton} style={{ height: '60vh', marginTop: '2rem' }} />;
  const show = detail.data;
  if (!show || detail.isError) return <NotFound what="Show" />;

  return (
    <>
      <Hero
        backdrop={show.has_backdrop !== false ? backdropUrl('show', id) : null}
        poster={show.has_poster !== false ? posterUrl('show', id, 500) : null}
        title={show.title}
        logo={show.logo ? proxiedImage(show.logo) : null}
        meta={
          <>
            {show.year ? <span>{show.year}</span> : null}
            {show.network ? <span>{show.network}</span> : null}
            {seasons.length ? (
              <span>
                {seasons.length} season{seasons.length === 1 ? '' : 's'}
              </span>
            ) : null}
            {(show.genres ?? []).slice(0, 3).map((g) => (
              <span key={g}>{g}</span>
            ))}
          </>
        }
        badges={
          <div className={s.badges}>
            {show.content_rating ? <Badge>{show.content_rating}</Badge> : null}
            {show.rating ? <Badge>★ {show.rating.toFixed(1)}</Badge> : null}
          </div>
        }
        overview={show.overview}
        actions={<PlayButton label="Play next episode" />}
      />

      {seasons.length > 1 ? (
        <div className={s.rail}>
          <Tabs
            label="Season"
            value={String(current?.season_number ?? '')}
            options={seasons.map((x) => ({
              value: String(x.season_number),
              label: x.season_number === 0 ? 'Specials' : `Season ${x.season_number}`,
            }))}
            onChange={(next) => void navigate({ search: { season: Number(next) }, replace: true })}
          />
        </div>
      ) : null}

      <div className={s.list} aria-label="Episodes">
        {(current?.episodes ?? [])
          .slice()
          .sort((a, b) => a.episode_number - b.episode_number)
          .map((episode) => {
            const row = progress.data?.[String(episode.id)];
            return (
              <EpisodeRow
                key={episode.id}
                episode={episode}
                files={filesByEpisode.get(episode.id) ?? []}
                position={
                  row && !row.finished
                    ? row.position_seconds
                    : row?.finished
                      ? (row.duration_seconds ?? undefined)
                      : undefined
                }
                duration={row?.duration_seconds}
              />
            );
          })}
      </div>

      <CastRow cast={show.cast} />
    </>
  );
}
