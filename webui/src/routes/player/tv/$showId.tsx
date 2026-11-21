import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import type { DetailEpisode, VideoFile } from '../-player.types';

import { remaining, runtime, shortDate } from '../-lib/format';
import { backdropSet, stillSet, WIDE_SIZES } from '../-lib/images';
import { episodeItems } from '../-lib/play-items';
import { backdropUrl, fraction, moreLikeThis, posterUrl, proxiedImage } from '../-lib/rails';
import { rankVersions, resolutionLabel } from '../-lib/video';
import {
  extrasQuery,
  progressLookupQuery,
  showDetailQuery,
  showFilesQuery,
  videoCatalogQuery,
} from '../-player.api';
import {
  DetailLayout,
  ExtrasVideos,
  Gallery,
  InfoPanel,
  Keywords,
  MoreLikeThis,
  ownedByTmdb,
  Section,
} from '../-ui/detail';
import { Art, Badge, Hero, HScroll, PlayButton, VerdictBadge, styles as s } from '../-ui/kit';
import { CrewList, mergeCrew, NetworkLink, VideoGenreLinks } from '../-ui/links';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { playItems } from '../-ui/playback/use-player';
import { useSections } from '../-ui/sections';
import { BackgroundTrailer, TrailerButton } from '../-ui/trailer';
import {
  Facts,
  LanguageSummary,
  languageName,
  languageNames,
  meaningfulAwards,
  Ratings,
} from '../-ui/video-facts';
import { CastRow, VersionPicker } from '../-ui/video-parts';
import { WatchlistButton } from '../-ui/watchlist';

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
  onPlay,
  filesKnown = true,
}: {
  episode: DetailEpisode;
  files: VideoFile[];
  position?: number;
  duration?: number | null;
  /** play this episode (and the rest of the show after it), in this version */
  onPlay?: (fileId?: number) => void;
  /** the show's file list has arrived (until then, ownership is the show's word) */
  filesKnown?: boolean;
}) {
  const [open, setOpen] = useState(false);
  // the show says what you own; the file list (versions, quality) only adds
  // detail. an episode is never greyed out just because that list is late
  const owned =
    episode.owned === true || (episode.owned !== false && (files.length > 0 || !filesKnown));
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
        aria-label={owned ? `Play episode ${episode.episode_number}` : undefined}
        aria-disabled={owned ? undefined : 'true'}
        onClick={() => owned && onPlay?.()}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && owned) {
            e.preventDefault();
            onPlay?.();
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
          {files.length > 1 ? (
            <button
              type="button"
              className={s.versionsToggle}
              aria-expanded={open}
              onClick={(e) => {
                e.stopPropagation();
                setOpen((v) => !v);
              }}
              data-nav
            >
              {files.length} versions
            </button>
          ) : null}
          {best?.playable ? (
            <VerdictBadge verdict={best.playable.verdict} reasons={best.playable.reasons} />
          ) : null}
        </div>
      </div>
      {open ? (
        <div className={s.rowMore}>
          <VersionPicker files={files} onChange={(fileId) => onPlay?.(fileId)} />
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
  const extras = useQuery({ ...extrasQuery('show', id), enabled: ok });

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
  // where you are: the season up next, else the first one you own anything of
  const nextSeason = detail.data?.next_up?.season_number;
  const firstOwned =
    seasons.find((x) => x.season_number === nextSeason) ??
    seasons.find((x) => (x.episode_owned ?? 0) > 0) ??
    seasons[0];
  const current = seasons.find((x) => x.season_number === wanted) ?? firstOwned;
  const [onlyMine, setOnlyMine] = useState(false);

  const files = useQuery({
    ...showFilesQuery(id),
    enabled: ok,
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
  // the episode up next (by season and number), else the first one owned
  const upNextId = next
    ? seasons
        .find((x) => x.season_number === next.season_number)
        ?.episodes.find((e) => e.episode_number === next.episode_number && e.owned !== false)?.id
    : undefined;
  const firstOwnedEpisode = episodeItems(show, seasons)[0]?.id;
  /** play from this episode on: the rest of the show follows */
  const playFrom = (episodeId?: number | string, fileId?: number) => {
    const queue = episodeItems(show, seasons, episodeId ? Number(episodeId) : undefined);
    if (!queue.length) return;
    if (fileId) queue[0] = { ...queue[0], part: String(fileId) };
    playItems(queue);
  };
  const creators = show.crew?.filter((c) => c.job === 'Creator');
  // still airing: when the next one lands
  const nextAir = !ended ? extras.data?.next_episode : null;
  const genreMatches = moreLikeThis(catalog.data?.shows ?? [], { id, genres: show.genres ?? [] });
  const tmdbId = catalog.data?.shows.find((x) => x.id === id)?.tmdb_id ?? null;
  const owned = ownedByTmdb(catalog.data?.shows ?? []);
  const subs = languageNames(show.subtitle_langs);

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
              onPlay={
                upNextId || firstOwnedEpisode
                  ? () => playFrom(upNextId ?? firstOwnedEpisode)
                  : undefined
              }
              why="There are no episodes on disk yet."
            />
            <WatchlistButton kind="show" id={id} />
            <TrailerButton kind="show" id={id} />
            {next?.title ? <span className={s.notYet}>Up next: {next.title}</span> : null}
          </>
        }
      />

      <DetailLayout
        main={
          <>
            <Section title="Episodes">
              {seasons.length > 1 ? (
                <HScroll className={s.seasonPicker}>
                  {seasons.map((x) => {
                    const owned = x.episode_owned ?? 0;
                    const total = x.episode_total ?? x.episodes.length;
                    return (
                      <button
                        key={x.season_number}
                        type="button"
                        className={s.seasonPill}
                        aria-pressed={x.season_number === current?.season_number}
                        data-empty={owned === 0 ? 'true' : undefined}
                        title={`${owned} of ${total} episodes in your library`}
                        onClick={() =>
                          void navigate({ search: { season: x.season_number }, replace: true })
                        }
                        data-nav
                      >
                        {x.season_number === 0
                          ? 'Specials'
                          : seasons.length > 8
                            ? `S${x.season_number}`
                            : `Season ${x.season_number}`}
                      </button>
                    );
                  })}
                </HScroll>
              ) : null}

              {current ? (
                <div className={s.seasonHead}>
                  <span className={s.rowSub}>
                    {current.season_number === 0 ? 'Specials' : `Season ${current.season_number}`}
                    {' · '}
                    {current.episode_owned ?? 0} of{' '}
                    {current.episode_total ?? current.episodes.length} episodes in your library
                  </span>
                  {(current.episode_owned ?? 0) <
                  (current.episode_total ?? current.episodes.length) ? (
                    <label className={s.check}>
                      <input
                        type="checkbox"
                        checked={onlyMine}
                        onChange={(e) => setOnlyMine(e.target.checked)}
                        data-nav
                      />{' '}
                      Only what I have
                    </label>
                  ) : null}
                </div>
              ) : null}

              <div className={s.list} aria-label="Episodes">
                {(current?.episodes ?? [])
                  .filter((e) => !onlyMine || e.owned !== false)
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
                        onPlay={(fileId) => playFrom(episode.id, fileId)}
                        filesKnown={!!files.data}
                      />
                    );
                  })}
              </div>
            </Section>
            <ExtrasVideos videos={extras.data?.videos} />
            <CastRow cast={show.cast} />
            <MoreLikeThis
              extras={extras.data}
              owned={owned}
              fallback={genreMatches}
              selfTmdb={tmdbId}
            />
            <Gallery gallery={extras.data?.gallery} />
          </>
        }
        side={
          <>
            {creators?.length ? (
              <InfoPanel title={creators.length > 1 ? 'Creators' : 'Creator'}>
                <CrewList people={mergeCrew(creators)} />
              </InfoPanel>
            ) : null}
            <InfoPanel title="Details">
              <Facts
                rows={[
                  [
                    'Next episode',
                    nextAir?.air_date
                      ? `S${nextAir.season_number} E${nextAir.episode_number} · ${shortDate(nextAir.air_date)}`
                      : null,
                  ],
                  ['Network', show.network ? <NetworkLink network={show.network} /> : null],
                  ['Status', show.status ? (ended ? 'Ended' : 'Still airing') : null],
                  ['First aired', shortDate(show.first_air_date)],
                  ['Last aired', ended ? shortDate(show.last_air_date) : null],
                  ['Seasons', seasons.filter((x) => x.season_number > 0).length || null],
                  [
                    'In your library',
                    show.episode_total
                      ? `${show.episode_owned ?? 0} of ${show.episode_total} episodes`
                      : null,
                  ],
                  ['Watched', show.watched_episodes ? `${show.watched_episodes} episodes` : null],
                  ['Rated', show.content_rating],
                  ['Country', extras.data?.facts?.countries?.join(', ')],
                  [
                    'Language',
                    extras.data?.facts?.original_language
                      ? languageName(extras.data.facts.original_language)
                      : null,
                  ],
                  ['Awards', meaningfulAwards(show.awards)],
                ]}
              />
            </InfoPanel>
            {subs.length ? (
              <InfoPanel title="Languages">
                <Facts rows={[['Subtitles', <LanguageSummary key="s" names={subs} />]]} />
              </InfoPanel>
            ) : null}
            <Keywords words={extras.data?.keywords} />
          </>
        }
      />
    </>
  );
}
