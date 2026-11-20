import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import { plainText, remaining, runtime, shortDate } from '../-lib/format';
import { episodeKey, fraction } from '../-lib/rails';
import { podcastShowQuery, podcastSubsQuery, progressLookupQuery } from '../-player.api';
import { Art, Badge, Hero, PlayButton, styles as s } from '../-ui/kit';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/podcasts/$podcastId')({
  component: PodcastPage,
});

const PAGE = 40;

function PodcastPage() {
  const sections = useSections();
  const id = Number(Route.useParams().podcastId);
  const subs = useQuery({ ...podcastSubsQuery(), enabled: sections.podcasts });
  const sub = subs.data?.find((p) => p.id === id);
  const show = useQuery({
    ...podcastShowQuery(sub?.feed_url ?? ''),
    enabled: !!sub?.feed_url,
    retry: false,
  });
  const [shown, setShown] = useState(PAGE);

  const episodes = useMemo(() => show.data?.episodes ?? [], [show.data]);
  const visible = episodes.slice(0, shown);
  const keys = visible.map(episodeKey);
  const progress = useQuery({
    ...progressLookupQuery('podcast_episode', keys),
    enabled: keys.length > 0,
  });

  if (!sections.podcasts) return <NotPartOfProfile what="Podcasts" />;
  if (subs.isLoading)
    return <div className={s.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  if (!sub) return <NotFound what="Podcast" />;

  const art = show.data?.artwork_url ?? sub.artwork_url;
  const about = plainText(show.data?.description ?? sub.description);

  return (
    <>
      <Hero
        backdrop={art}
        poster={art}
        posterShape="square"
        kicker="Podcast"
        title={show.data?.title ?? sub.title}
        meta={
          <>
            {show.data?.author || sub.author ? (
              <span>{show.data?.author ?? sub.author}</span>
            ) : null}
            {episodes.length ? <span>{episodes.length.toLocaleString()} episodes</span> : null}
            {(show.data?.categories ?? []).slice(0, 2).map((c) => (
              <span key={c}>{c}</span>
            ))}
          </>
        }
        overview={about}
        actions={<PlayButton label="Play latest" />}
      />

      {show.isLoading ? (
        <div className={s.skeleton} style={{ height: '30vh', marginTop: '1rem' }} />
      ) : null}
      {show.isError ? (
        <div className={s.rail}>
          <div className={s.notYet}>Could not reach this podcast's feed right now.</div>
        </div>
      ) : null}

      <div className={s.list} aria-label="Episodes">
        {visible.map((episode) => {
          const key = episodeKey(episode);
          const row = progress.data?.[key];
          const done = row
            ? row.finished
              ? 1
              : fraction(row.position_seconds, row.duration_seconds ?? episode.duration_seconds)
            : undefined;
          return (
            <div key={key} className={s.row} data-media="true" tabIndex={0} data-nav>
              <div className={s.rowArt} style={{ aspectRatio: '1', width: '5.5em' }}>
                <Art src={episode.artwork_url ?? art} alt="" />
                {done !== undefined ? (
                  <div className={s.rowBar}>
                    <div className={s.progressFill} style={{ width: `${done * 100}%` }} />
                  </div>
                ) : null}
              </div>
              <div className={s.rowMain}>
                <span className={s.rowTitle}>{episode.title}</span>
                <span className={s.rowSub}>
                  {[
                    shortDate(episode.pub_date),
                    row?.finished
                      ? 'played'
                      : row && row.position_seconds > 0
                        ? remaining(
                            row.position_seconds,
                            row.duration_seconds ?? episode.duration_seconds,
                          )
                        : runtime(episode.duration_seconds),
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
                {episode.description ? (
                  <span className={s.rowText}>{plainText(episode.description)}</span>
                ) : null}
              </div>
              <div className={s.rowEnd}>
                {episode.downloaded ? <Badge tone="yes">Downloaded</Badge> : null}
              </div>
            </div>
          );
        })}
      </div>
      {episodes.length > shown ? (
        <div className={s.rail}>
          <button
            type="button"
            className={s.btn}
            onClick={() => setShown((n) => n + PAGE)}
            data-nav
          >
            Show more episodes
          </button>
        </div>
      ) : null}
    </>
  );
}
