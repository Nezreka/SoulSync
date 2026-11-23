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
import { setActiveWatchPartyId } from './-ui/playback/party-reactions';
import { channelByIdTune } from './-ui/playback/tuner';
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

function CinemaNowCard({ x }: { x: WatchingSession }) {
  const item = itemOf(x.item_type, x.item_id, x.meta);
  const art = remoteImage(x.meta.backdrop) ?? remoteImage(x.meta.artwork);
  const part = x.duration ? Math.min(1, x.position / x.duration) : null;
  const join = () => {
    if (x.live && x.channel && channelByIdTune(x.channel.id)) return;
    if (item) playItems([item], 0, { startAt: x.position });
  };
  const detailsHref =
    x.meta.href ||
    (x.item_type === 'movie'
      ? `/player/movies/${x.item_id}`
      : x.item_type === 'episode' && x.meta.parent_id
        ? `/player/tv/${x.meta.parent_id}`
        : x.item_type === 'audiobook'
          ? `/player/audiobooks/${x.item_id}`
          : x.item_type === 'youtube'
            ? '/player/youtube'
            : null);

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
          {detailsHref ? (
            <PlayerLink href={detailsHref} className={wp.glassBtn} data-nav>
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
    <article
      className={wp.cinemaCard}
      style={{ cursor: 'pointer' }}
      onClick={() => onOpen(party.id)}
    >
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
  const canParty =
    me.item_type === 'movie' || me.item_type === 'episode' || me.item_type === 'youtube';

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
  const [kindFilter, setKindFilter] = useState<'all' | 'movie' | 'episode'>('all');
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

    if (kindFilter === 'all' || kindFilter === 'movie') {
      for (const m of cat.movies) {
        if (q && !m.title.toLowerCase().includes(q)) continue;
        out.push({
          item_type: 'movie',
          item_id: String(m.id),
          title: m.title,
          meta: {
            title: m.title,
            subtitle:
              [m.year, m.content_rating, m.genres?.[0]].filter(Boolean).join(' · ') || 'Movie',
            artwork: m.has_poster ? `/api/video/poster/movie/${m.id}?w=342` : undefined,
            backdrop: m.has_backdrop ? `/api/video/backdrop/movie/${m.id}?w=1280` : undefined,
            href: `/player/movies/${m.id}`,
          },
        });
        if (out.length >= (kindFilter === 'movie' ? 36 : 20)) break;
      }
    }

    if (kindFilter === 'all' || kindFilter === 'episode') {
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
            artwork: sh.has_poster ? `/api/video/poster/show/${sh.id}?w=342` : undefined,
            backdrop: sh.has_backdrop ? `/api/video/backdrop/show/${sh.id}?w=1280` : undefined,
            href: `/player/tv/${sh.id}`,
            parent_id: String(sh.id),
            parent_title: sh.title,
          },
        });
        if (out.length >= 40) break;
      }
    }

    return out;
  }, [catalog.data, search, kindFilter]);

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

  const pickedArt = remoteImage(picked?.meta.backdrop) ?? remoteImage(picked?.meta.artwork);
  const pickedPoster = remoteImage(picked?.meta.artwork);

  return (
    <div className={wp.backdrop} onClick={onClose}>
      <div className={wp.modal} style={{ maxWidth: 760 }} onClick={(e) => e.stopPropagation()}>
        <div className={wp.modalHero}>
          {pickedArt ? <img className={wp.modalHeroBg} src={pickedArt} alt="" /> : null}
          <div className={wp.modalHeroInner}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, minWidth: 0 }}>
              {pickedPoster ? (
                <img
                  src={pickedPoster}
                  alt=""
                  style={{
                    width: 56,
                    height: 82,
                    borderRadius: 10,
                    objectFit: 'cover',
                    border: '1px solid rgba(255,255,255,0.18)',
                    boxShadow: '0 12px 28px rgba(0,0,0,0.55)',
                    flexShrink: 0,
                  }}
                />
              ) : null}
              <div style={{ minWidth: 0 }}>
                <span className={wp.livePill} style={{ marginBottom: 6 }}>
                  <span className={`${wp.liveDot} ${wp.liveDotLobby}`} />
                  THEATER PREMIERE SETUP
                </span>
                <h2 className={wp.modalTitle}>
                  {picked ? (picked.meta.title ?? picked.title) : 'Start a Watch Party'}
                </h2>
                <p className={wp.modalSub}>
                  {picked
                    ? `${picked.item_type === 'movie' ? 'Movie' : 'TV Episode'}${picked.meta.subtitle ? ` · ${picked.meta.subtitle}` : ''}`
                    : 'Choose a movie or show to open a synchronized theater lobby with library music.'}
                </p>
              </div>
            </div>
            <button type="button" className={wp.glassBtn} onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        <div className={wp.roomMain} style={{ borderRight: 'none', gap: 14 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
            <input
              className={wp.input}
              style={{ flex: '1 1 260px' }}
              placeholder="Search your movies & shows…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className={wp.pillToggleRow}>
              {(
                [
                  ['all', 'All'],
                  ['movie', 'Movies'],
                  ['episode', 'TV Shows'],
                ] as const
              ).map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  className={`${wp.pillToggle} ${kindFilter === k ? wp.pillToggleOn : ''}`}
                  onClick={() => setKindFilter(k)}
                >
                  {label}
                </button>
              ))}
            </div>
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
                  <div className={wp.pickerPosterWrap}>
                    {thumb ? (
                      <img
                        className={wp.pickerThumb}
                        src={thumb}
                        alt=""
                        loading="lazy"
                        onError={(e) => {
                          e.currentTarget.style.display = 'none';
                        }}
                      />
                    ) : (
                      <div className={wp.pickerFallback}>{c.title.charAt(0).toUpperCase()}</div>
                    )}
                    <span className={wp.pickerBadge}>
                      {c.item_type === 'movie' ? 'FILM' : 'TV'}
                    </span>
                    {selected ? <span className={wp.pickerCheck}>✓</span> : null}
                  </div>
                  <div className={wp.pickerMeta}>
                    <div className={wp.pickerTitle}>{c.meta.title ?? c.title}</div>
                    <div className={wp.pickerSub}>
                      {c.meta.subtitle ?? (c.item_type === 'movie' ? 'Movie' : 'Latest Episode')}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
              gap: 10,
            }}
          >
            <div>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 800,
                  letterSpacing: '0.07em',
                  color: 'var(--pl-text-3)',
                  marginBottom: 6,
                }}
              >
                ROOM TITLE
              </div>
              <input
                className={wp.input}
                placeholder="Party name (e.g. Friday Night Movie)"
                value={roomTitle}
                onChange={(e) => setRoomTitle(e.target.value)}
              />
            </div>
            <div>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 800,
                  letterSpacing: '0.07em',
                  color: 'var(--pl-text-3)',
                  marginBottom: 6,
                }}
              >
                SHOWTIME
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
            </div>
          </div>

          {mode === 'schedule' ? (
            <input
              type="datetime-local"
              className={wp.input}
              value={scheduleInput}
              onChange={(e) => setScheduleInput(e.target.value)}
            />
          ) : null}

          <div>
            <div
              style={{
                fontSize: 11,
                fontWeight: 800,
                letterSpacing: '0.07em',
                color: 'var(--pl-text-3)',
                marginBottom: 6,
              }}
            >
              THEATER OPTIONS
            </div>
            <div className={wp.pillToggleRow}>
              <button
                type="button"
                className={`${wp.pillToggle} ${controlMode === 'host' ? wp.pillToggleOn : ''}`}
                onClick={() => setControlMode('host')}
              >
                ★ Host controls playback
              </button>
              <button
                type="button"
                className={`${wp.pillToggle} ${controlMode === 'everyone' ? wp.pillToggleOn : ''}`}
                onClick={() => setControlMode('everyone')}
              >
                Everyone can pause / seek
              </button>
              <button
                type="button"
                className={`${wp.pillToggle} ${lobbyMusic ? wp.pillToggleOn : ''}`}
                onClick={() => setLobbyMusic((v) => !v)}
              >
                ♪ Lobby music: {lobbyMusic ? 'On' : 'Off'}
              </button>
            </div>
          </div>

          {error ? <p style={{ color: '#f87171', fontSize: 12.5, margin: 0 }}>{error}</p> : null}

          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              gap: 10,
              paddingTop: 4,
            }}
          >
            <span style={{ fontSize: 12, color: 'var(--pl-text-3)' }}>
              {picked ? `Selected: ${picked.title}` : 'Select a movie or show above to continue'}
            </span>
            <div style={{ display: 'flex', gap: 8 }}>
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
    </div>
  );
}

// ── Watch Party Room Modal (Theater Waiting Room + Synced Lobby Music + Chat)

interface RoomFloatingBubble {
  uid: string;
  emoji: string;
  sender: string;
  side: 'left' | 'right';
  edgePx: number;
  swayPx: number;
}

function WatchPartyRoomModal({ partyId, onClose }: { partyId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const roomQ = useQuery(watchPartyQuery(partyId));
  const party = roomQ.data;
  const [chatText, setChatText] = useState('');
  const [lobbyMuted, setLobbyMuted] = useState(false);
  const [starting, setStarting] = useState(false);
  const [floatingBubbles, setFloatingBubbles] = useState<RoomFloatingBubble[]>([]);
  const [rateLimitedUntil, setRateLimitedUntil] = useState(0);
  const [roomError, setRoomError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const autoJoined = useRef(false);
  const seenMsgIds = useRef<Set<number>>(new Set());
  const msgInitialized = useRef(false);
  const prevStatusRef = useRef<string | null>(null);
  const reactionTimestampsRef = useRef<number[]>([]);

  const spawnRoomBubble = (uid: string, emoji: string, sender: string, seed: number) => {
    const side: 'left' | 'right' = seed % 2 === 0 ? 'left' : 'right';
    const edgePx = 10 + ((seed * 19) % 44);
    const swayPx = (seed % 3 === 0 ? 1 : -1) * (10 + (seed % 14));
    const bubble: RoomFloatingBubble = { uid, emoji, sender, side, edgePx, swayPx };
    setFloatingBubbles((prev) => [...prev.slice(-16), bubble]);
    window.setTimeout(() => {
      setFloatingBubbles((prev) => prev.filter((x) => x.uid !== uid));
    }, 2800);
  };

  // Float incoming emoji reactions up both the left and right sides of the room modal
  useEffect(() => {
    const msgs = party?.messages ?? [];
    if (!msgInitialized.current && msgs.length > 0) {
      msgInitialized.current = true;
      seenMsgIds.current = new Set(msgs.map((m) => m.id));
      return;
    }
    for (const m of msgs) {
      if (seenMsgIds.current.has(m.id)) continue;
      seenMsgIds.current.add(m.id);
      const em = m.body.trim();
      if (m.kind === 'reaction' || QUICK_EMOJIS.includes(em) || em.startsWith('🚽')) {
        spawnRoomBubble(`room-srv-${m.id}`, em, m.profile?.name ?? '', m.id);
      }
    }
  }, [party?.messages]);

  const launchSyncedVideo = (p: WatchParty, opts?: { withCurtain?: boolean }) => {
    const item = itemOf(p.item_type, p.item_id, p.meta);
    if (!item) return;
    if (audioRef.current) audioRef.current.pause();
    setActiveWatchPartyId(p.id, { triggerCurtain: opts?.withCurtain ?? true });
    playItems([item], 0, { startAt: p.live_position });
    onClose();
  };

  // When the party transitions from lobby/scheduled -> playing, auto-launch everyone into the fullscreen video player with the full-screen velvet curtain unfurl!
  useEffect(() => {
    if (!party) return;
    const prev = prevStatusRef.current;
    prevStatusRef.current = party.status;
    if ((prev === 'lobby' || prev === 'scheduled') && party.status === 'playing' && !starting) {
      launchSyncedVideo(party, { withCurtain: true });
    }
  }, [party?.status, party, starting]);

  const startShowtime = async (p: WatchParty) => {
    if (starting) return;
    setStarting(true);
    setRoomError(null);
    try {
      const updated = await watchPartyApi.state(p.id, { action: 'start' });
      refresh();
      launchSyncedVideo(updated, { withCurtain: true });
    } catch (e) {
      setRoomError(e instanceof Error ? e.message : 'Could not start stream');
      setStarting(false);
    }
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
    !lobbyMuted && !!lobbyTrack && (party?.status === 'lobby' || party?.status === 'scheduled');

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
    if (
      typeof lobbyTrack.live_position === 'number' &&
      Math.abs(el.currentTime - lobbyTrack.live_position) > 3
    ) {
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
  const poster = remoteImage(party.meta.artwork) ?? remoteImage(party.meta.backdrop);
  const bg = remoteImage(party.meta.backdrop) ?? poster;
  const majorityReady =
    party.majority_ready ?? (party.member_count > 0 && party.ready_count * 2 > party.member_count);
  const canStartFromLobby =
    party.is_host || (party.member_count > 1 && majorityReady) || party.can_control;
  const isRateLimited = Date.now() < rateLimitedUntil;
  const peeBreak = party.pee_break ?? null;
  const isMyPeeBreak = !!peeBreak && me?.profile_id === peeBreak.profile_id;
  const peeCooldownSec = party.pee_cooldown_remaining ?? 0;

  const handleSendRoomReaction = async (em: string) => {
    const now = Date.now();
    const recent = reactionTimestampsRef.current.filter((t) => now - t < 4000);
    if (recent.length >= 5) {
      setRateLimitedUntil(now + 2800);
      setRoomError('Emoji cooldown (max 5 reactions per 4s)…');
      window.setTimeout(() => setRoomError(null), 2600);
      return;
    }
    recent.push(now);
    reactionTimestampsRef.current = recent;
    if (recent.length >= 5) {
      setRateLimitedUntil(now + 2500);
    }
    const seed = now + recent.length * 13;
    spawnRoomBubble(`room-local-${now}-${Math.random()}`, em, 'You', seed);
    try {
      const msg = await watchPartyApi.chat(party.id, 'reaction', em);
      if (msg?.id) seenMsgIds.current.add(msg.id);
      refresh();
    } catch (e) {
      setRoomError(e instanceof Error ? e.message : 'Slow down on emoji reactions!');
    }
  };

  return (
    <div className={wp.backdrop} onClick={onClose}>
      <audio ref={audioRef} loop />
      <div className={wp.modal} onClick={(e) => e.stopPropagation()}>
        {/* Dual-Side (Left + Right) Floating Emoji Reaction Lanes inside the Room Modal */}
        <div className={wp.roomSideLaneLeft} aria-hidden="true">
          {floatingBubbles
            .filter((b) => b.side === 'left')
            .map((b) => (
              <div
                key={b.uid}
                className={wp.roomFloatingBubble}
                style={{
                  left: b.edgePx,
                  ['--sway-px' as string]: `${b.swayPx}px`,
                }}
              >
                <span className={wp.roomFloatingEmoji}>{b.emoji}</span>
                {b.sender ? <span className={wp.roomFloatingSender}>{b.sender}</span> : null}
              </div>
            ))}
        </div>
        <div className={wp.roomSideLaneRight} aria-hidden="true">
          {floatingBubbles
            .filter((b) => b.side === 'right')
            .map((b) => (
              <div
                key={b.uid}
                className={wp.roomFloatingBubble}
                style={{
                  right: b.edgePx,
                  ['--sway-px' as string]: `${b.swayPx}px`,
                }}
              >
                <span className={wp.roomFloatingEmoji}>{b.emoji}</span>
                {b.sender ? <span className={wp.roomFloatingSender}>{b.sender}</span> : null}
              </div>
            ))}
        </div>

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
                    ? peeBreak
                      ? `🚽 PEE BREAK · ${clock(party.live_position)}`
                      : `PAUSED · ${clock(party.live_position)}`
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
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {party.is_member ? (
                <button
                  type="button"
                  className={wp.glassBtn}
                  title="Leave this Watch Party room"
                  onClick={async () => {
                    await watchPartyApi.leave(party.id);
                    setActiveWatchPartyId(null);
                    refresh();
                    onClose();
                  }}
                >
                  Leave Party
                </button>
              ) : null}
              {party.is_host ? (
                <button
                  type="button"
                  className={wp.glassBtn}
                  title="End this Watch Party for everyone"
                  onClick={async () => {
                    await watchPartyApi.remove(party.id);
                    setActiveWatchPartyId(null);
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
            {/* Sleek Cinema Feature Presentation Showcase Card (Velvet curtains now unfurl full-screen over the video player!) */}
            <div className={wp.lobbyShowcase}>
              {bg ? <img className={wp.lobbyShowcaseBg} src={bg} alt="" /> : null}
              <div className={wp.lobbyShowcaseScrim} />
              <div className={wp.lobbyPosterCard}>
                {poster ? (
                  <img src={poster} alt="" />
                ) : (
                  <div className={wp.pickerFallback}>
                    {(party.title || '?').slice(0, 1).toUpperCase()}
                  </div>
                )}
              </div>
              <div className={wp.lobbyShowcaseInfo}>
                <div
                  style={{
                    fontSize: 10.5,
                    fontWeight: 850,
                    letterSpacing: '0.16em',
                    textTransform: 'uppercase',
                    color: '#f6c978',
                  }}
                >
                  {party.status === 'playing'
                    ? `● NOW SCREENING · ${clock(party.live_position)}`
                    : party.status === 'paused'
                      ? `❚❚ PAUSED AT ${clock(party.live_position)}`
                      : '★ FEATURE PRESENTATION'}
                </div>
                <div style={{ fontSize: 19, fontWeight: 900, color: '#fff', lineHeight: 1.18 }}>
                  {party.title}
                </div>
                {party.meta.subtitle ? (
                  <div style={{ fontSize: 12.5, color: 'rgba(255,255,255,0.72)' }}>
                    {party.meta.subtitle}
                  </div>
                ) : null}
                <div style={{ marginTop: 4 }}>
                  {majorityReady ? (
                    <span className={wp.majorityPill}>
                      ✓ {party.ready_count} of {party.member_count} Ready · Majority Ready to Start!
                    </span>
                  ) : (
                    <span className={wp.waitingMajorityPill}>
                      {party.ready_count} of {party.member_count} Ready · Majority or Host starts
                      stream
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Active 🚽 5-Minute Pee Break Intermission Banner */}
            {peeBreak ? (
              <div className={wp.peeBreakBanner}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 24 }}>🚽</span>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 850, color: '#fef3c7' }}>
                      {peeBreak.profile?.name ?? 'Someone'} called a 5-minute pee break!
                    </div>
                    <div style={{ fontSize: 11.5, color: '#fcd34d' }}>
                      Stream is paused for everyone until they return and click Resume (5s
                      countdown).
                    </div>
                  </div>
                </div>
                {isMyPeeBreak || party.is_host ? (
                  <button
                    type="button"
                    className={`${wp.glassBtn} ${wp.glassBtnPrimary}`}
                    onClick={async () => {
                      const updated = await watchPartyApi.state(party.id, { action: 'resume' });
                      refresh();
                      launchSyncedVideo(updated, { withCurtain: false });
                    }}
                  >
                    ✓ I'm Back & Resume (5s)
                  </button>
                ) : null}
              </div>
            ) : null}

            {/* Curved Theater Seating Row */}
            {party.members.length > 0 ? (
              <div className={wp.seatRow} aria-label="Seated audience">
                {party.members.map((m) => (
                  <div key={m.profile_id} className={`${wp.seat} ${m.ready ? wp.seatReady : ''}`}>
                    <Avatar profile={m.profile} />
                    <span className={wp.seatName}>{m.profile.name}</span>
                    <span
                      className={wp.seatTag}
                      style={!m.ready ? { color: 'rgba(255,255,255,0.45)' } : undefined}
                    >
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

            {roomError ? (
              <div style={{ fontSize: 12, fontWeight: 700, color: '#fbbf24' }}>{roomError}</div>
            ) : null}

            {/* Primary Room Controls */}
            <div className={wp.cardActions} style={{ marginTop: 0 }}>
              {party.status === 'lobby' || party.status === 'scheduled' ? (
                <>
                  <button
                    type="button"
                    className={wp.startPartyBtn}
                    disabled={!canStartFromLobby || starting}
                    title={
                      canStartFromLobby
                        ? 'Launch fullscreen player & unfurl velvet theater curtains for everyone'
                        : 'Waiting for majority ready or host to start'
                    }
                    onClick={() => void startShowtime(party)}
                  >
                    <PlayIcon />
                    {starting ? 'Unfurling Curtains…' : 'Start Feature Presentation'}
                  </button>
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
                    onClick={() => launchSyncedVideo(party, { withCurtain: true })}
                  >
                    <PlayIcon />
                    Watch & Sync ({clock(party.live_position)})
                  </button>
                  {/* Anyone in the party can pause; anyone (or pee-break caller/host) can resume with 5s countdown */}
                  <button
                    type="button"
                    className={wp.glassBtn}
                    disabled={!!peeBreak && !isMyPeeBreak && !party.is_host}
                    onClick={async () => {
                      const action = party.status === 'playing' ? 'pause' : 'resume';
                      const updated = await watchPartyApi.state(party.id, { action });
                      refresh();
                      if (action === 'resume') {
                        launchSyncedVideo(updated, { withCurtain: false });
                      }
                    }}
                  >
                    {party.status === 'playing' ? '⏸ Pause Everyone' : '▶ Resume (5s Countdown)'}
                  </button>
                  <button
                    type="button"
                    className={wp.glassBtn}
                    disabled={peeCooldownSec > 0 || !!peeBreak}
                    title={
                      peeCooldownSec > 0
                        ? `On cooldown (${Math.ceil(peeCooldownSec / 60)}m remaining — once every 10m)`
                        : 'Pause stream for 5 minutes so you can take a pee break'
                    }
                    onClick={async () => {
                      try {
                        await watchPartyApi.state(party.id, { action: 'pee_break' });
                        void handleSendRoomReaction('🚽💨');
                        refresh();
                      } catch (e) {
                        setRoomError(e instanceof Error ? e.message : 'Pee break on cooldown');
                      }
                    }}
                  >
                    {peeCooldownSec > 0
                      ? `🚽 Pee Break (${Math.ceil(peeCooldownSec / 60)}m)`
                      : '🚽 Pee Break (5m)'}
                  </button>
                  {party.is_host ? (
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
                IN THE ROOM ({party.ready_count}/{party.member_count} READY
                {majorityReady ? ' · MAJORITY READY' : ''})
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
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontSize: 11,
                fontWeight: 800,
                letterSpacing: '0.08em',
                color: 'var(--pl-text-3)',
              }}
            >
              <span>LOUNGE CHAT</span>
              {isRateLimited ? (
                <span style={{ color: '#fbbf24', fontSize: 10 }}>EMOJI COOLDOWN…</span>
              ) : null}
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
                  disabled={isRateLimited}
                  className={wp.emojiBtn}
                  title={isRateLimited ? 'Cooling down…' : `Send ${em} to room`}
                  onClick={() => void handleSendRoomReaction(em)}
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
              <SharedTitleCard key={t.key} t={t} onStartParty={(seed) => setCreateSeed(seed)} />
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
