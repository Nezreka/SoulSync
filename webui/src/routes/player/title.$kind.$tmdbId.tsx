import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useState } from 'react';

import { runtime, shortDate } from './-lib/format';
import { remoteImage } from './-lib/images';
import { tmdbTitleQuery, videoCatalogQuery } from './-player.api';
import { DetailLayout, extraCard, InfoPanel, ownedByTmdb } from './-ui/detail';
import { PlayIcon } from './-ui/icons';
import { Badge, CardRail, Hero, styles as s } from './-ui/kit';
import { VideoGenreLinks } from './-ui/links';
import { NotFound, NotPartOfProfile } from './-ui/not-part';
import { RequestButton } from './-ui/request';
import { useSections } from './-ui/sections';
import { VideoDialog } from './-ui/trailer';
import { Facts, meaningfulAwards, Ratings } from './-ui/video-facts';
import { CastRow } from './-ui/video-parts';

export const Route = createFileRoute('/player/title/$kind/$tmdbId')({
  component: TitlePage,
});

/**
 * a title you don't own: what it is (tmdb's detail, as the video side shows
 * it) and the one thing to do about it, ask for it. if it turns out you do
 * own it, this hands you to its library page.
 */
function TitlePage() {
  const sections = useSections();
  const params = Route.useParams();
  const kind = params.kind === 'show' ? 'show' : 'movie';
  const tmdbId = Number(params.tmdbId);
  const navigate = useNavigate();
  const allowed = kind === 'movie' ? sections.movies : sections.tv;
  const detail = useQuery({
    ...tmdbTitleQuery(kind, tmdbId),
    enabled: allowed && Number.isFinite(tmdbId),
  });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: allowed });
  const owned = useMemo(
    () => ownedByTmdb([...(catalog.data?.movies ?? []), ...(catalog.data?.shows ?? [])]),
    [catalog.data],
  );
  const [trailer, setTrailer] = useState(false);

  const redirect = detail.data?.redirect;
  useEffect(() => {
    if (redirect?.id)
      void navigate({
        to: redirect.kind === 'show' ? '/player/tv/$showId' : '/player/movies/$movieId',
        params:
          redirect.kind === 'show'
            ? { showId: String(redirect.id) }
            : { movieId: String(redirect.id) },
        replace: true,
      } as never);
  }, [redirect, navigate]);

  if (!allowed) return <NotPartOfProfile what={kind === 'movie' ? 'Movies' : 'TV shows'} />;
  if (detail.isLoading || redirect)
    return <div className={s.skeleton} style={{ height: '60vh', marginTop: '2rem' }} />;
  const d = detail.data;
  if (!d || detail.isError) return <NotFound what="Title" />;

  const seconds = d.runtime_minutes ? d.runtime_minutes * 60 : null;
  const director = d.crew?.filter((c) => c.job === 'Director').map((c) => c.name);
  const picks = (d.recommendations ?? []).map((t) => extraCard(t, owned));

  return (
    <>
      <Hero
        backdrop={remoteImage(d.backdrop_url, 'hero')}
        poster={remoteImage(d.poster_url, 'hero')}
        kicker="Not in your library"
        title={d.title}
        meta={
          <>
            {d.year ? <span>{d.year}</span> : null}
            {seconds ? <span>{runtime(seconds)}</span> : null}
            {d.season_count ? (
              <span>
                {d.season_count} season{d.season_count === 1 ? '' : 's'}
              </span>
            ) : null}
            <VideoGenreLinks
              kind={kind === 'movie' ? 'movies' : 'tv'}
              genres={(d.genres ?? []).slice(0, 3)}
            />
          </>
        }
        badges={
          <>
            {d.content_rating ? (
              <div className={s.badges}>
                <Badge>{d.content_rating}</Badge>
              </div>
            ) : null}
            <Ratings d={d} />
          </>
        }
        overview={d.overview}
        actions={
          <>
            <RequestButton
              kind={kind}
              tmdbId={tmdbId}
              title={d.title}
              year={d.year}
              poster={d.poster_url}
            />
            {d.trailer?.key ? (
              <button type="button" className={s.btn} onClick={() => setTrailer(true)} data-nav>
                <PlayIcon />
                Trailer
              </button>
            ) : null}
          </>
        }
      />
      {trailer && d.trailer?.key ? (
        <VideoDialog
          videoKey={d.trailer.key}
          title={d.trailer.name ?? d.title}
          onClose={() => setTrailer(false)}
        />
      ) : null}

      <DetailLayout
        main={
          <>
            <CastRow cast={d.cast} />
            {picks.length ? (
              <div className={s.detailRail}>
                <CardRail title="More like this" cards={picks} />
              </div>
            ) : null}
          </>
        }
        side={
          <InfoPanel title="Details">
            <Facts
              rows={[
                ['Directed by', director?.length ? director.join(', ') : null],
                ['Studio', d.studio],
                ['Network', d.network],
                ['Released', shortDate(d.release_date)],
                ['Runtime', seconds ? runtime(seconds) : null],
                ['Rated', d.content_rating],
                ['Awards', meaningfulAwards(d.awards)],
              ]}
            />
          </InfoPanel>
        }
      />
    </>
  );
}
