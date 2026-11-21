/**
 * watching now: what others on this server are playing right now (the
 * ones who share; it's on by default and off in player settings), and
 * what you and they have both started lately. join them where they are,
 * start it yourself, or open its page.
 */

import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import type { PlayItem } from './-engine/types';
import type { TogetherTitle, WatchingSession } from './-player.api';
import type { ProgressMeta } from './-player.types';

import { clock } from './-engine/timeline';
import { MEDIA_KIND, type ItemType } from './-engine/types';
import { remoteImage } from './-lib/images';
import { playerSettingsQuery, togetherQuery, watchingNowQuery } from './-player.api';
import { PlayIcon } from './-ui/icons';
import { Empty, PageTitle, PlayerLink, styles as k } from './-ui/kit';
import { channelByIdTune } from './-ui/playback/tuner';
import { playItems } from './-ui/playback/use-player';
import s from './-ui/social.module.css';

export const Route = createFileRoute('/player/watching')({
  component: WatchingPage,
});

const PLAYABLE = new Set<ItemType>(['track', 'movie', 'episode', 'youtube', 'audiobook']);

function itemOf(type: string, id: string, meta: ProgressMeta): PlayItem | null {
  if (!PLAYABLE.has(type as ItemType)) return null;
  return {
    type: type as ItemType,
    id,
    title: meta.title ?? 'Untitled',
    subtitle: meta.subtitle,
    artwork: remoteImage(meta.artwork) ?? null,
    backdrop: remoteImage(meta.backdrop) ?? null,
    href: meta.href,
    parentId: meta.parent_id,
    parentTitle: meta.parent_title,
  };
}

function Avatar({ profile }: { profile: WatchingSession['profile'] }) {
  return (
    <span
      className={s.avatar}
      style={profile.avatar_color ? { background: profile.avatar_color } : undefined}
      aria-hidden="true"
    >
      {profile.avatar_url ? (
        <img src={remoteImage(profile.avatar_url) ?? undefined} alt="" />
      ) : (
        profile.name.charAt(0).toUpperCase()
      )}
    </span>
  );
}

function verb(type: string) {
  return MEDIA_KIND[type as ItemType] === 'audio' ? 'Listening' : 'Watching';
}

function NowCard({ x }: { x: WatchingSession }) {
  const item = itemOf(x.item_type, x.item_id, x.meta);
  const art = remoteImage(x.meta.backdrop) ?? remoteImage(x.meta.artwork);
  const part = x.duration ? Math.min(1, x.position / x.duration) : null;
  const join = () => {
    if (x.live && x.channel && channelByIdTune(x.channel.id)) return;
    if (item) playItems([item], 0, { startAt: x.position });
  };
  return (
    <article className={s.card} data-playing={x.playing ? 'true' : undefined}>
      <div className={s.cardArt}>
        {art ? <img src={art} alt="" loading="lazy" /> : null}
        <span className={s.cardState}>
          <span className={s.dot} aria-hidden="true" />
          {x.live && x.channel
            ? `Channel ${x.channel.number ?? ''} · ${x.channel.name}`
            : x.playing
              ? verb(x.item_type)
              : 'Paused'}
        </span>
        {part !== null ? (
          <span className={s.cardBar}>
            <span style={{ width: `${part * 100}%` }} />
          </span>
        ) : null}
      </div>
      <div className={s.cardBody}>
        <div className={s.who}>
          <Avatar profile={x.profile} />
          <span className={s.whoName}>{x.profile.name}</span>
        </div>
        <h3 className={s.cardTitle}>{x.meta.title ?? 'Something'}</h3>
        {x.meta.subtitle ? <p className={s.cardSub}>{x.meta.subtitle}</p> : null}
        <div className={s.actions}>
          {item ? (
            <button type="button" className={k.btnPrimary} onClick={join} data-nav>
              <PlayIcon />
              {x.live && x.channel ? 'Tune in' : `Join at ${clock(x.position)}`}
            </button>
          ) : null}
          {item && !x.live ? (
            <button type="button" className={k.btn} onClick={() => playItems([item])} data-nav>
              Start it
            </button>
          ) : null}
          {x.meta.href ? (
            <PlayerLink href={x.meta.href} className={k.btn} data-nav>
              Details
            </PlayerLink>
          ) : null}
        </div>
      </div>
    </article>
  );
}

function place(p: {
  position: number;
  duration: number | null;
  finished: boolean;
  meta: ProgressMeta;
}) {
  if (p.finished) return 'finished';
  const where = p.meta.subtitle && p.meta.parent_id ? p.meta.subtitle.split(' · ').pop() : null;
  const pct = p.duration ? ` · ${Math.round((p.position / p.duration) * 100)}%` : '';
  return `${where ?? clock(p.position)}${pct}`;
}

function TogetherRow({ t }: { t: TogetherTitle }) {
  const me = t.mine;
  const art = remoteImage(me.meta.artwork) ?? remoteImage(me.meta.backdrop);
  const title = me.meta.parent_title ?? me.meta.title ?? 'Something';
  const item = itemOf(me.item_type, me.item_id, me.meta);
  return (
    <li className={s.together}>
      <span className={s.togetherArt}>{art ? <img src={art} alt="" loading="lazy" /> : null}</span>
      <span className={s.togetherText}>
        {me.meta.href ? (
          <PlayerLink href={me.meta.href} className={s.togetherTitle} data-nav>
            {title}
          </PlayerLink>
        ) : (
          <span className={s.togetherTitle}>{title}</span>
        )}
        <span className={s.togetherWho}>
          <span>You: {place(me)}</span>
          {t.others.map((o) => (
            <span key={o.profile_id} className={s.togetherOther}>
              <Avatar profile={o.profile} />
              {o.profile.name}: {place(o)}
            </span>
          ))}
        </span>
      </span>
      {item && !me.finished ? (
        <button
          type="button"
          className={s.round}
          aria-label={`Keep going with ${title}`}
          title="Keep going"
          onClick={() => playItems([item])}
          data-nav
        >
          <PlayIcon />
        </button>
      ) : null}
    </li>
  );
}

function WatchingPage() {
  const now = useQuery(watchingNowQuery());
  const together = useQuery(togetherQuery());
  const settings = useQuery(playerSettingsQuery());
  const sessions = now.data?.sessions ?? [];
  const sharing = settings.data?.settings.share_activity ?? true;

  return (
    <>
      <PageTitle>Watching now</PageTitle>
      {!sharing ? (
        <p className={s.note}>
          You aren’t sharing what you play, so others don’t see you here.{' '}
          <PlayerLink href="/player/settings" data-nav>
            Player settings
          </PlayerLink>
        </p>
      ) : null}

      <section className={k.rail} aria-label="Right now">
        <div className={k.railHead}>
          <h2 className={k.sectionTitle}>Right now</h2>
          {sessions.length ? (
            <span className={k.railMore}>
              {sessions.length} {sessions.length === 1 ? 'screen' : 'screens'}
            </span>
          ) : null}
        </div>
        {now.isLoading ? (
          <div className={k.skeleton} style={{ height: '14rem' }} />
        ) : sessions.length ? (
          <div className={s.grid}>
            {sessions.map((x) => (
              <NowCard key={`${x.profile_id}:${x.item_type}:${x.item_id}:${x.started}`} x={x} />
            ))}
          </div>
        ) : (
          <Empty title="Nobody else is playing anything">
            When someone on this server watches or listens, it shows up here, live.
          </Empty>
        )}
      </section>

      {together.data?.length ? (
        <section className={k.rail} aria-label="You both started">
          <div className={k.railHead}>
            <h2 className={k.sectionTitle}>You both started</h2>
            <span className={k.railMore}>the last two months</span>
          </div>
          <ul className={s.togetherList}>
            {together.data.map((t) => (
              <TogetherRow key={t.key} t={t} />
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
