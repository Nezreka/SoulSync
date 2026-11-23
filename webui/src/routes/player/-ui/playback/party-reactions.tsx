/**
 * Best-in-class Fullscreen Watch Party Social, Curtain & Sync Overlay:
 * - Full-Viewport (100vw × 100vh) Pleated Velvet Theater Curtain Unfurl directly over the video player
 * - Dual-side (Left + Right edge) floating emoji reactions with burst rate limiting (max 5 per 4s)
 * - Live playback state sync: anyone can pause, synchronized 5-second resume countdown (5… 4… 3… 2… 1…)
 * - 🚽 5-Minute Pee Break intermission alert banner (10-minute cooldown per person; resumes when they return)
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { clock } from '../../-engine/timeline';
import { type WatchParty, watchPartyApi } from '../../-player.api';
import { getEngine } from './use-player';

export const REACTION_EMOJIS = ['🍿', '🔥', '😂', '😮', '👏', '❤️'] as const;
const REACTION_BURST_LIMIT = 5;
const REACTION_BURST_WINDOW_MS = 4000;

let currentPartyId: string | null = null;
let pendingCurtainUnfurl = false;
const partyListeners = new Set<() => void>();

export function setActiveWatchPartyId(id: string | null, opts?: { triggerCurtain?: boolean }) {
  if (opts?.triggerCurtain && id) {
    pendingCurtainUnfurl = true;
  } else if (!id) {
    pendingCurtainUnfurl = false;
  }
  currentPartyId = id;
  for (const fn of partyListeners) fn();
}

export function consumePendingCurtainUnfurl(): boolean {
  if (!pendingCurtainUnfurl) return false;
  pendingCurtainUnfurl = false;
  return true;
}

/**
 * Called by FullPlayer when the user toggles Play/Pause via the main video player button,
 * video canvas click, or Space/K shortcut while inside a Watch Party.
 * Returns true if a Watch Party handled the toggle (with 5s countdown on resume).
 */
export function handleWatchPartyPlayerToggle(): boolean {
  if (!currentPartyId) return false;
  const pid = currentPartyId;
  const engine = getEngine();
  const st = engine.getState();
  const isPlaying = st.status === 'playing' || st.status === 'buffering';
  if (isPlaying) {
    engine.pause();
    void watchPartyApi
      .state(pid, { action: 'pause', position: st.position })
      .catch(() => undefined);
  } else {
    // Keep local video paused while the 5-second server resume countdown runs!
    void watchPartyApi
      .state(pid, { action: 'resume', position: st.position })
      .catch(() => undefined);
  }
  return true;
}

export function useActiveWatchPartyId(): string | null {
  return useSyncExternalStore(
    (fn) => {
      partyListeners.add(fn);
      return () => {
        partyListeners.delete(fn);
      };
    },
    () => currentPartyId,
    () => null,
  );
}

interface FloatingBubble {
  uid: string;
  emoji: string;
  sender: string;
  side: 'left' | 'right';
  edgePx: number;
  swayPx: number;
}

interface ChatToast {
  uid: string;
  sender: string;
  body: string;
}

export function PartyReactionsOverlay({ hudVisible }: { hudVisible: boolean }) {
  const partyId = useActiveWatchPartyId();
  const [party, setParty] = useState<WatchParty | null>(null);
  const [bubbles, setBubbles] = useState<FloatingBubble[]>([]);
  const [chatToasts, setChatToasts] = useState<ChatToast[]>([]);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatInput, setChatInput] = useState('');
  const [unreadChat, setUnreadChat] = useState(0);
  const [videoEnded, setVideoEnded] = useState(false);
  const [muted, setMuted] = useState(false);
  const [rateLimitedUntil, setRateLimitedUntil] = useState<number>(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  // Full-viewport velvet curtain unfurl state over the video player
  const [curtainMounted, setCurtainMounted] = useState(false);
  const [curtainOpen, setCurtainOpen] = useState(false);

  const seenIdsRef = useRef<Set<number>>(new Set());
  const initializedRef = useRef<string | null>(null);
  const serverOffsetSecRef = useRef<number>(0);
  const lastLocalActionAtRef = useRef<number>(0);
  const reactionTimestampsRef = useRef<number[]>([]);
  const chatOpenRef = useRef(false);
  chatOpenRef.current = chatOpen;

  const spawnChatToast = useCallback((uid: string, sender: string, body: string) => {
    setChatToasts((prev) => [...prev.slice(-2), { uid, sender, body }]);
    window.setTimeout(() => {
      setChatToasts((prev) => prev.filter((t) => t.uid !== uid));
    }, 5500);
  }, []);

  const showToast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.setTimeout(() => {
      setToastMsg((prev) => (prev === msg ? null : prev));
    }, 3200);
  }, []);

  // Trigger full-screen velvet curtain unfurl when entering Watch Party video
  useEffect(() => {
    if (!partyId) {
      setCurtainMounted(false);
      setCurtainOpen(false);
      setParty(null);
      return;
    }
    if (consumePendingCurtainUnfurl()) {
      setCurtainMounted(true);
      setCurtainOpen(false);
      const tOpen = window.setTimeout(() => setCurtainOpen(true), 420);
      const tUnmount = window.setTimeout(() => setCurtainMounted(false), 2650);
      return () => {
        window.clearTimeout(tOpen);
        window.clearTimeout(tUnmount);
      };
    }
  }, [partyId]);

  // 200ms local clock ticker for smooth 5-second resume countdown, pee break timer & post-credits check
  useEffect(() => {
    if (!partyId) {
      setVideoEnded(false);
      setChatOpen(false);
      setUnreadChat(0);
      return;
    }
    const id = window.setInterval(() => {
      setNowMs(Date.now());
      const st = getEngine().getState();
      if (st.kind === 'video' && st.duration > 30) {
        const atEnd =
          st.status === 'ended' || (st.status === 'paused' && st.position >= st.duration - 1.5);
        setVideoEnded(atEnd);
      } else {
        setVideoEnded(false);
      }
    }, 200);
    return () => window.clearInterval(id);
  }, [partyId]);

  const spawnBubble = useCallback((uid: string, emoji: string, sender: string, seed: number) => {
    const side: 'left' | 'right' = seed % 2 === 0 ? 'left' : 'right';
    const edgePx = 20 + ((seed * 23) % 74);
    const swayPx = (seed % 3 === 0 ? 1 : -1) * (12 + (seed % 16));
    const next: FloatingBubble = { uid, emoji, sender, side, edgePx, swayPx };
    setBubbles((prev) => [...prev.slice(-18), next]);
    window.setTimeout(() => {
      setBubbles((prev) => prev.filter((b) => b.uid !== uid));
    }, 3400);
  }, []);

  // Poll Watch Party state & sync play/pause/countdown/reactions
  const applyPartySync = useCallback((p: WatchParty) => {
    setParty(p);
    if (typeof p.server_now === 'number') {
      serverOffsetSecRef.current = p.server_now - Date.now() / 1000;
    }
    const estServerNow = Date.now() / 1000 + serverOffsetSecRef.current;
    const engine = getEngine();
    const st = engine.getState();
    if (st.kind !== 'video') return;

    const recentlyActed = Date.now() - lastLocalActionAtRef.current < 1500;
    const inResumeCountdown = typeof p.resume_at === 'number' && p.resume_at > estServerNow;

    if (p.status === 'paused' || inResumeCountdown) {
      if (st.status === 'playing' && !recentlyActed) {
        engine.pause();
      }
      const targetPos = p.position ?? p.live_position ?? 0;
      if (Math.abs(st.position - targetPos) > 2.5 && !recentlyActed) {
        engine.seek(targetPos);
      }
    } else if (p.status === 'playing' && !inResumeCountdown) {
      if (st.status === 'paused' && !recentlyActed) {
        if (Math.abs(st.position - p.live_position) > 2.5) {
          engine.seek(p.live_position);
        }
        engine.resume();
      } else if (
        st.status === 'playing' &&
        Math.abs(st.position - p.live_position) > 4.5 &&
        !recentlyActed
      ) {
        engine.seek(p.live_position);
      }
    }
  }, []);

  useEffect(() => {
    if (!partyId) {
      seenIdsRef.current.clear();
      initializedRef.current = null;
      return;
    }
    let alive = true;
    const poll = async () => {
      try {
        const fetched = await watchPartyApi.get(partyId);
        if (!alive || !fetched) return;
        applyPartySync(fetched);

        const msgs = fetched.messages ?? [];
        if (initializedRef.current !== partyId) {
          initializedRef.current = partyId;
          seenIdsRef.current = new Set(msgs.map((m) => m.id));
          return;
        }
        for (const m of msgs) {
          if (seenIdsRef.current.has(m.id)) continue;
          seenIdsRef.current.add(m.id);
          const emoji = m.body.trim();
          const isReaction =
            m.kind === 'reaction' ||
            (REACTION_EMOJIS as readonly string[]).includes(emoji) ||
            emoji.startsWith('🚽');
          if (isReaction && !muted) {
            spawnBubble(`srv-${m.id}`, emoji, m.profile?.name ?? '', m.id);
          } else if (m.kind === 'chat' && emoji) {
            if (!muted && !chatOpenRef.current) {
              spawnChatToast(`chat-${m.id}`, m.profile?.name ?? 'Someone', emoji);
            }
            if (!chatOpenRef.current) {
              setUnreadChat((c) => c + 1);
            }
          }
        }
      } catch {
        /* ignore transient poll failures */
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1800);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [partyId, muted, applyPartySync, spawnBubble, spawnChatToast]);

  const estServerNow = nowMs / 1000 + serverOffsetSecRef.current;
  const resumeRemainingSec =
    party?.resume_at && party.resume_at > estServerNow
      ? Math.max(1, Math.ceil(party.resume_at - estServerNow))
      : null;

  // Automatically start video playback the exact moment the 5-second countdown finishes
  const prevResumeRemainingRef = useRef<number | null>(null);
  useEffect(() => {
    if (
      prevResumeRemainingRef.current !== null &&
      resumeRemainingSec === null &&
      party?.status === 'playing'
    ) {
      const engine = getEngine();
      const st = engine.getState();
      if (st.kind === 'video' && st.status === 'paused') {
        engine.resume();
      }
    }
    prevResumeRemainingRef.current = resumeRemainingSec;
  }, [resumeRemainingSec, party?.status]);

  if (!partyId) return null;

  const isRateLimited = nowMs < rateLimitedUntil;

  const sendReaction = async (emoji: string) => {
    const now = Date.now();
    const recent = reactionTimestampsRef.current.filter((t) => now - t < REACTION_BURST_WINDOW_MS);
    if (recent.length >= REACTION_BURST_LIMIT) {
      setRateLimitedUntil(now + 2800);
      showToast('Whoa! Emoji cooldown (3s)…');
      return;
    }
    recent.push(now);
    reactionTimestampsRef.current = recent;
    if (recent.length >= REACTION_BURST_LIMIT) {
      setRateLimitedUntil(now + 2500);
    }

    const seed = now + recent.length * 17;
    spawnBubble(`local-${now}-${Math.random()}`, emoji, 'You', seed);
    try {
      const res = await watchPartyApi.chat(partyId, 'reaction', emoji);
      if (res?.id) seenIdsRef.current.add(res.id);
    } catch {
      /* server rate limit or network */
    }
  };

  const handleTogglePartyPauseResume = async () => {
    if (!party) return;
    const engine = getEngine();
    const pos = engine.getState().position;
    lastLocalActionAtRef.current = Date.now();
    try {
      if (party.status === 'playing' && !resumeRemainingSec) {
        engine.pause();
        const updated = await watchPartyApi.state(party.id, { action: 'pause', position: pos });
        applyPartySync(updated);
      } else {
        const updated = await watchPartyApi.state(party.id, { action: 'resume', position: pos });
        applyPartySync(updated);
      }
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Could not update playback');
    }
  };

  const handleTriggerPeeBreak = async () => {
    if (!party) return;
    const engine = getEngine();
    const pos = engine.getState().position;
    lastLocalActionAtRef.current = Date.now();
    try {
      engine.pause();
      const updated = await watchPartyApi.state(party.id, { action: 'pee_break', position: pos });
      applyPartySync(updated);
      // Float a toilet + dash emoji burst on both sides for everyone!
      spawnBubble(`pee-L-${Date.now()}`, '🚽💨', 'You', 2);
      spawnBubble(`pee-R-${Date.now()}`, '🚽💨', 'You', 3);
      void watchPartyApi.chat(party.id, 'reaction', '🚽💨').catch(() => undefined);
    } catch (e) {
      showToast(e instanceof Error ? e.message : 'Pee break is on cooldown (once every 10m)');
    }
  };

  const me = party?.members?.find((m) => m.you);
  const peeBreak = party?.pee_break ?? null;
  const peeEndsRemainingSec =
    peeBreak && peeBreak.ends_at > estServerNow
      ? Math.max(0, Math.ceil(peeBreak.ends_at - estServerNow))
      : 0;
  const isMyPeeBreak = !!peeBreak && me?.profile_id === peeBreak.profile_id;
  const canResumePeeBreak = isMyPeeBreak || !!party?.is_host;
  const peeCooldownSec = party?.pee_cooldown_remaining ?? 0;

  return (
    <>
      <style>{`
        @keyframes ssPartyFloatUp {
          0% {
            opacity: 0;
            transform: translate3d(0, 22px, 0) scale(0.6);
          }
          14% {
            opacity: 1;
            transform: translate3d(var(--ss-sway, 12px), -28px, 0) scale(1.12);
          }
          55% {
            opacity: 0.96;
            transform: translate3d(calc(var(--ss-sway, 12px) * -0.75), -200px, 0) scale(1);
          }
          100% {
            opacity: 0;
            transform: translate3d(var(--ss-sway, 12px), -390px, 0) scale(0.9);
          }
        }
        @keyframes ssCountdownPulse {
          0% { transform: scale(0.78); opacity: 0.4; }
          35% { transform: scale(1.06); opacity: 1; }
          100% { transform: scale(1); opacity: 0.95; }
        }
      `}</style>

      {/* 1. Full-Viewport Velvet Theater Curtain Unfurl directly over the Video Player */}
      {curtainMounted && (
        <div
          aria-hidden="true"
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 45,
            pointerEvents: 'none',
            overflow: 'hidden',
          }}
        >
          {/* Warm golden stage spotlight glow */}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background:
                'radial-gradient(ellipse at 50% 0%, rgba(255, 206, 118, 0.25), transparent 60%)',
              opacity: curtainOpen ? 0 : 1,
              transition: 'opacity 1.4s ease',
            }}
          />
          {/* Top Crimson & Gold Valance */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              height: 38,
              zIndex: 4,
              background:
                'repeating-linear-gradient(90deg, #5e0511 0px, #9c0d21 22px, #3f020a 44px)',
              borderBottom: '3px solid rgba(255, 212, 128, 0.78)',
              boxShadow: '0 12px 32px rgba(0, 0, 0, 0.8)',
              transform: curtainOpen ? 'translateY(-110%)' : 'translateY(0%)',
              transition: 'transform 1.4s cubic-bezier(0.22, 1, 0.36, 1) 0.35s',
            }}
          />
          {/* Left Pleated Velvet Curtain */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: 0,
              width: '53%',
              zIndex: 2,
              backgroundImage:
                'repeating-linear-gradient(90deg, rgba(124, 10, 24, 0.99) 0px, rgba(168, 18, 38, 0.99) 24px, rgba(74, 4, 14, 0.995) 48px, rgba(140, 13, 29, 0.99) 72px), linear-gradient(180deg, rgba(0,0,0,0.5) 0%, rgba(0,0,0,0.05) 35%, rgba(0,0,0,0.6) 100%)',
              backgroundBlendMode: 'multiply',
              borderRight: '3px solid rgba(255, 210, 122, 0.65)',
              boxShadow: '18px 0 60px rgba(0, 0, 0, 0.85), inset 0 0 60px rgba(0, 0, 0, 0.7)',
              transform: curtainOpen ? 'translateX(-96%) skewX(-3deg)' : 'translateX(0%)',
              transition: 'transform 1.95s cubic-bezier(0.22, 1, 0.36, 1)',
            }}
          />
          {/* Right Pleated Velvet Curtain */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              right: 0,
              width: '53%',
              zIndex: 2,
              backgroundImage:
                'repeating-linear-gradient(90deg, rgba(124, 10, 24, 0.99) 0px, rgba(168, 18, 38, 0.99) 24px, rgba(74, 4, 14, 0.995) 48px, rgba(140, 13, 29, 0.99) 72px), linear-gradient(180deg, rgba(0,0,0,0.5) 0%, rgba(0,0,0,0.05) 35%, rgba(0,0,0,0.6) 100%)',
              backgroundBlendMode: 'multiply',
              borderLeft: '3px solid rgba(255, 210, 122, 0.65)',
              boxShadow: '-18px 0 60px rgba(0, 0, 0, 0.85), inset 0 0 60px rgba(0, 0, 0, 0.7)',
              transform: curtainOpen ? 'translateX(96%) skewX(3deg)' : 'translateX(0%)',
              transition: 'transform 1.95s cubic-bezier(0.22, 1, 0.36, 1)',
            }}
          />
          {/* Feature Presentation Plaque */}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              zIndex: 3,
              display: 'grid',
              placeItems: 'center',
              opacity: curtainOpen ? 0 : 1,
              transform: curtainOpen ? 'scale(1.08)' : 'scale(1)',
              transition: 'opacity 0.75s ease, transform 0.95s ease',
            }}
          >
            <div
              style={{
                padding: '22px 38px',
                borderRadius: 18,
                background: 'rgba(10, 6, 12, 0.84)',
                border: '1.5px solid rgba(255, 210, 125, 0.55)',
                boxShadow: '0 24px 70px rgba(0, 0, 0, 0.85)',
                textAlign: 'center',
                backdropFilter: 'blur(14px)',
              }}
            >
              <div
                style={{
                  fontSize: 11.5,
                  fontWeight: 850,
                  letterSpacing: '0.24em',
                  color: '#f6c978',
                  textTransform: 'uppercase',
                  marginBottom: 8,
                }}
              >
                ★ Feature Presentation ★
              </div>
              <div style={{ fontSize: 28, fontWeight: 900, color: '#fff' }}>
                {party?.title ?? 'Watch Party'}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 2. 🚽 5-Minute Pee Break Intermission Banner at Top-Center */}
      {peeBreak && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute',
            top: 76,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 38,
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            padding: '12px 20px',
            borderRadius: 16,
            background: 'linear-gradient(135deg, rgba(24, 18, 10, 0.92), rgba(45, 26, 8, 0.94))',
            border: '1.5px solid rgba(251, 191, 36, 0.55)',
            boxShadow: '0 18px 50px rgba(0, 0, 0, 0.75)',
            backdropFilter: 'blur(16px)',
            maxWidth: 'min(92vw, 640px)',
          }}
        >
          <span style={{ fontSize: 30, lineHeight: 1 }}>🚽</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <div style={{ fontSize: 14.5, fontWeight: 850, color: '#fef3c7' }}>
              {peeBreak.profile?.name ?? 'Someone'} needs a 5-minute pee break!
            </div>
            <div style={{ fontSize: 12, color: '#fcd34d', fontWeight: 650 }}>
              Stream paused for everyone ·{' '}
              {peeEndsRemainingSec > 0
                ? `${clock(peeEndsRemainingSec)} remaining in break`
                : '5 minutes up — ready to resume!'}
            </div>
          </div>
          {canResumePeeBreak ? (
            <button
              type="button"
              onClick={() => void handleTogglePartyPauseResume()}
              style={{
                padding: '8px 16px',
                borderRadius: 999,
                border: '1px solid rgba(52, 211, 153, 0.65)',
                background: 'linear-gradient(135deg, #10b981, #34d399)',
                color: '#051b11',
                fontSize: 12.5,
                fontWeight: 850,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
                boxShadow: '0 6px 20px rgba(16, 185, 129, 0.35)',
              }}
            >
              {isMyPeeBreak ? "✓ I'm Back — Resume (5s)" : 'Host Resume (5s)'}
            </button>
          ) : (
            <span
              style={{
                fontSize: 11.5,
                fontWeight: 700,
                color: 'rgba(254, 243, 199, 0.75)',
                whiteSpace: 'nowrap',
              }}
            >
              Waiting for return…
            </span>
          )}
        </div>
      )}

      {/* 3. Synchronized 5-Second Resume Countdown Overlay in Center */}
      {resumeRemainingSec !== null && (
        <div
          aria-live="assertive"
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 37,
            display: 'grid',
            placeItems: 'center',
            background: 'rgba(6, 9, 16, 0.52)',
            backdropFilter: 'blur(6px)',
            pointerEvents: 'none',
          }}
        >
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 12,
              padding: '26px 42px',
              borderRadius: 24,
              background: 'rgba(11, 15, 25, 0.88)',
              border: '1.5px solid rgba(255, 255, 255, 0.18)',
              boxShadow: '0 24px 70px rgba(0, 0, 0, 0.8)',
            }}
          >
            <div
              style={{
                fontSize: 11.5,
                fontWeight: 850,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                color: '#a5b4fc',
              }}
            >
              Everyone Ready · Synchronized Resume
            </div>
            <div
              key={resumeRemainingSec}
              style={{
                width: 96,
                height: 96,
                borderRadius: '50%',
                border: '3px solid #818cf8',
                background: 'rgba(79, 70, 229, 0.22)',
                boxShadow: '0 0 40px rgba(99, 102, 241, 0.45)',
                display: 'grid',
                placeItems: 'center',
                fontSize: 48,
                fontWeight: 900,
                color: '#fff',
                animation: 'ssCountdownPulse 0.85s cubic-bezier(0.22, 1, 0.36, 1)',
              }}
            >
              {resumeRemainingSec}
            </div>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'rgba(255,255,255,0.8)' }}>
              Resuming playback at {clock(party?.position ?? party?.live_position ?? 0)}…
            </div>
          </div>
        </div>
      )}

      {/* 4. Dual-Side (Left & Right Edge) Floating Emoji Reaction Lanes */}
      {!muted &&
        (['left', 'right'] as const).map((side) => (
          <div
            key={side}
            aria-hidden="true"
            style={{
              position: 'absolute',
              [side]: 0,
              bottom: 92,
              width: 175,
              height: 430,
              pointerEvents: 'none',
              overflow: 'hidden',
              zIndex: 25,
            }}
          >
            {bubbles
              .filter((b) => b.side === side)
              .map((b) => (
                <div
                  key={b.uid}
                  style={{
                    position: 'absolute',
                    [side]: b.edgePx,
                    bottom: 10,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    gap: 3,
                    ['--ss-sway' as string]: `${b.swayPx}px`,
                    animation: 'ssPartyFloatUp 3.2s cubic-bezier(0.22, 1, 0.36, 1) forwards',
                  }}
                >
                  <span
                    style={{
                      fontSize: 32,
                      lineHeight: 1,
                      filter: 'drop-shadow(0 6px 14px rgba(0,0,0,0.72))',
                    }}
                  >
                    {b.emoji}
                  </span>
                  {b.sender && (
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 750,
                        color: 'rgba(255,255,255,0.94)',
                        background: 'rgba(10,12,20,0.74)',
                        border: '1px solid rgba(255,255,255,0.18)',
                        backdropFilter: 'blur(8px)',
                        padding: '1px 7px',
                        borderRadius: 999,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {b.sender}
                    </span>
                  )}
                </div>
              ))}
          </div>
        ))}

      {/* 4b. Top-Right "Who's Here" Presence Strip (when HUD is visible) */}
      {hudVisible && party && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute',
            top: 22,
            right: 24,
            zIndex: 31,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 12px',
            borderRadius: 999,
            background: 'rgba(10, 13, 22, 0.78)',
            border: '1px solid rgba(255,255,255,0.16)',
            backdropFilter: 'blur(16px)',
            boxShadow: '0 10px 28px rgba(0,0,0,0.45)',
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: party.status === 'playing' ? '#22c55e' : '#f59e0b',
              boxShadow:
                party.status === 'playing'
                  ? '0 0 10px rgba(34,197,94,0.85)'
                  : '0 0 10px rgba(245,158,11,0.85)',
            }}
          />
          <span style={{ fontSize: 11.5, fontWeight: 800, color: '#fff', letterSpacing: '0.02em' }}>
            {party.member_count} {party.member_count === 1 ? 'watching' : 'watching'}
          </span>
          <div style={{ display: 'flex', alignItems: 'center', marginLeft: 2 }}>
            {(party.members ?? []).slice(0, 5).map((m, idx) => {
              const onBreak = peeBreak?.profile_id === m.profile_id;
              return (
                <span
                  key={m.profile_id}
                  title={`${m.profile?.name ?? 'Member'}${onBreak ? ' (Pee break)' : m.is_host ? ' (Host)' : ''}`}
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: '50%',
                    marginLeft: idx === 0 ? 0 : -6,
                    background: onBreak ? '#f59e0b' : 'rgba(99, 102, 241, 0.85)',
                    border: `2px solid ${onBreak ? '#fef3c7' : '#0b0f19'}`,
                    color: onBreak ? '#111' : '#fff',
                    fontSize: 10.5,
                    fontWeight: 850,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    overflow: 'hidden',
                  }}
                >
                  {onBreak ? '🚽' : (m.profile?.name ?? '?').slice(0, 1).toUpperCase()}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* 4c. Auto-Fading Live Chat Toasts (bottom-left, non-intrusive) */}
      {!muted && !chatOpen && chatToasts.length > 0 && (
        <div
          aria-live="polite"
          style={{
            position: 'absolute',
            left: 24,
            bottom: hudVisible ? 154 : 36,
            zIndex: 28,
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
            maxWidth: 'min(360px, 72vw)',
            pointerEvents: 'none',
            transition: 'bottom 200ms ease',
          }}
        >
          {chatToasts.map((t) => (
            <div
              key={t.uid}
              style={{
                padding: '7px 12px',
                borderRadius: 12,
                background: 'rgba(10, 13, 22, 0.78)',
                border: '1px solid rgba(255,255,255,0.14)',
                backdropFilter: 'blur(14px)',
                color: '#fff',
                fontSize: 12.5,
                lineHeight: 1.35,
                boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
              }}
            >
              <strong style={{ color: '#a5b4fc', marginRight: 6 }}>{t.sender}</strong>
              <span>{t.body}</span>
            </div>
          ))}
        </div>
      )}

      {/* 4d. Slide-Over In-Player Chat Drawer */}
      {chatOpen && party && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute',
            top: 72,
            right: 24,
            bottom: 152,
            width: 'min(330px, 86vw)',
            zIndex: 36,
            display: 'flex',
            flexDirection: 'column',
            borderRadius: 18,
            background: 'rgba(10, 13, 22, 0.9)',
            border: '1px solid rgba(255,255,255,0.16)',
            backdropFilter: 'blur(20px)',
            boxShadow: '0 22px 60px rgba(0,0,0,0.75)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '11px 14px',
              borderBottom: '1px solid rgba(255,255,255,0.1)',
            }}
          >
            <span
              style={{
                fontSize: 12,
                fontWeight: 850,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: '#c7d2fe',
              }}
            >
              Party Chat · {party.member_count} live
            </span>
            <button
              type="button"
              onClick={() => setChatOpen(false)}
              style={{
                border: 'none',
                background: 'rgba(255,255,255,0.1)',
                color: '#fff',
                borderRadius: 999,
                width: 24,
                height: 24,
                cursor: 'pointer',
                fontSize: 12,
                fontWeight: 800,
              }}
            >
              ✕
            </button>
          </div>
          <div
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: '10px 14px',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            {(party.messages ?? [])
              .filter((m) => m.kind !== 'reaction')
              .slice(-40)
              .map((m) =>
                m.kind === 'system' ? (
                  <div
                    key={m.id}
                    style={{ fontSize: 11.5, color: 'rgba(255,255,255,0.52)', fontStyle: 'italic' }}
                  >
                    {m.profile?.name ?? 'Someone'} {m.body.toLowerCase()}
                  </div>
                ) : (
                  <div key={m.id} style={{ fontSize: 12.5, color: '#fff', lineHeight: 1.38 }}>
                    <strong style={{ color: '#a5b4fc', marginRight: 6 }}>
                      {m.profile?.name ?? 'Someone'}
                    </strong>
                    <span>{m.body}</span>
                  </div>
                ),
              )}
          </div>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const txt = chatInput.trim();
              if (!txt) return;
              setChatInput('');
              try {
                const res = await watchPartyApi.chat(party.id, 'chat', txt);
                if (res?.id) seenIdsRef.current.add(res.id);
                const fresh = await watchPartyApi.get(party.id);
                if (fresh) setParty(fresh);
              } catch {
                /* ignore */
              }
            }}
            style={{
              display: 'flex',
              gap: 6,
              padding: 10,
              borderTop: '1px solid rgba(255,255,255,0.1)',
            }}
          >
            <input
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="Message the room…"
              style={{
                flex: 1,
                borderRadius: 999,
                border: '1px solid rgba(255,255,255,0.16)',
                background: 'rgba(255,255,255,0.08)',
                color: '#fff',
                fontSize: 12.5,
                padding: '7px 12px',
                outline: 'none',
              }}
            />
            <button
              type="submit"
              style={{
                borderRadius: 999,
                border: 'none',
                background: '#6366f1',
                color: '#fff',
                fontSize: 12,
                fontWeight: 800,
                padding: '7px 14px',
                cursor: 'pointer',
              }}
            >
              Send
            </button>
          </form>
        </div>
      )}

      {/* 4e. Post-Credits Lounge Overlay when Movie Ends */}
      {videoEnded && party && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 40,
            display: 'grid',
            placeItems: 'center',
            background: 'rgba(6, 9, 16, 0.72)',
            backdropFilter: 'blur(12px)',
          }}
        >
          <div
            style={{
              padding: '28px 36px',
              borderRadius: 22,
              background: 'rgba(13, 17, 29, 0.94)',
              border: '1.5px solid rgba(255, 255, 255, 0.16)',
              boxShadow: '0 28px 80px rgba(0,0,0,0.8)',
              textAlign: 'center',
              maxWidth: 440,
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 850,
                letterSpacing: '0.2em',
                textTransform: 'uppercase',
                color: '#a5b4fc',
                marginBottom: 8,
              }}
            >
              ★ Credits Rolled ★
            </div>
            <div style={{ fontSize: 22, fontWeight: 900, color: '#fff', marginBottom: 6 }}>
              {party.title}
            </div>
            <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.72)', marginBottom: 20 }}>
              Hope everyone enjoyed the show! Head back to the theater lounge or leave the party.
            </div>
            <div style={{ display: 'flex', justifyContent: 'center', gap: 10, flexWrap: 'wrap' }}>
              <button
                type="button"
                onClick={() => {
                  const pid = party.id;
                  setActiveWatchPartyId(null);
                  getEngine().stop();
                  window.location.href = `/player/watching?party=${encodeURIComponent(pid)}`;
                }}
                style={{
                  padding: '9px 18px',
                  borderRadius: 999,
                  border: 'none',
                  background: '#fff',
                  color: '#0b0f19',
                  fontSize: 13,
                  fontWeight: 850,
                  cursor: 'pointer',
                }}
              >
                Return to Lounge
              </button>
              <button
                type="button"
                onClick={() => {
                  void watchPartyApi.leave(party.id).catch(() => undefined);
                  setActiveWatchPartyId(null);
                  getEngine().stop();
                }}
                style={{
                  padding: '9px 18px',
                  borderRadius: 999,
                  border: '1px solid rgba(244,63,94,0.45)',
                  background: 'rgba(244,63,94,0.18)',
                  color: '#fda4af',
                  fontSize: 13,
                  fontWeight: 800,
                  cursor: 'pointer',
                }}
              >
                Leave Party
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast alert for cooldowns */}
      {toastMsg && (
        <div
          style={{
            position: 'absolute',
            right: 24,
            bottom: 152,
            zIndex: 39,
            padding: '7px 14px',
            borderRadius: 999,
            background: 'rgba(15, 18, 28, 0.9)',
            border: '1px solid rgba(251, 191, 36, 0.45)',
            color: '#fde68a',
            fontSize: 12,
            fontWeight: 750,
            boxShadow: '0 10px 26px rgba(0,0,0,0.55)',
          }}
        >
          {toastMsg}
        </div>
      )}

      {/* 5. Sleek Frosted Watch Party Social & Control Dock inside the Cinema HUD */}
      {hudVisible && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'absolute',
            right: 24,
            bottom: 96,
            zIndex: 30,
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            padding: '6px 10px',
            borderRadius: 999,
            background: 'rgba(10, 13, 22, 0.82)',
            border: '1px solid rgba(255,255,255,0.16)',
            backdropFilter: 'blur(16px)',
            boxShadow: '0 12px 34px rgba(0,0,0,0.55)',
          }}
        >
          {/* Party Pause / 5s Countdown Resume button */}
          <button
            type="button"
            onClick={() => void handleTogglePartyPauseResume()}
            disabled={!!peeBreak && !canResumePeeBreak}
            title={
              party?.status === 'paused'
                ? 'Resume Watch Party (5-second countdown for everyone)'
                : 'Pause Watch Party for everyone'
            }
            style={{
              padding: '5px 11px',
              borderRadius: 999,
              border: '1px solid rgba(255,255,255,0.16)',
              background:
                party?.status === 'paused'
                  ? 'rgba(52, 211, 153, 0.22)'
                  : 'rgba(255, 255, 255, 0.08)',
              color: party?.status === 'paused' ? '#6ee7b7' : '#fff',
              fontSize: 11.5,
              fontWeight: 800,
              cursor: peeBreak && !canResumePeeBreak ? 'not-allowed' : 'pointer',
              opacity: peeBreak && !canResumePeeBreak ? 0.45 : 1,
              whiteSpace: 'nowrap',
            }}
          >
            {party?.status === 'paused' ? '▶ Resume (5s)' : '⏸ Pause All'}
          </button>

          {/* 🚽 5-Minute Pee Break Button (10m per-user cooldown) */}
          <button
            type="button"
            onClick={() => void handleTriggerPeeBreak()}
            disabled={peeCooldownSec > 0 || !!peeBreak}
            title={
              peeCooldownSec > 0
                ? `Pee break on cooldown (${Math.ceil(peeCooldownSec / 60)}m left — usable once every 10m)`
                : 'Call a 5-minute Pee Break (pauses stream for everyone until you return)'
            }
            style={{
              padding: '5px 11px',
              borderRadius: 999,
              border: '1px solid rgba(251, 191, 36, 0.35)',
              background:
                peeCooldownSec > 0 ? 'rgba(255,255,255,0.04)' : 'rgba(245, 158, 11, 0.18)',
              color: peeCooldownSec > 0 ? 'rgba(255,255,255,0.45)' : '#fde68a',
              fontSize: 11.5,
              fontWeight: 800,
              cursor: peeCooldownSec > 0 || !!peeBreak ? 'not-allowed' : 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            {peeCooldownSec > 0 ? `🚽 ${Math.ceil(peeCooldownSec / 60)}m` : '🚽 Pee Break'}
          </button>

          <div
            style={{
              width: 1,
              height: 18,
              background: 'rgba(255,255,255,0.14)',
              margin: '0 2px',
            }}
          />

          {/* 6 Quick Emoji Reaction Buttons (float up both sides with burst rate limit) */}
          {REACTION_EMOJIS.map((emoji) => (
            <button
              key={emoji}
              type="button"
              disabled={isRateLimited}
              onClick={() => void sendReaction(emoji)}
              title={isRateLimited ? 'Cooling down…' : `React ${emoji}`}
              style={{
                width: 34,
                height: 34,
                borderRadius: '50%',
                border: 'none',
                background: 'transparent',
                fontSize: 19,
                cursor: isRateLimited ? 'not-allowed' : 'pointer',
                opacity: isRateLimited ? 0.38 : 1,
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'transform 120ms ease, background 120ms ease',
              }}
              onMouseEnter={(e) => {
                if (isRateLimited) return;
                e.currentTarget.style.transform = 'scale(1.25)';
                e.currentTarget.style.background = 'rgba(255,255,255,0.12)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'scale(1)';
                e.currentTarget.style.background = 'transparent';
              }}
            >
              {emoji}
            </button>
          ))}

          <button
            type="button"
            onClick={() => {
              setChatOpen((o) => {
                if (!o) setUnreadChat(0);
                return !o;
              });
            }}
            title="Toggle Party Chat"
            style={{
              marginLeft: 2,
              padding: '4px 10px',
              borderRadius: 999,
              border: '1px solid rgba(165, 180, 252, 0.35)',
              background: chatOpen ? 'rgba(99, 102, 241, 0.35)' : 'rgba(255,255,255,0.08)',
              color: '#fff',
              fontSize: 10.5,
              fontWeight: 800,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            💬 Chat{unreadChat > 0 && !chatOpen ? ` (${unreadChat})` : ''}
          </button>

          <button
            type="button"
            onClick={() => setMuted((m) => !m)}
            title={muted ? 'Show floating reactions' : 'Hide floating reactions'}
            style={{
              marginLeft: 2,
              padding: '4px 9px',
              borderRadius: 999,
              border: '1px solid rgba(255,255,255,0.14)',
              background: muted ? 'rgba(244,63,94,0.22)' : 'rgba(255,255,255,0.08)',
              color: '#fff',
              fontSize: 10.5,
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            {muted ? 'Off' : 'Live'}
          </button>

          <button
            type="button"
            onClick={() => {
              if (partyId) {
                void watchPartyApi.leave(partyId).catch(() => undefined);
              }
              setActiveWatchPartyId(null);
            }}
            title="Leave Watch Party"
            style={{
              marginLeft: 2,
              padding: '4px 10px',
              borderRadius: 999,
              border: '1px solid rgba(244, 63, 94, 0.42)',
              background: 'rgba(244, 63, 94, 0.18)',
              color: '#fda4af',
              fontSize: 10.5,
              fontWeight: 800,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            Leave Party
          </button>
        </div>
      )}
    </>
  );
}
