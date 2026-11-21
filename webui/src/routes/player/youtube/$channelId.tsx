import { useQuery } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';

import { compactCount, plainText } from '../-lib/format';
import { proxiedImage, ytVideoCard } from '../-lib/rails';
import { youtubeChannelQuery } from '../-player.api';
import { Badge, CardGrid, Empty, Hero, PlayButton, Tabs, styles as s } from '../-ui/kit';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

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
  const art = proxiedImage(channel.banner) ?? proxiedImage(videos[0]?.thumbnail);

  return (
    <>
      <Hero
        backdrop={art}
        poster={proxiedImage(channel.avatar)}
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
        overview={plainText(channel.description)}
        actions={
          <>
            <PlayButton label="Play newest" />
            <PlayButton label="Shuffle" primary={false} />
          </>
        }
      />

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
              show === 'downloaded' ? 'Nothing downloaded from this channel' : 'No videos here'
            }
          >
            {show === 'downloaded'
              ? 'Downloads you make from this channel in SoulSync show up here.'
              : 'SoulSync hasn’t listed this channel’s videos yet.'}
          </Empty>
        )}
      </div>
    </>
  );
}
