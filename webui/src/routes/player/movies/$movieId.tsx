import { useQuery } from '@tanstack/react-query';
import { Link, createFileRoute } from '@tanstack/react-router';
import { useState } from 'react';

import { runtime, shortDate } from '../-lib/format';
import { backdropSet } from '../-lib/images';
import { movieItem } from '../-lib/play-items';
import { backdropUrl, moreLikeThis, posterUrl, proxiedImage } from '../-lib/rails';
import { rankVersions, resolutionLabel } from '../-lib/video';
import {
  extrasQuery,
  movieDetailQuery,
  movieFilesQuery,
  progressLookupQuery,
  videoCatalogQuery,
} from '../-player.api';
import { Comments } from '../-ui/comments';
import {
  CollectionRail,
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
import { DownloadButton } from '../-ui/download-button';
import { Badge, Hero, PlayButton, VerdictBadge, styles as s } from '../-ui/kit';
import { CrewList, mergeCrew, StudioLink, VideoGenreLinks } from '../-ui/links';
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
  money,
  Ratings,
} from '../-ui/video-facts';
import { CastRow, VersionPicker } from '../-ui/video-parts';
import { WatchlistButton } from '../-ui/watchlist';

export const Route = createFileRoute('/player/movies/$movieId')({
  component: MoviePage,
});

function MoviePage() {
  const sections = useSections();
  const id = Number(Route.useParams().movieId);
  const ok = sections.movies && Number.isFinite(id);
  const detail = useQuery({ ...movieDetailQuery(id), enabled: ok, retry: false });
  const files = useQuery({ ...movieFilesQuery(id), enabled: ok, retry: false });
  const saved = useQuery({ ...progressLookupQuery('movie', [String(id)]), enabled: ok });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: ok });
  const extras = useQuery({ ...extrasQuery('movie', id), enabled: ok });
  // the version play uses: the one picked, else the best
  const [version, setVersion] = useState<number | null>(null);

  if (!sections.movies) return <NotPartOfProfile what="Movies" />;
  if (detail.isLoading)
    return <div className={s.skeleton} style={{ height: '60vh', marginTop: '2rem' }} />;
  const m = detail.data;
  if (!m || detail.isError) return <NotFound what="Movie" />;

  const best = rankVersions(files.data?.files ?? [])[0];
  const playFile = version ?? best?.id ?? null;
  const resume = saved.data?.[String(id)];
  const resuming = !!resume && !resume.finished && resume.position_seconds > 0;
  const director = m.crew?.filter((c) => c.job === 'Director');
  const writers = m.crew?.filter(
    (c) => c.job === 'Screenplay' || c.job === 'Writer' || c.job === 'Story',
  );
  const crew = mergeCrew([...(director ?? []), ...(writers ?? []).slice(0, 4)]).slice(0, 6);
  const seconds = best?.runtime_seconds ?? (m.runtime_minutes ? m.runtime_minutes * 60 : null);
  const langs = fileLanguages(best);
  const subs = langs.subs.length ? langs.subs : languageNames(m.subtitle_langs);
  const tmdbId = catalog.data?.movies.find((x) => x.id === id)?.tmdb_id ?? null;
  const owned = ownedByTmdb(catalog.data?.movies ?? []);
  const genreMatches = moreLikeThis(catalog.data?.movies ?? [], { id, genres: m.genres ?? [] });
  const ends = seconds ? new Date(Date.now() + seconds * 1000) : null;

  return (
    <>
      <Hero
        backdrop={m.has_backdrop !== false ? backdropUrl('movie', id) : null}
        backdropSrcSet={backdropSet('movie', id).srcSet}
        poster={m.has_poster !== false ? posterUrl('movie', id, 500) : null}
        title={m.title}
        trailer={<BackgroundTrailer kind="movie" id={id} />}
        logo={m.logo ? proxiedImage(m.logo) : null}
        meta={
          <>
            {m.year ? <span>{m.year}</span> : null}
            {seconds ? <span>{runtime(seconds)}</span> : null}
            <VideoGenreLinks kind="movies" genres={(m.genres ?? []).slice(0, 3)} />
          </>
        }
        badges={
          <>
            <div className={s.badges}>
              {m.content_rating ? <Badge>{m.content_rating}</Badge> : null}
              {best?.resolution ? <Badge>{resolutionLabel(best.resolution)}</Badge> : null}
              {best?.dynamic_range ? <Badge>{best.dynamic_range}</Badge> : null}
              {best?.atmos ? <Badge>Atmos</Badge> : null}
              {best?.playable ? (
                <VerdictBadge verdict={best.playable.verdict} reasons={best.playable.reasons} />
              ) : null}
              {m.watched ? <Badge tone="yes">Watched</Badge> : null}
            </div>
            <Ratings d={m} />
          </>
        }
        overview={m.overview}
        actions={
          <>
            <PlayButton
              label={resuming ? `Resume · ${runtime(resume.position_seconds)}` : 'Play'}
              onPlay={best ? () => playItems([movieItem(m, playFile)]) : undefined}
              why="There's no file for this yet."
            />
            {resuming && best ? (
              <PlayButton
                label="Restart"
                primary={false}
                onPlay={() => playItems([movieItem(m, playFile)], 0, { startAt: 0 })}
              />
            ) : null}
            {best ? (
              <Link
                to="/player/watching"
                search={{
                  start: `movie:${id}`,
                  title: m.title,
                  artwork: m.has_poster ? `/api/video/poster/movie/${id}` : undefined,
                  backdrop: m.has_backdrop ? `/api/video/backdrop/movie/${id}` : undefined,
                }}
                className={s.btn}
                title="Start a Watch Party for this movie"
                data-nav
              >
                🍿 Watch Party
              </Link>
            ) : null}
            <WatchlistButton kind="movie" id={id} />
            <TrailerButton kind="movie" id={id} />
            {best ? <DownloadButton type="movie" id={String(id)} title={m.title} /> : null}
            <ReportIssueButton entityType="movie" entityId={id} title={m.title} />
            {ends ? (
              <span className={s.notYet}>
                Ends at {ends.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
              </span>
            ) : null}
          </>
        }
      />

      <DetailLayout
        main={
          <>
            {m.tagline || extras.data?.keywords?.length ? (
              <Section title="About">
                {m.tagline ? <p className={s.tagline}>“{m.tagline}”</p> : null}
                <Keywords words={extras.data?.keywords} />
              </Section>
            ) : null}
            <ExtrasVideos videos={extras.data?.videos} />
            <CastRow cast={m.cast} />
            <CollectionRail collection={extras.data?.collection} owned={owned} selfTmdb={tmdbId} />
            <RelatedDiscoveryRails
              kind="movie"
              selfId={id}
              cast={m.cast}
              leadCrew={director}
              studioOrNetwork={m.studio}
              catalog={catalog.data}
              franchiseCollectionName={extras.data?.collection?.name}
            />
            <MoreLikeThis
              extras={extras.data}
              owned={owned}
              fallback={genreMatches}
              selfTmdb={tmdbId}
            />
            <Gallery gallery={extras.data?.gallery} />
            <Comments itemType="movie" itemId={String(id)} title={m.title} />
          </>
        }
        side={
          <>
            {files.data?.files?.length ? (
              <InfoPanel title={files.data.files.length > 1 ? 'Your copies' : 'Your copy'}>
                <VersionPicker
                  files={files.data.files}
                  bare
                  value={version}
                  onChange={setVersion}
                />
              </InfoPanel>
            ) : null}
            {crew.length ? (
              <InfoPanel title="Crew">
                <CrewList people={crew} />
              </InfoPanel>
            ) : null}
            <InfoPanel title="Details">
              <Facts
                rows={[
                  ['Studio', m.studio ? <StudioLink studio={m.studio} /> : null],
                  ['Released', shortDate(m.release_date)],
                  ['Streaming since', shortDate(m.digital_release_date)],
                  ['Runtime', seconds ? runtime(seconds) : null],
                  ['Rated', m.content_rating],
                  ['Country', extras.data?.facts?.countries?.join(', ')],
                  [
                    'Language',
                    extras.data?.facts?.original_language
                      ? languageName(extras.data.facts.original_language)
                      : null,
                  ],
                  ['Budget', money(extras.data?.facts?.budget)],
                  ['Box office', money(extras.data?.facts?.revenue)],
                  ['Awards', meaningfulAwards(m.awards)],
                  [
                    'After the credits',
                    m.mediastinger ? 'Stay for a scene during or after the credits' : null,
                  ],
                ]}
              />
            </InfoPanel>
            {langs.audio.length || subs.length ? (
              <InfoPanel title="Languages">
                <Facts
                  rows={[
                    ['Audio', langs.audio.length ? <LanguageSummary names={langs.audio} /> : null],
                    ['Subtitles', subs.length ? <LanguageSummary names={subs} /> : null],
                  ]}
                />
              </InfoPanel>
            ) : null}
          </>
        }
      />
    </>
  );
}
