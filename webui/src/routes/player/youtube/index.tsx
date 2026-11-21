import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import { ago, clockDuration, compactCount } from '../-lib/format';
import { proxiedImage, ytChannelCard, ytVideoCard } from '../-lib/rails';
import { youtubeHomeQuery } from '../-player.api';
import { Badge, CardRail, Empty, Hero, PlayButton, PlayerLink, styles as s } from '../-ui/kit';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/youtube/')({
  component: YoutubeHome,
});

/** how many channels get a rail of their own below the shared ones */
const CHANNEL_RAILS = 8;

function YoutubeHome() {
  const sections = useSections();
  const home = useQuery({ ...youtubeHomeQuery(), enabled: sections.video });

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
      {lead ? (
        <Hero
          backdrop={proxiedImage(lead.thumbnail)}
          kicker={
            <span className={s.channelKicker}>
              {leadChannel?.avatar ? (
                <img
                  className={s.kickerAvatar}
                  src={proxiedImage(leadChannel.avatar) ?? ''}
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
      ) : (
        <h1 className={s.pageTitle}>YouTube</h1>
      )}

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
  );
}
