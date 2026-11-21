import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';

import { compactCount, plainText } from '../-lib/format';
import { remoteImage, youtubeThumb } from '../-lib/images';
import { ytVideoCard } from '../-lib/rails';
import { youtubeChannelQuery } from '../-player.api';
import { DetailLayout, InfoPanel, Section } from '../-ui/detail';
import { Badge, CardGrid, CardRail, Empty, Hero, PlayButton, Tabs, styles as s } from '../-ui/kit';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';
import { Facts } from '../-ui/video-facts';

type ChannelTab = 'all' | 'downloaded' | 'embed';

export const Route = createFileRoute('/player/youtube/$channelId')({
  validateSearch: (search: Record<string, unknown>): { show?: ChannelTab } =>
    search.show === 'downloaded' || search.show === 'embed' || search.show === 'all'
      ? { show: search.show }
      : {},
  component: ChannelPage,
});

function ChannelPage() {
  const sections = useSections();
  const { channelId } = Route.useParams();
  const { show = 'all' } = Route.useSearch();
  const navigate = useNavigate({ from: '/player/youtube/$channelId' });
  const data = useQuery({ ...youtubeChannelQuery(channelId), enabled: sections.video });

  if (!sections.video) return <NotPartOfProfile what="YouTube" single />;
  if (data.isLoading)
    return <div className={s.skeleton} style={{ height: '60vh', marginTop: '2rem' }} />;
  if (!data.data || data.isError) return <NotFound what="Channel" />;

  const { channel, videos } = data.data;
  const downloaded = videos.filter((v) => v.downloaded);
  const shown =
    show === 'downloaded'
      ? downloaded
      : show === 'embed'
        ? videos.filter((v) => !v.downloaded)
        : videos;
  const about = plainText(channel.description);
  const latest = videos.filter((v) => v.published_at).slice(0, 16);
  const popular = [...videos]
    .filter((v) => v.views)
    .sort((a, b) => (b.views ?? 0) - (a.views ?? 0))
    .slice(0, 16);
  const art = remoteImage(channel.banner, 'hero') ?? youtubeThumb(videos[0]?.thumbnail, 'hero');

  return (
    <>
      <Hero
        backdrop={art}
        poster={remoteImage(channel.avatar)}
        posterShape="square"
        posterRound
        kicker={channel.handle ?? 'YouTube channel'}
        title={channel.title}
        meta={
          <>
            {channel.subscribers ? (
              <span>{compactCount(channel.subscribers)} subscribers</span>
            ) : null}
            <span>{videos.length.toLocaleString()} videos</span>
            {downloaded.length ? (
              <span>{downloaded.length.toLocaleString()} downloaded</span>
            ) : null}
          </>
        }
        badges={
          channel.followed ? (
            <div className={s.badges}>
              <Badge>Following</Badge>
            </div>
          ) : null
        }
        overview={about ? about.split('\n')[0] : null}
        actions={
          <>
            <PlayButton label="Play newest" />
            <PlayButton label="Shuffle" primary={false} />
          </>
        }
      />

      <DetailLayout
        main={
          <>
            {latest.length > 3 ? (
              <div className={s.detailRail}>
                <CardRail title="Latest" cards={latest.map((v) => ytVideoCard(v, false))} />
              </div>
            ) : null}
            {popular.length > 3 ? (
              <div className={s.detailRail}>
                <CardRail title="Most watched" cards={popular.map((v) => ytVideoCard(v, false))} />
              </div>
            ) : null}
            <Section title="All videos">
              <div className={s.rail}>
                <div className={s.toolbar}>
                  <Tabs
                    label="Show"
                    value={show}
                    options={[
                      { value: 'all', label: `All ${videos.length}` },
                      { value: 'downloaded', label: `Downloaded ${downloaded.length}` },
                      { value: 'embed', label: `Embed ${videos.length - downloaded.length}` },
                    ]}
                    onChange={(next) => void navigate({ search: { show: next }, replace: true })}
                  />
                </div>
                {shown.length ? (
                  <CardGrid cards={shown.map((v) => ytVideoCard(v, false))} shape="wide" />
                ) : (
                  <Empty
                    title={
                      show === 'downloaded'
                        ? 'Nothing downloaded from this channel'
                        : 'No videos here'
                    }
                  >
                    {show === 'downloaded'
                      ? 'Downloads you make from this channel in SoulSync show up here.'
                      : 'SoulSync hasn’t listed this channel’s videos yet.'}
                  </Empty>
                )}
              </div>
            </Section>
          </>
        }
        side={
          <>
            <InfoPanel title="About">
              <Facts
                rows={[
                  ['Handle', channel.handle],
                  ['Subscribers', channel.subscribers ? compactCount(channel.subscribers) : null],
                  ['Videos', videos.length.toLocaleString()],
                  ['Downloaded', downloaded.length ? downloaded.length.toLocaleString() : null],
                  ['Following', channel.followed ? 'Yes' : null],
                ]}
              />
              {about ? <p className={s.aboutText}>{about}</p> : null}
            </InfoPanel>
          </>
        }
      />
    </>
  );
}
