/**
 * Watching now & Watch Parties (Watch Together):
 * - Cinema-grade live screen cards for everyone sharing on this server
 * - Theater Waiting Room Watch Parties with synced SoulSync library lobby music,
 *   Ready checkmarks, live chat & reactions, and scheduled premieres
 * - Poster-first "Watching the same thing" rail with one-click Watch Party launch
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import { appURL } from '@/platform/url-base';

import type { PlayItem } from './-engine/types';
import type { TogetherTitle, WatchParty, WatchingSession } from './-player.api';
import type { ProgressMeta } from './-player.types';

import { clock } from './-engine/timeline';
import { MEDIA_KIND, type ItemType } from './-engine/types';
import { remoteImage } from './-lib/images';
import {
  playerSettingsQuery,
  togetherQuery,
  videoCatalogQuery,
  watchPartiesQuery,
  watchPartyApi,
  watchPartyQuery,
  watchingNowQuery,
} from './-player.api';
import { Avatar, nameOf } from './-ui/channel-viewers';
import { PlayIcon } from './-ui/icons';
import { Empty, PageTitle, PlayerLink, styles as k } from './-ui/kit';
import { channelByIdTune } from './-ui/playback/tuner';
import { setActiveWatchPartyId } from './-ui/playback/party-reactions';
import { playItems } from './-ui/playback/use-player';
import s from './-ui/social.module.css';
import wp from './-ui/watch-party.module.css';

export const Route = createFileRoute('/player/watching')({
  component: WatchingPage,
});

const PLAYABLE = new Set<ItemType>([
  'track',
  'movie',
  'episode',
  'youtube',
  'audiobook',
  'podcast_episode',
]);
const QUICK_EMOJIS = ['🍿', '🔥', '😂', '😮', '👏', '❤️'];

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

function verb(type: string) {
  return MEDIA_KIND[type as ItemType] === 'audio' ? 'LISTENING' : 'WATCHING';
}

function countdownText(epochSec: number | null, nowSec: number): string {
  if (!epochSec) return '';
  const diff = Math.max(0, Math.round(epochSec - nowSec));
  if (diff < 60) return 'Starting now';
  const mins = Math.floor(diff / 60);
  if (mins < 60) return `In ${mins}m`;
  const hrs = Math.floor(mins / 60);
  const remM = mins % 60;
  if (hrs < 24) return remM > 0 ? `In ${hrs}h ${remM}m` : `In ${hrs}h`;
  return new Date(epochSec * 1000).toLocaleString([], {
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function place(p: {
  position: number;
  duration: number | null;
  finished: boolean;
  meta: ProgressMeta;
}) {
  if (p.finished) return 'Finished';
  const where = p.meta.subtitle && p.meta.parent_id ? p.meta.subtitle.split(' · ').pop() : null;
  const pct = p.duration ? ` · ${Math.round((p.position / p.duration) * 100)}%` : '';
  return `${where ?? clock(p.position)}${pct}`;
}

// ── Cinema-Grade Watching Now Card ──────────────────────────────────────────

function CinemaNowCard({
  x,
  onStartParty,
}: {
  x: WatchingSession;
  onStartParty: (seed: {
    item_type: 'movie' | 'episode' | 'youtube';
    item_id: string;
    title: string;
    meta: ProgressMeta;
  }) => void;
}) {
  const item = itemOf(x.item_type, x.item_id, x.meta);
  const art = remoteImage(x.meta.backdrop) ?? remoteImage(x.meta.artwork);
  const part = x.duration ? Math.min(1, x.position / x.duration) : null;
  const join = () => {
    if (x.live && x.channel && channelByIdTune(x.channel.id)) return;
    if (item) playItems([item], 0, { startAt: x.position });
  };
  const canParty = x.item_type === 'movie' || x.item_type === 'episode' || x.item_type === 'youtube';

  return (
    <article className={wp.cinemaCard}>
      {art ? <img className={wp.cinemaBg} src={art} alt="" loading="lazy" /> : null}
      <div className={wp.cinemaScrim} />

      <div className={wp.cinemaTop}>
        <span className={wp.livePill}>
          <span
            className={`${wp.liveDot} ${x.playing ? wp.liveDotActive : ''}`}
            aria-hidden="true"
          />
          {x.live && x.channel
            ? `CH ${x.channel.number ?? ''} · ${x.channel.name}`
            : x.playing
              ? verb(x.item_type)
              : 'PAUSED'}
        </span>

        <span className={wp.whoChip}>
          <Avatar profile={x.profile} />
          <span>{nameOf(x)}</span>
        </span>
      </div>

      <div className={wp.cinemaBody}>
        <h3 className={wp.cinemaTitle}>{x.meta.title ?? 'Something'}</h3>
        {x.meta.subtitle ? <p className={wp.cinemaSub}>{x.meta.subtitle}</p> : null}

        {part !== null ? (
          <div className={wp.progressWrap}>
            <div className={wp.progressTrack}>
              <div className={wp.progressFill} style={{ width: `${Math.round(part * 100)}%` }} />
            </div>
            <span className={wp.progressTime}>
              {clock(x.position)} / {clock(x.duration ?? 0)}
            </span>
          </div>
        ) : null}

        <div className={wp.cardActions}>
          {item && !x.you ? (
            <button
              type="button"
              className={`${wp.glassBtn} ${wp.glassBtnPrimary}`}
              onClick={join}
              data-nav
            >
              <PlayIcon />
              {x.live && x.channel ? 'Tune in' : `Join at ${clock(x.position)}`}
            </button>
          ) : null}
          {canParty ? (
            <button
              type="button"
              className={wp.glassBtn}
              onClick={() =>
                onStartParty({
                  item_type: x.item_type as 'movie' | 'episode' | 'youtube',
                  item_id: x.item_id,
                  title: x.meta.title ?? 'Watch Party',
                  meta: x.meta,
                })
              }
              data-nav
            >
              🍿 Watch Together
            </button>
          ) : null}
          {x.meta.href ? (
            <PlayerLink href={x.meta.href} className={wp.glassBtn} data-nav>
              Details
            </PlayerLink>
          ) : null}
        </div>
      </div>
    </article>
  );
}

// ── Watch Party Card (Active Lobby / Playing / Scheduled Premiere) ──────────

function WatchPartyCard({
  party,
  nowSec,
  onOpen,
}: {
  party: WatchParty;
  nowSec: number;
  onOpen: (id: string) => void;
}) {
  const art = remoteImage(party.meta.backdrop) ?? remoteImage(party.meta.artwork);
  const statusLabel =
    party.status === 'playing'
      ? `LIVE · ${clock(party.live_position)}`
      : party.status === 'paused'
        ? `PAUSED · ${clock(party.live_position)}`
        : party.status === 'scheduled'
          ? `SCHEDULED · ${countdownText(party.scheduled_for, nowSec)}`
          : `LOBBY OPEN · ${party.ready_count}/${party.member_count} READY`;

  return (
    <article className={wp.cinemaCard} style={{ cursor: 'pointer' }} onClick={() => onOpen(party.id)}>
      {art ? <img className={wp.cinemaBg} src={art} alt="" loading="lazy" /> : null}
      <div className={wp.cinemaScrim} />

      <div className={wp.cinemaTop}>
        <span className={wp.livePill}>
          <span
            className={`${wp.liveDot} ${party.status === 'playing' ? wp.liveDotActive : wp.liveDotLobby}`}
            aria-hidden="true"
          />
          {statusLabel}
        </span>

        <span className={wp.whoChip}>
          <Avatar profile={party.host_profile} />
          <span>
            {party.member_count} {party.member_count === 1 ? 'viewer' : 'viewers'}
          </span>
        </span>
      </div>

      <div className={wp.cinemaBody}>
        <h3 className={wp.cinemaTitle}>{party.title}</h3>
        <p className={wp.cinemaSub}>
          Hosted by {party.host_profile?.name ?? 'Host'}
          {party.meta.subtitle ? ` · ${party.meta.subtitle}` : ''}
        </p>

        {party.lobby_track && (party.status === 'lobby' || party.status === 'scheduled') ? (
          <div className={wp.musicTicker}>
            <span>♪</span>
            <span>
              Lobby music: {party.lobby_track.title}
              {party.lobby_track.artist ? ` — ${party.lobby_track.artist}` : ''}
            </span>
          </div>
        ) : null}

        <div className={wp.cardActions}>
          <button
            type="button"
            className={`${wp.glassBtn} ${wp.glassBtnPrimary}`}
            onClick={(e) => {
              e.stopPropagation();
              onOpen(party.id);
            }}
          >
            <PlayIcon />
            {party.status === 'playing'
              ? 'Join & Sync'
              : party.status === 'scheduled'
                ? party.is_member
                  ? 'Open Room'
                  : 'RSVP / Enter'
                : 'Enter Theater Lobby'}
          </button>
        </div>
      </div>
    </article>
  );
}

// ── Shared Title Card ("Watching the Same Thing") ───────────────────────────

function SharedTitleCard({
  t,
  onStartParty,
}: {
  t: TogetherTitle;
  onStartParty: (seed: {
    item_type: 'movie' | 'episode' | 'youtube';
    item_id: string;
    title: string;
    meta: ProgressMeta;
  }) => void;
}) {
  const me = t.mine;
  const art = remoteImage(me.meta.artwork) ?? remoteImage(me.meta.backdrop);
  const title = me.meta.parent_title ?? me.meta.title ?? 'Something';
  const item = itemOf(me.item_type, me.item_id, me.meta);
  const canParty = me.item_type === 'movie' || me.item_type === 'episode' || me.item_type === 'youtube';

  return (
    <article className={wp.sharedCard}>
      <div className={wp.sharedPoster}>{art ? <img src={art} alt="" loading="lazy" /> : null}</div>
      <div className={wp.sharedInfo}>
        {me.meta.href ? (
          <PlayerLink href={me.meta.href} className={wp.sharedTitle} data-nav>
            {title}
          </PlayerLink>
        ) : (
          <span className={wp.sharedTitle}>{title}</span>
        )}
        <div className={wp.sharedMeta}>
          <span>You: {place(me)}</span>
          {t.others.map((o) => (
            <span key={o.profile_id} className={s.togetherOther}>
              <Avatar profile={o.profile} />
              {o.profile.name}: {place(o)}
            </span>
          ))}
        </div>
        <div className={wp.cardActions} style={{ marginTop: 4 }}>
          {item && !me.finished ? (
            <button
              type="button"
              className={`${wp.glassBtn} ${wp.glassBtnPrimary}`}
              onClick={() => playItems([item])}
            >
              <PlayIcon />
              Resume
            </button>
          ) : null}
          {canParty ? (
            <button
              type="button"
              className={wp.glassBtn}
              onClick={() =>
                onStartParty({
                  item_type: me.item_type as 'movie' | 'episode' | 'youtube',
                  item_id: me.item_id,
                  title,
                  meta: me.meta,
                })
              }
            >
              🍿 Watch Party
            </button>
          ) : null}
        </div>
      </div>
    </article>
  );
}

// ── Create Watch Party Modal ────────────────────────────────────────────────

interface PartySeed {
  item_type: 'movie' | 'episode' | 'youtube';
  item_id: string;
  title: string;
  meta: ProgressMeta;
  show_id?: number | null;
}

function CreatePartyModal({
  initialSeed,
  onClose,
  onCreated,
}: {
  initialSeed: PartySeed | null;
  onClose: () => void;
  onCreated: (party: WatchParty) => void;
}) {
  const catalog = useQuery(videoCatalogQuery());
  const [search, setSearch] = useState('');
  const [picked, setPicked] = useState<PartySeed | null>(initialSeed);
  const [roomTitle, setRoomTitle] = useState(initialSeed?.title ?? '');
  const [mode, setMode] = useState<'now' | 'schedule'>('now');
  const [scheduleInput, setScheduleInput] = useState(() => {
    const d = new Date(Date.now() + 60 * 60 * 1000);
    d.setMinutes(0, 0, 0);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  });
  const [controlMode, setControlMode] = useState<'host' | 'everyone'>('host');
  const [lobbyMusic, setLobbyMusic] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const candidates = useMemo(() => {
    const cat = catalog.data;
    if (!cat) return [] as PartySeed[];
    const q = search.trim().toLowerCase();
    const out: PartySeed[] = [];

    for (const m of cat.movies) {
      if (q && !m.title.toLowerCase().includes(q)) continue;
      out.push({
        item_type: 'movie',
        item_id: String(m.id),
        title: m.title,
        meta: {
          title: m.title,
          subtitle: m.year ? String(m.year) : undefined,
          artwork: m.has_poster ? `/api/video/artwork/movie/${m.id}/poster` : undefined,
          backdrop: m.has_backdrop ? `/api/video/artwork/movie/${m.id}/backdrop` : undefined,
          href: `/player/movies/${m.id}`,
        },
      });
      if (out.length >= 24) break;
    }

    for (const sh of cat.shows) {
      if (q && !sh.title.toLowerCase().includes(q)) continue;
      const latest = sh.episodes?.[sh.episodes.length - 1];
      if (!latest) continue;
      const [epId, season, epNum, , epTitle] = latest;
      out.push({
        item_type: 'episode',
        item_id: String(epId),
        show_id: sh.id,
        title: `${sh.title} · S${season} E${epNum}`,
        meta: {
          title: sh.title,
          subtitle: `S${season} E${epNum}${epTitle ? ` · ${epTitle}` : ''}`,
          artwork: sh.has_poster ? `/api/video/artwork/show/${sh.id}/poster` : undefined,
          backdrop: sh.has_backdrop ? `/api/video/artwork/show/${sh.id}/backdrop` : undefined,
          href: `/player/tv/${sh.id}`,
          parent_id: String(sh.id),
          parent_title: sh.title,
        },
      });
      if (out.length >= 44) break;
    }

    return out;
  }, [catalog.data, search]);

  const createMut = useMutation({
    mutationFn: () => {
      if (!picked) throw new Error('Pick a movie or show first');
      let scheduled_for: number | null = null;
      if (mode === 'schedule') {
        const ms = Date.parse(scheduleInput);
        if (!Number.isFinite(ms)) throw new Error('Pick a valid schedule date & time');
        scheduled_for = Math.floor(ms / 1000);
      }
      return watchPartyApi.create({
        title: roomTitle.trim() || picked.title,
        item_type: picked.item_type,
        item_id: picked.item_id,
        meta: picked.meta,
        show_id: picked.show_id ?? null,
        scheduled_for,
        control_mode: controlMode,
        lobby_music: lobbyMusic,
      });
    },
    onSuccess: (party) => onCreated(party),
    onError: (err: Error) => setError(err.message || 'Could not create watch party'),
  });

  return (
    <div className={wp.backdrop} onClick={onClose}>
      <div className={wp.modal} style={{ maxWidth: 680 }} onClick={(e) => e.stopPropagation()}>
        <div className={wp.modalHero}>
          <div className={wp.modalHeroInner}>
            <div>
              <h2 className={wp.modalTitle}>Start a Watch Party</h2>
              <p className={wp.modalSub}>
                Open a theater lobby with SoulSync library music or schedule a premiere.
              </p>
            </div>
            <button type="button" className={wp.glassBtn} onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        <div className={wp.roomMain} style={{ borderRight: 'none' }}>
          <div>
            <input
              className={wp.input}
              placeholder="Search your movies & shows (shows auto-pick latest episode)…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className={wp.pickerList}>
            {candidates.map((c) => {
              const selected = picked?.item_type === c.item_type && picked?.item_id === c.item_id;
              const thumb = remoteImage(c.meta.artwork);
              return (
                <button
                  key={`${c.item_type}:${c.item_id}`}
                  type="button"
                  className={`${wp.pickerItem} ${selected ? wp.pickerItemActive : ''}`}
                  onClick={() => {
                    setPicked(c);
                    setRoomTitle(c.title);
                  }}
                >
                  {thumb ? <img className={wp.pickerThumb} src={thumb} alt="" /> : null}
                  <div style={{ minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 12.5,
                        fontWeight: 750,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {c.title}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--pl-text-3)' }}>
                      {c.item_type === 'movie' ? 'Movie' : 'Latest Episode'}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          <div>
            <input
              className={wp.input}
              placeholder="Party name (e.g. Friday Night Dune)"
              value={roomTitle}
              onChange={(e) => setRoomTitle(e.target.value)}
            />
          </div>

          <div className={wp.pillToggleRow}>
            <button
              type="button"
              className={`${wp.pillToggle} ${mode === 'now' ? wp.pillToggleOn : ''}`}
              onClick={() => setMode('now')}
            >
              🍿 Open Lobby Now
            </button>
            <button
              type="button"
              className={`${wp.pillToggle} ${mode === 'schedule' ? wp.pillToggleOn : ''}`}
              onClick={() => setMode('schedule')}
            >
              🗓 Schedule Premiere
            </button>
          </div>

          {mode === 'schedule' ? (
            <input
              type="datetime-local"
              className={wp.input}
              value={scheduleInput}
              onChange={(e) => setScheduleInput(e.target.value)}
            />
          ) : null}

          <div className={wp.pillToggleRow}>
            <button
              type="button"
              className={`${wp.pillToggle} ${controlMode === 'host' ? wp.pillToggleOn : ''}`}
              onClick={() => setControlMode('host')}
            >
              Host controls playback
            </button>
            <button
              type="button"
              className={`${wp.pillToggle} ${controlMode === 'everyone' ? wp.pillToggleOn : ''}`}
              onClick={() => setControlMode('everyone')}
            >
              Anyone can pause / seek
            </button>
            <button
              type="button"
              className={`${wp.pillToggle} ${lobbyMusic ? wp.pillToggleOn : ''}`}
              onClick={() => setLobbyMusic((v) => !v)}
            >
              ♪ Lobby music: {lobbyMusic ? 'On' : 'Off'}
            </button>
          </div>

          {error ? <p style={{ color: '#f87171', fontSize: 12.5, margin: 0 }}>{error}</p> : null}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button type="button" className={wp.glassBtn} onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className={wp.startPartyBtn}
              disabled={!picked || createMut.isPending}
              onClick={() => {
                setError(null);
                createMut.mutate();
              }}
            >
              {createMut.isPending
                ? 'Creating…'
                : mode === 'schedule'
                  ? 'Schedule Watch Party'
                  : 'Open Theater Lobby'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Watch Party Room Modal (Theater Waiting Room + Synced Lobby Music + Chat)

function WatchPartyRoomModal({
  partyId,
  onClose,
}: {
  partyId: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const roomQ = useQuery(watchPartyQuery(partyId));
  const party = roomQ.data;
  const [chatText, setChatText] = useState('');
  const [lobbyMuted, setLobbyMuted] = useState(false);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [curtainsOpen, setCurtainsOpen] = useState(false);
  const [floatingEmojis, setFloatingEmojis] = useState<{ id: number; emoji: string; leftPct: number }[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const autoJoined = useRef(false);
  const lastMsgCount = useRef(0);

  // Keep curtain state synced with party status (unfurl smoothly on showtime!)
  useEffect(() => {
    if (!party) return;
    if (party.status === 'playing' || party.status === 'paused') {
      setCurtainsOpen(true);
    } else if (countdown === null) {
      setCurtainsOpen(false);
    }
  }, [party?.status, countdown, party]);

  // Float quick-reaction emojis up across the cinema stage when new reactions arrive
  useEffect(() => {
    const msgs = party?.messages ?? [];
    if (msgs.length > lastMsgCount.current) {
      const added = msgs.slice(lastMsgCount.current);
      const bursts = added
        .filter((m) => QUICK_EMOJIS.includes(m.body.trim()))
        .map((m, idx) => ({
          id: Date.now() + idx,
          emoji: m.body.trim(),
          leftPct: 16 + ((m.id * 19 + idx * 17) % 68),
        }));
      if (bursts.length) {
        setFloatingEmojis((prev) => [...prev.slice(-10), ...bursts]);
        window.setTimeout(() => {
          const ids = new Set(bursts.map((b) => b.id));
          setFloatingEmojis((prev) => prev.filter((x) => !ids.has(x.id)));
        }, 2300);
      }
    }
    lastMsgCount.current = msgs.length;
  }, [party?.messages]);

  const startShowtimeWithCurtain = async (p: WatchParty) => {
    if (countdown !== null) return;
    setCountdown(3);
    await new Promise((r) => window.setTimeout(r, 850));
    setCountdown(2);
    await new Promise((r) => window.setTimeout(r, 850));
    setCountdown(1);
    await new Promise((r) => window.setTimeout(r, 850));
    setCountdown(null);
    setCurtainsOpen(true);
    const updated = await watchPartyApi.state(p.id, { action: 'start' });
    refresh();
    // Let the velvet curtains glide apart before opening the full synced video player!
    window.setTimeout(() => launchSyncedVideo(updated), 1450);
  };

  // Auto-join room on open
  useEffect(() => {
    if (!party || autoJoined.current) return;
    autoJoined.current = true;
    if (!party.is_member) {
      void watchPartyApi
        .join(partyId, { rsvp: party.status === 'scheduled' })
        .then(() => qc.invalidateQueries({ queryKey: ['player', 'party', partyId] }));
    }
  }, [party, partyId, qc]);

  // Synced SoulSync library lobby music while in the waiting room
  const lobbyTrack = party?.lobby_track ?? null;
  const playLobbyAudio =
    !lobbyMuted &&
    !!lobbyTrack &&
    (party?.status === 'lobby' || party?.status === 'scheduled');

  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    if (!playLobbyAudio || !lobbyTrack) {
      el.pause();
      return;
    }
    const url = appURL(`/api/player/stream/track/${encodeURIComponent(lobbyTrack.track_id)}`);
    if (el.src !== new URL(url, window.location.origin).href) {
      el.src = url;
    }
    if (typeof lobbyTrack.live_position === 'number' && Math.abs(el.currentTime - lobbyTrack.live_position) > 3) {
      el.currentTime = lobbyTrack.live_position;
    }
    void el.play().catch(() => {
      /* browser autoplay policy: user clicks Unmute */
    });
  }, [playLobbyAudio, lobbyTrack]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['player', 'party', partyId] });
    void qc.invalidateQueries({ queryKey: ['player', 'parties'] });
  };

  const launchSyncedVideo = (p: WatchParty) => {
    const item = itemOf(p.item_type, p.item_id, p.meta);
    if (!item) return;
    if (audioRef.current) audioRef.current.pause();
    setActiveWatchPartyId(p.id);
    playItems([item], 0, { startAt: p.live_position });
  };

  if (!party) {
    return (
      <div className={wp.backdrop} onClick={onClose}>
        <div className={wp.modal} style={{ padding: 32, textAlign: 'center' }}>
          Loading theater room…
        </div>
      </div>
    );
  }

  const me = party.members.find((m) => m.you);
  const bg = remoteImage(party.meta.backdrop) ?? remoteImage(party.meta.artwork);

  return (
    <div className={wp.backdrop} onClick={onClose}>
      <audio ref={audioRef} loop />
      <div className={wp.modal} onClick={(e) => e.stopPropagation()}>
        <div className={wp.modalHero}>
          {bg ? <img className={wp.modalHeroBg} src={bg} alt="" /> : null}
          <div className={wp.modalHeroInner}>
            <div>
              <span className={wp.livePill} style={{ marginBottom: 8 }}>
                <span
                  className={`${wp.liveDot} ${party.status === 'playing' ? wp.liveDotActive : wp.liveDotLobby}`}
                />
                {party.status === 'playing'
                  ? `PLAYING · ${clock(party.live_position)}`
                  : party.status === 'paused'
                    ? `PAUSED · ${clock(party.live_position)}`
                    : party.status === 'scheduled'
                      ? `SCHEDULED · ${countdownText(party.scheduled_for, Date.now() / 1000)}`
                      : 'THEATER WAITING ROOM'}
              </span>
              <h2 className={wp.modalTitle}>{party.title}</h2>
              <p className={wp.modalSub}>
                Hosted by {party.host_profile?.name ?? 'Host'} · Room #{party.id}
                {party.meta.subtitle ? ` · ${party.meta.subtitle}` : ''}
              </p>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              {party.is_host ? (
                <button
                  type="button"
                  className={wp.glassBtn}
                  onClick={async () => {
                    await watchPartyApi.remove(party.id);
                    refresh();
                    onClose();
                  }}
                >
                  End Party
                </button>
              ) : null}
              <button type="button" className={wp.glassBtn} onClick={onClose}>
                Minimize
              </button>
            </div>
          </div>
        </div>

        <div className={wp.roomGrid}>
          <div className={wp.roomMain}>
            {/* 16:9 Cinema Proscenium Stage with Pleated Velvet Curtains */}
            <div className={wp.theaterStage} data-open={curtainsOpen ? 'true' : 'false'}>
              {bg ? <img className={wp.stageScreen} src={bg} alt="" /> : null}
              <div className={wp.stageGlow} />
              <div className={wp.curtainValance} />
              <div className={wp.curtainLeft} />
              <div className={wp.curtainRight} />

              <div className={wp.stageCenterOverlay}>
                {countdown !== null ? (
                  <div className={wp.countdownRing}>{countdown}</div>
                ) : !curtainsOpen ? (
                  <div className={wp.stageMarquee}>
                    <div className={wp.stageEyebrow}>
                      {party.status === 'scheduled'
                        ? `PREMIERE · ${countdownText(party.scheduled_for, Date.now() / 1000)}`
                        : 'FEATURE PRESENTATION'}
                    </div>
                    <h3 className={wp.stageTitle}>{party.title}</h3>
                    <div className={wp.stageSub}>
                      {party.members.filter((m) => m.ready).length} of {party.members.length} seats ready
                      {party.meta.subtitle ? ` · ${party.meta.subtitle}` : ''}
                    </div>
                  </div>
                ) : (
                  <div className={wp.stageMarquee} style={{ opacity: 0.92 }}>
                    <div className={wp.stageEyebrow}>
                      {party.status === 'playing' ? '● NOW SCREENING' : '❚❚ SCREENING PAUSED'}
                    </div>
                    <h3 className={wp.stageTitle}>{party.title}</h3>
                    <div className={wp.stageSub}>Synced at {clock(party.live_position)}</div>
                  </div>
                )}
              </div>

              {floatingEmojis.map((fe) => (
                <span
                  key={fe.id}
                  className={wp.floatingReaction}
                  style={{ left: `${fe.leftPct}%` }}
                  aria-hidden="true"
                >
                  {fe.emoji}
                </span>
              ))}
            </div>

            {/* Curved Theater Seating Row */}
            {party.members.length > 0 ? (
              <div className={wp.seatRow} aria-label="Seated audience">
                {party.members.map((m) => (
                  <div key={m.profile_id} className={`${wp.seat} ${m.ready ? wp.seatReady : ''}`}>
                    <Avatar profile={m.profile} />
                    <span className={wp.seatName}>{m.profile.name}</span>
                    <span className={wp.seatTag} style={!m.ready ? { color: 'rgba(255,255,255,0.45)' } : undefined}>
                      {m.is_host ? '★ Host' : m.ready ? '✓ Ready' : 'Seated'}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}

            {/* Synced SoulSync Library Lobby Music */}
            {lobbyTrack && (party.status === 'lobby' || party.status === 'scheduled') ? (
              <div className={wp.lobbyMusicBox}>
                {lobbyTrack.artwork ? (
                  <img
                    className={wp.lobbyMusicArt}
                    src={remoteImage(lobbyTrack.artwork) ?? ''}
                    alt=""
                  />
                ) : null}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 11, fontWeight: 800, color: 'var(--accent-light)' }}>
                    ♪ THEATER LOBBY MUSIC
                  </div>
                  <div
                    style={{
                      fontSize: 13.5,
                      fontWeight: 750,
                      color: '#fff',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {lobbyTrack.title}
                  </div>
                  {lobbyTrack.artist ? (
                    <div style={{ fontSize: 11.5, color: 'var(--pl-text-3)' }}>
                      {lobbyTrack.artist}
                    </div>
                  ) : null}
                </div>
                <button
                  type="button"
                  className={wp.glassBtn}
                  onClick={() => setLobbyMuted((m) => !m)}
                >
                  {lobbyMuted ? 'Unmute' : 'Mute'}
                </button>
                {party.can_control ? (
                  <button
                    type="button"
                    className={wp.glassBtn}
                    onClick={async () => {
                      await watchPartyApi.state(party.id, { shuffle_lobby_music: true });
                      refresh();
                    }}
                  >
                    Shuffle
                  </button>
                ) : null}
              </div>
            ) : null}

            {/* Primary Room Controls */}
            <div className={wp.cardActions} style={{ marginTop: 0 }}>
              {party.status === 'lobby' || party.status === 'scheduled' ? (
                <>
                  {party.can_control ? (
                    <button
                      type="button"
                      className={wp.startPartyBtn}
                      disabled={countdown !== null}
                      onClick={() => void startShowtimeWithCurtain(party)}
                    >
                      <PlayIcon />
                      {countdown !== null ? `Starting in ${countdown}…` : 'Unfurl Curtains & Start'}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className={wp.glassBtn}
                    onClick={async () => {
                      await watchPartyApi.join(party.id, { ready: !(me?.ready ?? false) });
                      refresh();
                    }}
                  >
                    {me?.ready ? '✓ Ready!' : 'Mark Ready'}
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    className={wp.startPartyBtn}
                    onClick={() => launchSyncedVideo(party)}
                  >
                    <PlayIcon />
                    Watch & Sync ({clock(party.live_position)})
                  </button>
                  {party.can_control ? (
                    <button
                      type="button"
                      className={wp.glassBtn}
                      onClick={async () => {
                        await watchPartyApi.state(party.id, {
                          action: party.status === 'playing' ? 'pause' : 'play',
                        });
                        refresh();
                      }}
                    >
                      {party.status === 'playing' ? 'Pause Everyone' : 'Resume Everyone'}
                    </button>
                  ) : null}
                  {party.can_control ? (
                    <button
                      type="button"
                      className={wp.glassBtn}
                      onClick={async () => {
                        await watchPartyApi.state(party.id, { action: 'lobby' });
                        refresh();
                      }}
                    >
                      Back to Lobby
                    </button>
                  ) : null}
                </>
              )}
            </div>

            {/* Guest Roster */}
            <div>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 800,
                  letterSpacing: '0.08em',
                  color: 'var(--pl-text-3)',
                  marginBottom: 8,
                }}
              >
                IN THE ROOM ({party.ready_count}/{party.member_count} READY)
              </div>
              <div className={wp.memberList}>
                {party.members.map((m) => (
                  <div key={m.profile_id} className={wp.memberRow}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <Avatar profile={m.profile} />
                      <span style={{ fontSize: 13, fontWeight: 700, color: '#fff' }}>
                        {m.profile?.name ?? 'Guest'}
                        {m.is_host ? ' (Host)' : m.you ? ' (You)' : ''}
                      </span>
                    </div>
                    <span className={m.ready ? wp.readyBadge : wp.waitingBadge}>
                      {m.ready ? 'Ready' : m.status === 'rsvp' ? 'RSVP' : 'Waiting'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Live Chat & Emoji Reactions */}
          <div className={wp.chatPane}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 800,
                letterSpacing: '0.08em',
                color: 'var(--pl-text-3)',
              }}
            >
              LOUNGE CHAT
            </div>
            <div className={wp.chatLog}>
              {party.messages.map((msg) =>
                msg.kind === 'system' ? (
                  <div key={msg.id} className={wp.chatSys}>
                    {msg.profile?.name ?? 'Someone'} {msg.body.toLowerCase()}
                  </div>
                ) : (
                  <div key={msg.id} className={wp.chatMsg}>
                    <strong>{msg.profile?.name ?? 'Someone'}</strong>
                    <span>{msg.body}</span>
                  </div>
                ),
              )}
            </div>

            <div className={wp.emojiRow}>
              {QUICK_EMOJIS.map((em) => (
                <button
                  key={em}
                  type="button"
                  className={wp.emojiBtn}
                  onClick={async () => {
                    await watchPartyApi.chat(party.id, 'reaction', em);
                    refresh();
                  }}
                >
                  {em}
                </button>
              ))}
            </div>

            <form
              className={wp.chatForm}
              onSubmit={async (e) => {
                e.preventDefault();
                const txt = chatText.trim();
                if (!txt) return;
                setChatText('');
                await watchPartyApi.chat(party.id, 'chat', txt);
                refresh();
              }}
            >
              <input
                className={wp.input}
                placeholder="Say something…"
                value={chatText}
                onChange={(e) => setChatText(e.target.value)}
              />
              <button type="submit" className={wp.glassBtn}>
                Send
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main Watching Now Page ──────────────────────────────────────────────────

function WatchingPage() {
  const qc = useQueryClient();
  const now = useQuery(watchingNowQuery());
  const together = useQuery(togetherQuery());
  const partiesQ = useQuery(watchPartiesQuery());
  const settings = useQuery(playerSettingsQuery());
  const [createSeed, setCreateSeed] = useState<PartySeed | null | undefined>(undefined);
  const [activePartyId, setActivePartyId] = useState<string | null>(null);
  const [nowSec, setNowSec] = useState(() => Date.now() / 1000);

  useEffect(() => {
    const id = window.setInterval(() => setNowSec(Date.now() / 1000), 15_000);
    return () => window.clearInterval(id);
  }, []);

  const sessions = now.data?.sessions ?? [];
  const parties = partiesQ.data ?? [];
  const sharing = settings.data?.settings.share_activity ?? true;

  return (
    <>
      <div className={wp.pageHead}>
        <PageTitle>Watching now</PageTitle>
        <div className={wp.headActions}>
          <button
            type="button"
            className={wp.startPartyBtn}
            onClick={() => setCreateSeed(null)}
            data-nav
          >
            🍿 Start a Watch Party
          </button>
        </div>
      </div>

      {!sharing ? (
        <p className={s.note}>
          You aren’t sharing what you play, so others don’t see you here.{' '}
          <PlayerLink href="/player/settings" data-nav>
            Player settings
          </PlayerLink>
        </p>
      ) : null}

      {/* Section 1: Watch Parties (Live Lobbies & Scheduled Premieres) */}
      <section className={k.rail} aria-label="Watch Parties">
        <div className={k.railHead}>
          <h2 className={k.sectionTitle}>Watch Parties</h2>
          {parties.length ? (
            <span className={k.railMore}>
              {parties.length} {parties.length === 1 ? 'room' : 'rooms'}
            </span>
          ) : null}
        </div>
        {parties.length ? (
          <div className={wp.cinemaGrid}>
            {parties.map((p) => (
              <WatchPartyCard
                key={p.id}
                party={p}
                nowSec={nowSec}
                onOpen={(id) => setActivePartyId(id)}
              />
            ))}
          </div>
        ) : (
          <Empty title="No active Watch Parties">
            Open a theater waiting room with music from your library, or schedule a movie night for
            the household.
          </Empty>
        )}
      </section>

      {/* Section 2: Right Now (Redesigned Cinema Backdrop Cards) */}
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
          <div className={wp.cinemaGrid}>
            {sessions.map((x) => (
              <CinemaNowCard
                key={`${x.profile_id}:${x.item_type}:${x.item_id}:${x.started}`}
                x={x}
                onStartParty={(seed) => setCreateSeed(seed)}
              />
            ))}
          </div>
        ) : (
          <Empty title="Nobody else is playing anything">
            When someone on this server watches or listens, it shows up here, live.
          </Empty>
        )}
      </section>

      {/* Section 3: Watching the Same Thing (Poster-First Grid) */}
      {together.data?.length ? (
        <section className={k.rail} aria-label="Watching the same thing">
          <div className={k.railHead}>
            <h2 className={k.sectionTitle}>Watching the same thing</h2>
            <span className={k.railMore}>the last two months</span>
          </div>
          <div className={wp.sharedGrid}>
            {together.data.map((t) => (
              <SharedTitleCard
                key={t.key}
                t={t}
                onStartParty={(seed) => setCreateSeed(seed)}
              />
            ))}
          </div>
        </section>
      ) : null}

      {createSeed !== undefined ? (
        <CreatePartyModal
          initialSeed={createSeed}
          onClose={() => setCreateSeed(undefined)}
          onCreated={(party) => {
            setCreateSeed(undefined);
            setActivePartyId(party.id);
            void qc.invalidateQueries({ queryKey: ['player', 'parties'] });
          }}
        />
      ) : null}

      {activePartyId ? (
        <WatchPartyRoomModal partyId={activePartyId} onClose={() => setActivePartyId(null)} />
      ) : null}
    </>
  );
}
