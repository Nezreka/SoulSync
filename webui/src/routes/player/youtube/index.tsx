import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { BrowseTab } from '../-lib/video';
import type { YtVideo } from '../-player.types';

import { ago, clockDuration, compactCount } from '../-lib/format';
import { remoteImage, youtubeThumb } from '../-lib/images';
import { ytChannelCard, ytVideoCard } from '../-lib/rails';
import { youtubeHomeQuery } from '../-player.api';
import { BrowseTabs, useContinueWatching } from '../-ui/browse';
import {
  Badge,
  CardGrid,
  CardRail,
  Empty,
  Hero,
  PageTitle,
  PlayButton,
  PlayerLink,
  useViewMode,
  ViewToggle,
  styles as s,
} from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/youtube/')({
  validateSearch: (search: Record<string, unknown>): { tab?: BrowseTab } =>
    search.tab === 'library' || search.tab === 'collections' || search.tab === 'categories'
      ? { tab: search.tab }
      : {},
  component: YoutubeHome,
});

const YT_TABS: { value: BrowseTab; label: string }[] = [
  { value: 'foryou', label: 'For you' },
  { value: 'library', label: 'Downloaded' },
  { value: 'collections', label: 'Channels' },
  { value: 'categories', label: 'Categories' },
];

/** videos cut by how long they take, then by how far they reached */
function categoryRails(videos: YtVideo[]) {
  const seen = new Set<string>();
  const unique = videos.filter((v) => !seen.has(v.id) && seen.add(v.id));
  const length = (v: YtVideo) => v.duration_seconds ?? 0;
  const week = Date.now() - 7 * 86_400_000;
  return [
    {
      title: 'This week',
      videos: unique.filter((v) => v.published_at && Date.parse(v.published_at) >= week),
    },
    {
      title: 'Quick watches · under 10 minutes',
      videos: unique.filter((v) => length(v) > 0 && length(v) < 600),
    },
    {
      title: 'Sit-down watches · 10 to 30 minutes',
      videos: unique.filter((v) => length(v) >= 600 && length(v) < 1800),
    },
    { title: 'Long watches · 30 minutes or more', videos: unique.filter((v) => length(v) >= 1800) },
    {
      title: 'Most viewed',
      videos: unique
        .filter((v) => (v.views ?? 0) > 0)
        .sort((a, b) => (b.views ?? 0) - (a.views ?? 0)),
    },
  ].filter((r) => r.videos.length >= 3);
}

/** how many channels get a rail of their own below the shared ones */
const CHANNEL_RAILS = 8;

function YoutubeHome() {
  const sections = useSections();
  const home = useQuery({ ...youtubeHomeQuery(), enabled: sections.video });
  const tab = Route.useSearch().tab ?? 'foryou';
  const navigate = useNavigate({ from: '/player/youtube/' });
  const continuing = useContinueWatching(['youtube:']);
  const [view, setView] = useViewMode('youtube-downloads');
  const categories = useMemo(
    () => categoryRails([...(home.data?.latest ?? []), ...(home.data?.downloads ?? [])]),
    [home.data],
  );

  if (!sections.video) return <NotPartOfProfile what="YouTube" single />;
  if (home.isLoading)
    return <div className={s.skeleton} style={{ height: '60vh', marginTop: '2rem' }} />;
  if (home.isError)
    return (
      <div className={s.rail}>
        <Empty title="Could not load YouTube">Try again in a moment.</Empty>
      </div>
    );

  const { channels, latest, downloads } = home.data ?? { channels: [], latest: [], downloads: [] };
  const lead = latest[0] ?? downloads[0];
  const leadChannel = channels.find((c) => c.id === lead?.channel_id);
  const byChannel = channels
    .filter((c) => c.followed)
    .slice(0, CHANNEL_RAILS)
    .map((c) => ({ channel: c, videos: latest.filter((v) => v.channel_id === c.id) }))
    .filter((x) => x.videos.length > 1);

  const tabs = (
    <BrowseTabs
      value={tab}
      options={YT_TABS}
      onChange={(next) =>
        void navigate({ search: next === 'foryou' ? {} : { tab: next }, replace: true })
      }
    />
  );
  const body =
    tab === 'library' ? (
      downloads.length ? (
        <div className={s.rail}>
          <div className={s.toolbar}>
            <span className={s.rowSub}>
              {downloads.length} downloaded video{downloads.length === 1 ? '' : 's'}
            </span>
            <ViewToggle value={view} onChange={setView} />
          </div>
          <CardGrid cards={downloads.map((v) => ytVideoCard(v))} shape="wide" view={view} />
        </div>
      ) : (
        <div className={s.rail}>
          <Empty title="Nothing downloaded yet">
            Videos you download from YouTube in SoulSync show up here and play from your library.
          </Empty>
        </div>
      )
    ) : tab === 'collections' ? (
      <div className={s.rail}>
        <CardGrid cards={channels.map(ytChannelCard)} shape="square" round />
      </div>
    ) : (
      <>
        {categories.map((r) => (
          <CardRail
            key={r.title}
            title={r.title}
            cards={r.videos.slice(0, 30).map((v) => ytVideoCard(v))}
          />
        ))}
        {!categories.length ? (
          <div className={s.rail}>
            <Empty title="Not enough videos yet">Categories fill in as your channels post.</Empty>
          </div>
        ) : null}
      </>
    );

  if (!lead && !channels.length)
    return (
      <>
        <h1 className={s.pageTitle}>YouTube</h1>
        <Empty title="No YouTube yet">
          Follow a channel in SoulSync and its newest videos show up here. The ones you download
          play from your library; the rest play from YouTube itself once playback lands.
        </Empty>
      </>
    );

  return (
    <>
      {tab !== 'foryou' ? <PageTitle>YouTube</PageTitle> : null}
      {tab === 'foryou' && lead ? (
        <Hero
          backdrop={youtubeThumb(lead.thumbnail, 'hero')}
          kicker={
            <span className={s.channelKicker}>
              {leadChannel?.avatar ? (
                <img
                  className={s.kickerAvatar}
                  src={remoteImage(leadChannel.avatar) ?? ''}
                  alt=""
                />
              ) : null}
              New from {lead.channel_title ?? 'your channels'}
            </span>
          }
          title={lead.title}
          meta={
            <>
              {lead.views ? <span>{compactCount(lead.views)} views</span> : null}
              {lead.published_at ? <span>{ago(lead.published_at)}</span> : null}
              {lead.duration_seconds ? <span>{clockDuration(lead.duration_seconds)}</span> : null}
            </>
          }
          badges={
            <div className={s.badges}>
              {lead.downloaded ? (
                <Badge tone="yes">Downloaded · plays from your library</Badge>
              ) : (
                <Badge>Embed · plays from YouTube</Badge>
              )}
            </div>
          }
          actions={
            <>
              <PlayButton />
              {lead.channel_id ? (
                <PlayerLink
                  href={`/player/youtube/${encodeURIComponent(lead.channel_id)}`}
                  className={s.btn}
                  data-nav
                >
                  Open channel
                </PlayerLink>
              ) : null}
            </>
          }
        />
      ) : tab === 'foryou' ? (
        <PageTitle>YouTube</PageTitle>
      ) : null}

      {tabs}
      {tab !== 'foryou' ? body : null}
      {tab === 'foryou' ? <CardRail title="Continue watching" cards={continuing} /> : null}
      {tab === 'foryou' ? (
        <>
          <CardRail
            title="Latest from your channels"
            cards={latest.slice(0, 30).map((v) => ytVideoCard(v))}
          />
          <CardRail title="Your channels" cards={channels.map(ytChannelCard)} round />
          <CardRail title="Downloaded" cards={downloads.slice(0, 30).map((v) => ytVideoCard(v))} />
          {byChannel.map(({ channel, videos }) => (
            <CardRail
              key={channel.id}
              title={channel.title}
              more={{ to: `/player/youtube/${encodeURIComponent(channel.id)}`, label: 'Channel' }}
              cards={videos.map((v) => ytVideoCard(v, false))}
            />
          ))}
        </>
      ) : null}
    </>
  );
}
