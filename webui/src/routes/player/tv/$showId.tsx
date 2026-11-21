import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import type { DetailEpisode, VideoFile } from '../-player.types';

import { remaining, runtime, shortDate } from '../-lib/format';
import { backdropSet, stillSet, WIDE_SIZES } from '../-lib/images';
import {
  backdropUrl,
  fraction,
  moreLikeThis,
  posterUrl,
  proxiedImage,
  showCard,
} from '../-lib/rails';
import { rankVersions, resolutionLabel } from '../-lib/video';
import {
  progressLookupQuery,
  showDetailQuery,
  showFilesQuery,
  videoCatalogQuery,
} from '../-player.api';
import {
  Art,
  Badge,
  CardRail,
  Hero,
  PlayButton,
  Tabs,
  VerdictBadge,
  styles as s,
} from '../-ui/kit';
import { NetworkLink, PeopleLinks, VideoGenreLinks } from '../-ui/links';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { BackgroundTrailer, TrailerButton } from '../-ui/trailer';
import { Facts, Ratings } from '../-ui/video-facts';
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
            src={episode.has_still ? stillSet(episode.id).src : null}
            srcSet={episode.has_still ? stillSet(episode.id).srcSet : undefined}
            sizes={WIDE_SIZES}
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
            {[
              shortDate(episode.air_date),
              runtime(seconds),
              episode.rating ? `★ ${episode.rating.toFixed(1)}` : '',
              owned ? '' : 'not in your library',
            ]
              .filter(Boolean)
              .join(' · ')}
            {position !== undefined && done !== undefined && done < 1
              ? ` · ${remaining(position, duration ?? seconds)}`
              : ''}
          </span>
          {episode.overview ? <span className={s.rowText}>{episode.overview}</span> : null}
        </div>
        <div className={s.rowEnd}>
          {episode.watched ? <Badge tone="yes">Watched</Badge> : null}
          {best?.resolution ? <Badge>{resolutionLabel(best.resolution)}</Badge> : null}
          {files.length > 1 ? <Badge>{files.length} versions</Badge> : null}
          {best?.playable ? (
            <VerdictBadge verdict={best.playable.verdict} reasons={best.playable.reasons} />
          ) : null}
        </div>
      </div>
      {open ? (
        <div className={s.rowMore}>
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
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: ok });

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

  const firstYear = show.first_air_date?.slice(0, 4) ?? (show.year ? String(show.year) : '');
  const lastYear = show.last_air_date?.slice(0, 4) ?? '';
  const ended = (show.status ?? '').toLowerCase() === 'ended';
  const years =
    firstYear && ended && lastYear && lastYear !== firstYear
      ? `${firstYear}–${lastYear}`
      : firstYear;
  const next = show.next_up;
  const creators = show.crew?.filter((c) => c.job === 'Creator');
  const similar = moreLikeThis(catalog.data?.shows ?? [], { id, genres: show.genres ?? [] }).map(
    showCard,
  );

  return (
    <>
      <Hero
        backdrop={show.has_backdrop !== false ? backdropUrl('show', id) : null}
        backdropSrcSet={backdropSet('show', id).srcSet}
        poster={show.has_poster !== false ? posterUrl('show', id, 500) : null}
        title={show.title}
        trailer={<BackgroundTrailer kind="show" id={id} />}
        logo={show.logo ? proxiedImage(show.logo) : null}
        meta={
          <>
            {years ? <span>{years}</span> : null}
            {show.network ? <NetworkLink network={show.network} /> : null}
            {seasons.length ? (
              <span>
                {seasons.length} season{seasons.length === 1 ? '' : 's'}
              </span>
            ) : null}
            <VideoGenreLinks kind="tv" genres={(show.genres ?? []).slice(0, 3)} />
          </>
        }
        badges={
          <>
            <div className={s.badges}>
              {show.content_rating ? <Badge>{show.content_rating}</Badge> : null}
              {show.status ? <Badge>{ended ? 'Ended' : 'Still airing'}</Badge> : null}
            </div>
            <Ratings d={show} />
          </>
        }
        overview={show.overview}
        actions={
          <>
            <PlayButton
              label={
                next
                  ? `${next.resume ? 'Resume' : 'Play'} S${next.season_number} E${next.episode_number}`
                  : 'Play'
              }
            />
            <TrailerButton kind="show" id={id} />
            {next?.title ? <span className={s.notYet}>Up next: {next.title}</span> : null}
          </>
        }
      />

      <div className={s.rail}>
        <Facts
          rows={[
            ['Created by', creators?.length ? <PeopleLinks people={creators} /> : null],
            ['Network', show.network ? <NetworkLink network={show.network} /> : null],
            [
              'Episodes',
              show.episode_total
                ? `${show.episode_owned ?? 0} of ${show.episode_total} in your library`
                : null,
            ],
            ['Watched', show.watched_episodes ? `${show.watched_episodes} episodes` : null],
            ['First aired', shortDate(show.first_air_date)],
            ['Last aired', ended ? shortDate(show.last_air_date) : null],
            ['Awards', show.awards],
          ]}
        />
      </div>

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
      <CardRail title="More like this" cards={similar} />
    </>
  );
}
