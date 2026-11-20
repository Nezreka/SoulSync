import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { RailCard } from '../-lib/rails';
import type { CatalogYoutube } from '../-player.types';

import { runtime } from '../-lib/format';
import { proxiedImage } from '../-lib/rails';
import { followedChannelsQuery, videoCatalogQuery } from '../-player.api';
import { Badge, CardRail, Empty, PageTitle, styles as s } from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/youtube/')({
  component: YoutubeBrowse,
});

function videoCard(v: CatalogYoutube): RailCard {
  return {
    key: `y${v.id}`,
    title: v.title,
    subtitle: [runtime(v.duration_seconds), 'Downloaded'].filter(Boolean).join(' · '),
    art: proxiedImage(v.thumbnail_url),
    shape: 'wide',
  };
}

function YoutubeBrowse() {
  const sections = useSections();
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.video });
  const channels = useQuery({ ...followedChannelsQuery(), enabled: sections.video });
  const videos = catalog.data?.youtube ?? [];

  const byChannel = useMemo(() => {
    const map = new Map<string, { title: string; videos: CatalogYoutube[] }>();
    for (const v of videos) {
      const key = v.channel_id ?? 'unknown';
      const entry = map.get(key) ?? { title: v.channel_title ?? 'Other videos', videos: [] };
      entry.videos.push(v);
      map.set(key, entry);
    }
    return [...map.entries()].sort((a, b) => b[1].videos.length - a[1].videos.length);
  }, [videos]);

  if (!sections.video) return <NotPartOfProfile what="YouTube" single />;

  const streamOnly = (channels.data ?? []).filter(
    (c) => (c.video_count ?? 0) > (c.owned_count ?? 0),
  );

  return (
    <>
      <PageTitle>YouTube</PageTitle>
      <div className={s.badges}>
        <Badge tone="yes">Downloaded</Badge>
        <span className={s.notYet}>plays from your library</span>
        <Badge>Embed</Badge>
        <span className={s.notYet}>plays from YouTube itself, once playback lands</span>
      </div>

      {catalog.isLoading ? (
        <div className={s.skeleton} style={{ height: '40vh', marginTop: '2rem' }} />
      ) : null}

      <CardRail title="Latest downloads" cards={videos.slice(0, 24).map(videoCard)} />
      {byChannel.length > 1
        ? byChannel
            .slice(0, 12)
            .map(([key, entry]) => (
              <CardRail
                key={key}
                title={entry.title}
                cards={entry.videos.slice(0, 24).map(videoCard)}
              />
            ))
        : null}

      {streamOnly.length ? (
        <section className={s.rail} aria-label="Channels you follow">
          <div className={s.railHead}>
            <h2 className={s.sectionTitle}>Channels you follow</h2>
          </div>
          <div className={s.list}>
            {streamOnly.map((c) => (
              <div key={c.id} className={s.row} tabIndex={0} data-nav>
                <span className={s.rowNum} />
                <span className={s.rowMain}>
                  <span className={s.rowTitle}>{c.title}</span>
                  <span className={s.rowSub}>
                    {(c.owned_count ?? 0).toLocaleString()} downloaded ·{' '}
                    {((c.video_count ?? 0) - (c.owned_count ?? 0)).toLocaleString()} as embeds
                  </span>
                </span>
                <span className={s.rowEnd}>
                  <Badge>Embed</Badge>
                </span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {catalog.data && !videos.length && !streamOnly.length ? (
        <div className={s.rail}>
          <Empty title="No YouTube videos yet">
            Follow a channel or download a video in SoulSync and it shows up here.
          </Empty>
        </div>
      ) : null}
    </>
  );
}
