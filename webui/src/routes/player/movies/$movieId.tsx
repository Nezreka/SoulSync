import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import { runtime } from '../-lib/format';
import { backdropUrl, posterUrl, proxiedImage } from '../-lib/rails';
import { rankVersions, resolutionLabel } from '../-lib/video';
import { movieDetailQuery, movieFilesQuery, progressLookupQuery } from '../-player.api';
import { Badge, Hero, PlayButton, VerdictBadge, styles as s } from '../-ui/kit';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
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

  if (!sections.movies) return <NotPartOfProfile what="Movies" />;
  if (detail.isLoading)
    return <div className={s.skeleton} style={{ height: '60vh', marginTop: '2rem' }} />;
  const m = detail.data;
  if (!m || detail.isError) return <NotFound what="Movie" />;

  const best = rankVersions(files.data?.files ?? [])[0];
  const resume = saved.data?.[String(id)];
  const director = m.crew?.find((c) => c.job === 'Director')?.name;
  const writers = m.crew
    ?.filter((c) => c.job === 'Screenplay' || c.job === 'Writer')
    .map((c) => c.name);
  const seconds = best?.runtime_seconds ?? (m.runtime_minutes ? m.runtime_minutes * 60 : null);

  return (
    <>
      <Hero
        backdrop={m.has_backdrop !== false ? backdropUrl('movie', id) : null}
        poster={m.has_poster !== false ? posterUrl('movie', id, 500) : null}
        title={m.title}
        logo={m.logo ? proxiedImage(m.logo) : null}
        meta={
          <>
            {m.year ? <span>{m.year}</span> : null}
            {seconds ? <span>{runtime(seconds)}</span> : null}
            {(m.genres ?? []).slice(0, 3).map((g) => (
              <span key={g}>{g}</span>
            ))}
          </>
        }
        badges={
          <div className={s.badges}>
            {m.content_rating ? <Badge>{m.content_rating}</Badge> : null}
            {m.rating ? <Badge>★ {m.rating.toFixed(1)}</Badge> : null}
            {best?.resolution ? <Badge>{resolutionLabel(best.resolution)}</Badge> : null}
            {best?.dynamic_range ? <Badge>{best.dynamic_range}</Badge> : null}
            {best?.playable ? (
              <VerdictBadge verdict={best.playable.verdict} reasons={best.playable.reasons} />
            ) : null}
          </div>
        }
        overview={m.tagline && !m.overview ? m.tagline : m.overview}
        actions={
          <>
            <PlayButton
              label={
                resume && !resume.finished && resume.position_seconds > 0
                  ? `Resume from ${runtime(resume.position_seconds)}`
                  : 'Play'
              }
            />
            {resume && !resume.finished && resume.position_seconds > 0 ? (
              <PlayButton label="From the start" primary={false} />
            ) : null}
          </>
        }
      />

      <div className={s.twoCol}>
        <div>
          {files.data?.files?.length ? (
            <VersionPicker files={files.data.files} />
          ) : files.isLoading ? null : (
            <div className={s.notYet}>No file details for this film yet.</div>
          )}
        </div>
        <dl className={s.facts}>
          {director ? (
            <>
              <dt>Director</dt>
              <dd>{director}</dd>
            </>
          ) : null}
          {writers?.length ? (
            <>
              <dt>Written by</dt>
              <dd>{writers.slice(0, 3).join(', ')}</dd>
            </>
          ) : null}
          {m.studio ? (
            <>
              <dt>Studio</dt>
              <dd>{m.studio}</dd>
            </>
          ) : null}
        </dl>
      </div>

      <CastRow cast={m.cast} />
    </>
  );
}
