import { useQuery } from '@tanstack/react-query';
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { DetailEpisode, VideoFile } from '../-player.types';

import { remaining, shortDate } from '../-lib/format';
import { backdropSet, remoteImage, stillSet, WIDE_SIZES } from '../-lib/images';
import { episodeItems } from '../-lib/play-items';
import { backdropUrl, fraction, moreLikeThis, posterUrl, proxiedImage } from '../-lib/rails';
import { rankVersions, resolutionLabel } from '../-lib/video';
import {
  episodeExtraQuery,
  extrasQuery,
  progressLookupQuery,
  requestOptionsQuery,
  requestTitle,
  showDetailQuery,
  showFilesQuery,
  videoCatalogQuery,
  type RequestMode,
  type RequestResult,
} from '../-player.api';
import { Comments } from '../-ui/comments';
import {
  DetailLayout,
  ExtrasVideos,
  Gallery,
  InfoPanel,
  Keywords,
  MoreLikeThis,
  ownedByTmdb,
  RelatedDiscoveryRails,
  Section,
} from '../-ui/detail';
import { DownloadMediaButton } from '../-ui/download-button';
import { Art, Badge, Hero, HScroll, PlayButton, VerdictBadge, styles as s } from '../-ui/kit';
import { CrewList, mergeCrew, NetworkLink, VideoGenreLinks } from '../-ui/links';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { playItems } from '../-ui/playback/use-player';
import { ReportIssueButton } from '../-ui/report-issue';
import { useSections } from '../-ui/sections';
import { BackgroundTrailer, TrailerButton } from '../-ui/trailer';
import {
  Facts,
  fileLanguages,
  LanguageSummary,
  languageName,
  languageNames,
  meaningfulAwards,
  Ratings,
} from '../-ui/video-facts';
import { CastRow, VersionPicker } from '../-ui/video-parts';
import { WatchlistButton } from '../-ui/watchlist';

export const Route = createFileRoute('/player/tv/$showId')({
  validateSearch: (search: Record<string, unknown>): { season?: number; episode?: number } => {
    const sNum = Number(search.season);
    const eNum = Number(search.episode);
    return {
      ...(Number.isInteger(sNum) && sNum >= 0 ? { season: sNum } : {}),
      ...(Number.isInteger(eNum) && eNum >= 0 ? { episode: eNum } : {}),
    };
  },
  component: ShowPage,
});

function EpisodeRow({
  showId,
  showTitle,
  showTmdbId,
  seasonNumber,
  episode,
  files,
  position,
  duration,
  onPlay,
  filesKnown = true,
  requestMode = 'none',
  autoExpand = false,
}: {
  showId: number;
  showTitle: string;
  showTmdbId?: number | null;
  seasonNumber: number;
  episode: DetailEpisode;
  files: VideoFile[];
  position?: number;
  duration?: number | null;
  /** play this episode (and the rest of the show after it), in this version */
  onPlay?: (fileId?: number) => void;
  /** the show's file list has arrived (until then, ownership is the show's word) */
  filesKnown?: boolean;
  /** profile's show request permission ('download' | 'ask' | 'none') */
  requestMode?: RequestMode;
  autoExpand?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(autoExpand);
  const rowRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!autoExpand) return;
    setExpanded(true);
    const t = window.setTimeout(() => {
      rowRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 120);
    return () => window.clearTimeout(t);
  }, [autoExpand]);
  const [reqBusy, setReqBusy] = useState(false);
  const [reqResult, setReqResult] = useState<RequestResult | null>(null);
  const epExtra = useQuery({
    ...episodeExtraQuery(showTmdbId ?? 0, seasonNumber, episode.episode_number),
    enabled: expanded && !!showTmdbId && showTmdbId > 0,
  });
  // the show says what you own; the file list (versions, quality) only adds
  // detail. an episode is never greyed out just because that list is late
  const owned =
    episode.owned === true || (episode.owned !== false && (files.length > 0 || !filesKnown));
  const best = rankVersions(files)[0];
  const seconds =
    best?.runtime_seconds ??
    (episode.runtime_minutes
      ? episode.runtime_minutes * 60
      : epExtra.data?.runtime_minutes
        ? epExtra.data.runtime_minutes * 60
        : null);
  const done = position !== undefined ? fraction(position, duration ?? seconds) : undefined;
  const overview = episode.overview || epExtra.data?.overview || null;
  const langs = fileLanguages(best);
  const guestStars = epExtra.data?.guest_stars ?? [];
  const epCrew = epExtra.data?.crew ?? [];
  const epCode = `S${String(seasonNumber).padStart(2, '0')}E${String(episode.episode_number).padStart(2, '0')}`;
  const epIssueTitle = `${showTitle} · ${epCode}${episode.title ? ` — ${episode.title}` : ''}`;
  const canRequestEpisode = !owned && !!showTmdbId && showTmdbId > 0 && requestMode !== 'none';
  const handleRequestEpisode = async () => {
    if (!canRequestEpisode || !showTmdbId || reqBusy) return;
    setReqBusy(true);
    try {
      const res = await requestTitle({
        kind: 'episode',
        tmdb_id: showTmdbId,
        title: showTitle,
        season: seasonNumber,
        episode: episode.episode_number,
        get: requestMode === 'download',
      });
      setReqResult(res);
    } finally {
      setReqBusy(false);
    }
  };
  const reqLabel =
    reqResult?.state === 'got'
      ? 'Added to wishlist'
      : reqResult?.state === 'asked'
        ? reqResult.already
          ? 'Already requested'
          : 'Requested'
        : reqResult?.state === 'on-the-way'
          ? 'On the way'
          : reqResult?.state === 'owned'
            ? 'In library'
            : reqBusy
              ? 'Requesting…'
              : requestMode === 'download'
                ? 'Get episode'
                : 'Request episode';
  const reqDone =
    reqResult?.state === 'got' ||
    reqResult?.state === 'asked' ||
    reqResult?.state === 'on-the-way' ||
    reqResult?.state === 'owned';
  return (
    <div
      ref={rowRef}
      className={s.episodeCard}
      style={
        autoExpand
          ? {
              boxShadow: '0 0 0 1.5px rgba(var(--accent-rgb, 129, 140, 248), 0.7)',
              borderRadius: 14,
            }
          : undefined
      }
      data-expanded={expanded ? 'true' : undefined}
      data-watched={episode.watched ? 'true' : undefined}
      data-missing={owned ? undefined : 'true'}
    >
      <div
        className={s.episodeHeader}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-label={`Episode ${episode.episode_number}: ${episode.title || `Episode ${episode.episode_number}`}`}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setExpanded((v) => !v);
          }
        }}
        data-show-id={showId}
        data-nav
      >
        <div
          className={s.episodeStill}
          onClick={(e) => {
            if (owned) {
              e.stopPropagation();
              onPlay?.();
            }
          }}
        >
          <Art
            src={episode.has_still ? stillSet(episode.id).src : null}
            srcSet={episode.has_still ? stillSet(episode.id).srcSet : undefined}
            sizes={WIDE_SIZES}
            alt={`Episode ${episode.episode_number}`}
          />
          <div className={s.episodeStillVignette} aria-hidden="true" />
          <span className={s.episodeNumBadge}>
            E{String(episode.episode_number).padStart(2, '0')}
          </span>
          {episode.watched ? (
            <span className={s.episodeWatchedBadge} title="Watched" aria-label="Watched">
              ✓
            </span>
          ) : null}
          {owned ? (
            <span className={s.episodePlayOrb} aria-hidden="true">
              ▶
            </span>
          ) : (
            <span className={s.episodeMissingPill}>MISSING</span>
          )}
          {seconds ? (
            <span className={s.episodeRuntimeBadge}>{Math.max(1, Math.round(seconds / 60))}m</span>
          ) : null}
          {done !== undefined ? (
            <div className={s.rowBar}>
              <div className={s.progressFill} style={{ width: `${done * 100}%` }} />
            </div>
          ) : null}
        </div>
        <div className={s.rowMain}>
          <span className={s.rowTitle}>{episode.title || `Episode ${episode.episode_number}`}</span>
          <span className={s.rowSub}>
            {[
              shortDate(episode.air_date),
              episode.rating ? `★ ${episode.rating.toFixed(1)}` : '',
              best?.resolution ? resolutionLabel(best.resolution) : '',
              owned ? '' : 'Not in library',
            ]
              .filter(Boolean)
              .join(' · ')}
            {position !== undefined && done !== undefined && done < 1
              ? ` · ${remaining(position, duration ?? seconds)}`
              : ''}
          </span>
          {!expanded && episode.overview ? (
            <span className={s.rowText}>{episode.overview}</span>
          ) : null}
        </div>
        <div className={s.rowEnd}>
          {canRequestEpisode ? (
            <button
              type="button"
              className={reqDone ? s.btn : s.epActionPrimary}
              disabled={reqBusy || reqDone}
              onClick={(e) => {
                e.stopPropagation();
                void handleRequestEpisode();
              }}
              style={
                reqDone
                  ? { height: 34, padding: '0 13px', fontSize: '0.8rem' }
                  : { height: 34, padding: '0 14px', fontSize: '0.8rem' }
              }
              data-nav
            >
              {reqLabel}
            </button>
          ) : null}
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
      {expanded ? (
        <div className={s.episodeExpandBody}>
          <div className={s.episodeChipRow}>
            <span className={s.epChipAccent}>{epCode}</span>
            {best?.resolution ? (
              <span className={s.epChip}>{resolutionLabel(best.resolution)}</span>
            ) : null}
            {best?.dynamic_range ? <span className={s.epChip}>{best.dynamic_range}</span> : null}
            {best?.atmos ? <span className={s.epChip}>Atmos</span> : null}
            {episode.rating || epExtra.data?.rating ? (
              <span className={s.epChipGold}>
                ★ {(episode.rating ?? epExtra.data?.rating ?? 0).toFixed(1)}
              </span>
            ) : null}
            {langs.audio.length ? (
              <span style={{ fontSize: '0.8rem', color: 'var(--pl-text-2)' }}>
                Audio: <LanguageSummary names={langs.audio} />
              </span>
            ) : null}
            {langs.subs.length ? (
              <span style={{ fontSize: '0.8rem', color: 'var(--pl-text-2)' }}>
                · Subtitles: <LanguageSummary names={langs.subs} />
              </span>
            ) : null}
          </div>

          {overview ? (
            <p
              style={{
                margin: 0,
                color: 'var(--pl-text-2)',
                fontSize: '0.94rem',
                lineHeight: 1.62,
              }}
            >
              {overview}
            </p>
          ) : null}

          <div className={s.episodeChipRow}>
            {owned ? (
              <button
                type="button"
                className={s.epActionPrimary}
                onClick={(e) => {
                  e.stopPropagation();
                  onPlay?.();
                }}
                data-nav
              >
                <span aria-hidden="true">▶</span>
                {position && done !== undefined && done < 1 ? 'Resume episode' : 'Play episode'}
              </button>
            ) : canRequestEpisode ? (
              <button
                type="button"
                className={reqDone ? s.btn : s.epActionPrimary}
                disabled={reqBusy || reqDone}
                onClick={(e) => {
                  e.stopPropagation();
                  void handleRequestEpisode();
                }}
                style={reqDone ? { height: 36, padding: '0 14px', fontSize: '0.82rem' } : undefined}
                data-nav
              >
                {reqLabel}
              </button>
            ) : null}
            {owned ? (
              <DownloadMediaButton
                itemType="episode"
                itemId={String(episode.id)}
                title={epIssueTitle}
                subtitle={showTitle}
                artwork={`/api/video/poster/episode/${episode.id}`}
              />
            ) : null}
            <ReportIssueButton
              entityType="episode"
              entityId={episode.id}
              title={epIssueTitle}
              compact
            />
            {reqResult?.state === 'error' ? (
              <span style={{ fontSize: '0.82rem', color: '#ff7b7b' }}>{reqResult.message}</span>
            ) : null}
          </div>

          {epCrew.length > 0 ? (
            <div className={s.episodeChipRow}>
              {epCrew.map((c, idx) =>
                c.tmdb_id ? (
                  <Link
                    key={`${c.job}-${c.name}-${idx}`}
                    to="/player/person/$personId"
                    params={{ personId: String(c.tmdb_id) }}
                    className={s.episodeCrewChip}
                    data-nav
                  >
                    <span className={s.episodeCrewJob}>{c.job}:</span>
                    <span>{c.name}</span>
                  </Link>
                ) : (
                  <span key={`${c.job}-${c.name}-${idx}`} className={s.episodeCrewChip}>
                    <span className={s.episodeCrewJob}>{c.job}:</span>
                    <span>{c.name}</span>
                  </span>
                ),
              )}
            </div>
          ) : null}

          {guestStars.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <span className={s.episodeSubheading}>Guest stars &amp; cast</span>
              <HScroll className={s.people}>
                {guestStars.map((g, idx) => {
                  const content = (
                    <>
                      <span className={s.personPhoto}>
                        <Art src={remoteImage(g.photo)} alt={g.name} />
                      </span>
                      <span className={s.personName}>{g.name}</span>
                      {g.character ? <span className={s.personRole}>{g.character}</span> : null}
                    </>
                  );
                  return g.tmdb_id ? (
                    <Link
                      key={`${g.tmdb_id}-${idx}`}
                      to="/player/person/$personId"
                      params={{ personId: String(g.tmdb_id) }}
                      className={s.person}
                      data-nav
                    >
                      {content}
                    </Link>
                  ) : (
                    <div key={`${g.name}-${idx}`} className={s.person}>
                      {content}
                    </div>
                  );
                })}
              </HScroll>
            </div>
          ) : epExtra.isLoading && showTmdbId ? (
            <span style={{ fontSize: '0.82rem', color: 'var(--pl-text-2)' }}>
              Loading episode cast &amp; details…
            </span>
          ) : null}

          {files.length > 1 ? (
            <VersionPicker files={files} onChange={(fileId) => onPlay?.(fileId)} />
          ) : null}
        </div>
      ) : null}
      {open && !expanded ? (
        <div className={s.episodeExpandBody}>
          <VersionPicker files={files} onChange={(fileId) => onPlay?.(fileId)} />
        </div>
      ) : null}
    </div>
  );
}

function ShowPage() {
  const sections = useSections();
  const id = Number(Route.useParams().showId);
  const { season: wanted, episode: targetEpisode } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/tv/$showId' });
  const ok = sections.tv && Number.isFinite(id);
  const detail = useQuery({ ...showDetailQuery(id), enabled: ok, retry: false });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: ok });
  const extras = useQuery({ ...extrasQuery('show', id), enabled: ok });
  const reqOptions = useQuery({ ...requestOptionsQuery(), enabled: ok });
  const showRequestMode: RequestMode = reqOptions.data?.show ?? 'none';

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
            <ReportIssueButton entityType="show" entityId={id} title={show.title} />
            {next?.title ? <span className={s.notYet}>Up next: {next.title}</span> : null}
          </>
        }
      />

      <DetailLayout
        main={
          <>
            <Section title={seasons.length > 1 ? `Seasons (${seasons.length})` : 'Episodes'}>
              {seasons.length > 1 ? (
                <HScroll className={s.seasonStrip}>
                  {seasons.map((x) => {
                    const sOwned = x.episode_owned ?? 0;
                    const sTotal = Math.max(x.episode_total ?? 0, x.episodes.length);
                    const pct = sTotal > 0 ? Math.min(100, Math.round((sOwned / sTotal) * 100)) : 0;
                    const label =
                      x.title?.trim() ||
                      (x.season_number === 0 ? 'Specials' : `Season ${x.season_number}`);
                    const art =
                      x.has_poster && x.id > 0
                        ? posterUrl('season', x.id, 342)
                        : show.has_poster !== false
                          ? posterUrl('show', id, 342)
                          : null;
                    return (
                      <button
                        key={x.season_number}
                        type="button"
                        className={s.seasonCard}
                        aria-pressed={x.season_number === current?.season_number}
                        title={`${sOwned} of ${sTotal} episodes in your library`}
                        onClick={() =>
                          void navigate({ search: { season: x.season_number }, replace: true })
                        }
                        data-nav
                      >
                        <span className={s.seasonPoster}>
                          <Art src={art} alt={label} />
                          <span className={s.seasonVignette} aria-hidden="true" />
                          <span className={s.seasonCountPill}>
                            {sOwned}/{sTotal}
                          </span>
                          <span className={s.seasonBar} aria-hidden="true">
                            <span className={s.seasonBarFill} style={{ width: `${pct}%` }} />
                          </span>
                        </span>
                        <span className={s.seasonCardTitle}>{label}</span>
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
                    <button
                      type="button"
                      className={s.filterPill}
                      aria-pressed={onlyMine}
                      onClick={() => setOnlyMine((v) => !v)}
                      data-nav
                    >
                      {onlyMine ? '✓ In library only' : 'In library only'}
                    </button>
                  ) : null}
                </div>
              ) : null}

              <div className={s.episodeList} aria-label="Episodes">
                {(current?.episodes ?? [])
                  .filter((e) => !onlyMine || e.owned !== false)
                  .slice()
                  .sort((a, b) => a.episode_number - b.episode_number)
                  .map((episode) => {
                    const row = progress.data?.[String(episode.id)];
                    return (
                      <EpisodeRow
                        key={episode.id}
                        showId={id}
                        showTitle={show.title}
                        showTmdbId={tmdbId}
                        seasonNumber={current?.season_number ?? 1}
                        episode={episode}
                        autoExpand={targetEpisode === episode.episode_number}
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
                        requestMode={showRequestMode}
                      />
                    );
                  })}
              </div>
            </Section>
            <ExtrasVideos videos={extras.data?.videos} />
            <CastRow cast={show.cast} />
            <RelatedDiscoveryRails
              kind="show"
              selfId={id}
              cast={show.cast}
              leadCrew={creators}
              studioOrNetwork={show.network}
              catalog={catalog.data}
            />
            <MoreLikeThis
              extras={extras.data}
              owned={owned}
              fallback={genreMatches}
              selfTmdb={tmdbId}
            />
            <Gallery gallery={extras.data?.gallery} />
            <Comments itemType="show" itemId={String(id)} title={show.title} />
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
