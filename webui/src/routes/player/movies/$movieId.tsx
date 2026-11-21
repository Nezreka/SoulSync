import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import { runtime, shortDate } from '../-lib/format';
import { backdropUrl, movieCard, moreLikeThis, posterUrl, proxiedImage } from '../-lib/rails';
import { rankVersions, resolutionLabel } from '../-lib/video';
import {
  movieDetailQuery,
  movieFilesQuery,
  progressLookupQuery,
  videoCatalogQuery,
} from '../-player.api';
import { Badge, CardRail, Hero, PlayButton, VerdictBadge, styles as s } from '../-ui/kit';
import { PeopleLinks, StudioLink, VideoGenreLinks } from '../-ui/links';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { BackgroundTrailer, TrailerButton } from '../-ui/trailer';
import { Facts, fileLanguages, languageList, Ratings } from '../-ui/video-facts';
import { CastRow, VersionPicker } from '../-ui/video-parts';

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

  if (!sections.movies) return <NotPartOfProfile what="Movies" />;
  if (detail.isLoading)
    return <div className={s.skeleton} style={{ height: '60vh', marginTop: '2rem' }} />;
  const m = detail.data;
  if (!m || detail.isError) return <NotFound what="Movie" />;

  const best = rankVersions(files.data?.files ?? [])[0];
  const resume = saved.data?.[String(id)];
  const resuming = !!resume && !resume.finished && resume.position_seconds > 0;
  const director = m.crew?.filter((c) => c.job === 'Director');
  const writers = m.crew?.filter(
    (c) => c.job === 'Screenplay' || c.job === 'Writer' || c.job === 'Story',
  );
  const seconds = best?.runtime_seconds ?? (m.runtime_minutes ? m.runtime_minutes * 60 : null);
  const langs = fileLanguages(best);
  const similar = moreLikeThis(catalog.data?.movies ?? [], { id, genres: m.genres ?? [] }).map(
    movieCard,
  );
  const ends = seconds ? new Date(Date.now() + seconds * 1000) : null;

  return (
    <>
      <Hero
        backdrop={m.has_backdrop !== false ? backdropUrl('movie', id) : null}
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
              label={resuming ? `Resume from ${runtime(resume.position_seconds)}` : 'Play'}
            />
            {resuming ? <PlayButton label="From the start" primary={false} /> : null}
            <TrailerButton kind="movie" id={id} />
            {ends ? (
              <span className={s.notYet}>
                Ends at {ends.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
              </span>
            ) : null}
          </>
        }
      />

      {m.tagline ? <p className={s.tagline}>“{m.tagline}”</p> : null}

      <div className={s.twoCol}>
        <div className={s.stack}>
          {files.data?.files?.length ? (
            <VersionPicker files={files.data.files} />
          ) : files.isLoading ? null : (
            <div className={s.notYet}>No file details for this film yet.</div>
          )}
        </div>
        <Facts
          rows={[
            ['Directed by', director?.length ? <PeopleLinks people={director} /> : null],
            ['Written by', writers?.length ? <PeopleLinks people={writers.slice(0, 3)} /> : null],
            ['Studio', m.studio ? <StudioLink studio={m.studio} /> : null],
            ['Released', shortDate(m.release_date)],
            ['Streaming since', shortDate(m.digital_release_date)],
            ['Audio', langs.audio],
            ['Subtitles', langs.subs || languageList(m.subtitle_langs)],
            ['Awards', m.awards],
            [
              'After the credits',
              m.mediastinger ? 'Stay for a scene during or after the credits' : null,
            ],
          ]}
        />
      </div>

      <CastRow cast={m.cast} />
      <CardRail title="More like this" cards={similar} />
    </>
  );
}
